#!/usr/bin/env python3
"""App Store Connect API: TestFlight build management.

Usage:
    asc-testflight.py status              # Check build processing status
    asc-testflight.py submit              # Set test notes + add to external group
    asc-testflight.py submit --notes-file FILE  # Use notes from file
"""
import sys, os, time, json, argparse
import jwt, requests
from datetime import datetime, timedelta, timezone

# --- Load config from env files ---
def _load_env_file(path):
    if not os.path.exists(path):
        return
    # Use errors='replace' so a single non-UTF-8 byte in any unrelated value
    # (e.g. an Unsplash access key copy-pasted with a smart quote) does not
    # crash the loader. We only consume ASC_* / HOMEKIT_* keys — other lines
    # tolerating a U+FFFD replacement char in their value is harmless.
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line:
                key, _, val = line.partition("=")
                if key.strip() not in os.environ:
                    os.environ[key.strip()] = val.strip()

# Project .env.local first, then global secrets as fallback
_script_dir = os.path.dirname(os.path.abspath(__file__))
_load_env_file(os.path.join(_script_dir, "..", ".env.local"))
_load_env_file(os.path.expanduser("~/.secrets.env"))

# --- Config (from ~/.secrets.env via chezmoi) ---
ISSUER_ID = os.environ.get("ASC_ISSUER_ID")
KEY_ID = os.environ.get("ASC_KEY_ID")
KEY_PATH = os.environ.get("ASC_KEY_PATH", os.path.expanduser("~/.private_keys/AuthKey_{}.p8".format(KEY_ID or "MISSING")))
BUNDLE_ID = "com.shahine.homeclaw"
BASE = "https://api.appstoreconnect.apple.com/v1"

def _check_config():
    missing = []
    if not ISSUER_ID: missing.append("ASC_ISSUER_ID")
    if not KEY_ID: missing.append("ASC_KEY_ID")
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}. Source ~/.secrets.env or set them.")
    if not os.path.exists(KEY_PATH):
        sys.exit(f"API key not found: {KEY_PATH}")


def make_token():
    with open(KEY_PATH, "r") as f:
        private_key = f.read()
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER_ID,
        "iat": now,
        "exp": now + timedelta(minutes=20),
        "aud": "appstoreconnect-v1",
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": KEY_ID})


def api(method, path, token, data=None):
    url = f"{BASE}{path}" if path.startswith("/") else path
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    r = requests.request(method, url, headers=headers, json=data)
    if r.status_code == 409:
        # Already exists (e.g., build already in group)
        return {"conflict": True, "status": r.status_code}
    r.raise_for_status()
    return r.json() if r.text else {}


def get_app_id(token):
    resp = api("GET", f"/apps?filter[bundleId]={BUNDLE_ID}", token)
    apps = resp.get("data", [])
    if not apps:
        sys.exit(f"No app found with bundle ID {BUNDLE_ID}")
    return apps[0]["id"]


def get_build(token, app_id, build_number=None):
    """Find a build by number, or get the latest."""
    path = f"/builds?filter[app]={app_id}&sort=-uploadedDate&limit=5"
    if build_number:
        path += f"&filter[version]={build_number}"
    resp = api("GET", path, token)
    builds = resp.get("data", [])
    if not builds:
        return None
    return builds[0]


def get_beta_groups(token, app_id):
    resp = api("GET", f"/betaGroups?filter[app]={app_id}", token)
    return resp.get("data", [])


def check_status(token, build_number=None):
    app_id = get_app_id(token)
    build = get_build(token, app_id, build_number)
    if not build:
        print(f"No build found{f' with number {build_number}' if build_number else ''}")
        return None

    attrs = build["attributes"]
    version = attrs.get("version", "?")
    state = attrs.get("processingState", "UNKNOWN")
    bri_state = attrs.get("buildAudienceType", "?")

    print(f"Build {version}")
    print(f"  Processing: {state}")
    print(f"  Uploaded:   {attrs.get('uploadedDate', '?')}")
    print(f"  Expired:    {attrs.get('expired', '?')}")

    # Check beta build localizations for test notes
    loc_link = build.get("relationships", {}).get("betaBuildLocalizations", {}).get("links", {}).get("related")
    if loc_link:
        loc_resp = api("GET", loc_link, token)
        locs = loc_resp.get("data", [])
        if locs:
            notes = locs[0]["attributes"].get("whatsNew") or "(none)"
            print(f"  Test notes: {notes[:80]}{'...' if len(notes) > 80 else ''}")
        else:
            print("  Test notes: (not set)")

    # Check external review status
    review_link = build.get("relationships", {}).get("buildBetaDetail", {}).get("links", {}).get("related")
    if review_link:
        detail = api("GET", review_link, token)
        detail_attrs = detail.get("data", {}).get("attributes", {})
        ext_status = detail_attrs.get("externalBuildState", "?")
        int_status = detail_attrs.get("internalBuildState", "?")
        print(f"  Internal:   {int_status}")
        print(f"  External:   {ext_status}")

    return build


def set_test_notes(token, build, notes):
    """Set 'What to Test' via betaBuildLocalizations."""
    loc_link = build["relationships"]["betaBuildLocalizations"]["links"]["related"]
    loc_resp = api("GET", loc_link, token)
    locs = loc_resp.get("data", [])

    if locs:
        # Update existing
        loc_id = locs[0]["id"]
        api("PATCH", f"/betaBuildLocalizations/{loc_id}", token, {
            "data": {
                "type": "betaBuildLocalizations",
                "id": loc_id,
                "attributes": {"whatsNew": notes},
            }
        })
        print(f"Updated test notes for build {build['attributes']['version']}")
    else:
        # Create new
        api("POST", "/betaBuildLocalizations", token, {
            "data": {
                "type": "betaBuildLocalizations",
                "attributes": {"locale": "en-US", "whatsNew": notes},
                "relationships": {
                    "build": {"data": {"type": "builds", "id": build["id"]}}
                },
            }
        })
        print(f"Created test notes for build {build['attributes']['version']}")


def add_to_external_group(token, app_id, build):
    """Add build to the first external beta group."""
    groups = get_beta_groups(token, app_id)
    external = [g for g in groups if not g["attributes"].get("isInternalGroup", True)]

    if not external:
        print("No external beta groups found. Create one in App Store Connect first.")
        return False

    group = external[0]
    group_name = group["attributes"]["name"]
    print(f"Adding build {build['attributes']['version']} to group '{group_name}'...")

    result = api("POST", f"/betaGroups/{group['id']}/relationships/builds", token, {
        "data": [{"type": "builds", "id": build["id"]}]
    })

    if isinstance(result, dict) and result.get("conflict"):
        print(f"Build already in group '{group_name}'")
    else:
        print(f"Build added to '{group_name}'")
    return True


def submit_for_review(token, build):
    """Submit build for Beta App Review."""
    result = api("POST", "/betaAppReviewSubmissions", token, {
        "data": {
            "type": "betaAppReviewSubmissions",
            "relationships": {
                "build": {"data": {"type": "builds", "id": build["id"]}}
            },
        }
    })

    if isinstance(result, dict) and result.get("conflict"):
        print(f"Build {build['attributes']['version']} already submitted for review")
    else:
        state = result.get("data", {}).get("attributes", {}).get("betaReviewState", "UNKNOWN")
        print(f"Build {build['attributes']['version']} submitted for Beta App Review ({state})")


def wait_for_processing(token, app_id, build_number, timeout=300):
    """Poll until build finishes processing."""
    start = time.time()
    while time.time() - start < timeout:
        build = get_build(token, app_id, build_number)
        if not build:
            print(f"Build {build_number} not found yet, waiting...")
            time.sleep(15)
            continue
        state = build["attributes"].get("processingState", "UNKNOWN")
        if state == "VALID":
            print(f"Build {build_number} is ready (VALID)")
            return build
        if state == "INVALID":
            sys.exit(f"Build {build_number} failed processing (INVALID)")
        print(f"Build {build_number} processing state: {state}, waiting...")
        time.sleep(15)
    sys.exit(f"Timed out waiting for build {build_number} to process ({timeout}s)")


def main():
    parser = argparse.ArgumentParser(description="App Store Connect TestFlight management")
    parser.add_argument("action", choices=["status", "submit"], help="Action to perform")
    parser.add_argument("--build", default=None, help="Build number (default: latest)")
    parser.add_argument("--notes-file", default=None, help="File containing test notes")
    parser.add_argument("--notes", default=None, help="Test notes string")
    parser.add_argument("--wait", action="store_true", help="Wait for processing to complete")
    args = parser.parse_args()

    _check_config()
    token = make_token()
    app_id = get_app_id(token)

    if args.action == "status":
        build = check_status(token, args.build)
        if not build:
            sys.exit(1)

    elif args.action == "submit":
        # Get or wait for build
        if args.wait:
            if not args.build:
                sys.exit("--build is required with --wait")
            build = wait_for_processing(token, app_id, args.build)
        else:
            build = get_build(token, app_id, args.build)
            if not build:
                sys.exit(f"Build {args.build or 'latest'} not found")

        state = build["attributes"].get("processingState", "UNKNOWN")
        if state != "VALID":
            print(f"Build is still {state}. Use --wait to poll until ready.")
            sys.exit(1)

        # Set test notes
        notes = None
        if args.notes_file:
            with open(args.notes_file) as f:
                notes = f.read().strip()
        elif args.notes:
            notes = args.notes

        if notes:
            set_test_notes(token, build, notes)

        # Add to external group and submit for review
        add_to_external_group(token, app_id, build)
        submit_for_review(token, build)

        # Final status
        print()
        check_status(token, build["attributes"]["version"])


if __name__ == "__main__":
    main()
