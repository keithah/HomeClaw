---
description: Archive, upload, and submit to external TestFlight (full loop with monitor)
allowed-tools: Bash(scripts/*), Bash(python3 *), Bash(git *), Bash(source *), Bash(cat *), Bash(echo *), Bash(tail *), Bash(grep *), Bash(open *), Bash(defaults read *), Read, Write, Monitor
---

Run the **full TestFlight loop** for HomeClaw: generate notes, archive, upload to App Store Connect, submit to the External Testers group, tag the release, and confirm processing. The pipeline is long (8–20 min) so it MUST be run via Monitor so progress events stream into the conversation as they happen.

## Pre-flight

1. Verify the working tree is clean and on `main`:
   ```bash
   git status
   git rev-parse --abbrev-ref HEAD
   ```
   Abort if dirty or on a feature branch — release builds should ship from `main`.

2. Check what's shipping. Generate release notes from commits since the last release tag (release tags are `v{version}+{build}`; the script auto-strips the `+build` suffix when re-archiving so a stale tag won't break it):
   ```bash
   LAST_TAG=$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null)
   git log --oneline "$LAST_TAG..HEAD"
   ```

3. Draft tester notes to a file (NOT inline `--notes` — multiline + bullet points read better, and the file persists if you need to retry the submit step). Keep it user-facing: what features they get, what bugs were fixed. Avoid commit-hash speak.
   ```bash
   # Write notes to /tmp/homeclaw_testflight_notes.txt
   ```

## Run the pipeline (with Monitor)

The archive script does 5 steps: project gen → MCP build → archive → export+upload → submit. It runs `set -e`-style, so a hard failure at any step kills the run. Use `run_in_background` so the Monitor can stream from its log file while you keep working.

```bash
scripts/archive.sh --testflight --notes-file /tmp/homeclaw_testflight_notes.txt 2>&1 | tee /tmp/homeclaw_archive.log
```
Run with `run_in_background: true`. **Then arm a Monitor** to surface milestones:

```bash
tail -f /tmp/homeclaw_archive.log | grep -E --line-buffered "✓|Done!|Error|error|fail|FAIL|Submitting|Uploading|Build [0-9]+|TestFlight|Version:|warning|Traceback|EXPORT SUCCEEDED|Upload succeeded"
```

The grep alternation MUST cover failure signatures (`Error|fail|Traceback`) — silence is not success. Each ✓ is one of the 5 pipeline steps. Expected event timeline:

| Time | Event |
|---|---|
| 0s | xcodegen + MCP build (2× ✓) |
| ~2 min | Archive succeeds, version banner `1.0.0 (NNN)` + bundle checks |
| ~3–5 min | `Uploading to App Store Connect…` then `Upload succeeded` |
| ~6–15 min | `Build NNN not found yet, waiting...` → `Build NNN is ready (VALID)` → `Submitted for Beta App Review` |

## After the pipeline

When the background task completes (exit 0), read the tail of the log to capture the build number, then:

1. **Verify final status** — confirms processing/external status:
   ```bash
   python3 scripts/asc-testflight.py status
   ```
   Expected: `Processing: VALID`, `External: IN_BETA_TESTING` (or `WAITING_FOR_BETA_REVIEW` if Apple hasn't approved yet — usually clears within an hour).

2. **Commit the bumped Info.plist + any script fixes** (the archive incremented `CFBundleVersion`):
   ```bash
   git add Resources/Info.plist scripts/archive.sh scripts/asc-testflight.py
   git commit -m "chore(release): build NNN"
   git push
   ```

3. **Tag the release** as `v{version}+{build}`:
   ```bash
   git tag -a v1.0.0+NNN -m "Release v1.0.0 build NNN" -m "<short summary>"
   git push origin v1.0.0+NNN
   ```

4. **Final report**: version, build number, External status, tag URL, App Store Connect URL (`https://appstoreconnect.apple.com/apps/6759682551/testflight`).

5. Per global memory, generate a TestFlight tester update message (see `memory/testflight-updates.md`) and offer to send it.

## If a step fails

| Failure | Recovery |
|---|---|
| Archive fails (steps 1–3) | Read the log, fix the build issue, re-run the whole pipeline. Build number does NOT increment on failure. |
| Upload fails (step 4) | The .xcarchive at `.build/archives/HomeClaw.xcarchive` is reusable. Run only the export/upload manually with `xcodebuild -exportArchive`, then proceed to the submit step. |
| Submit fails (step 5) but Upload succeeded | The build is already in App Store Connect. **Re-run only the submit standalone** without re-archiving: `python3 scripts/asc-testflight.py submit --build NNN --wait --notes-file /tmp/homeclaw_testflight_notes.txt`. This is the path we hit on build 142 when a non-UTF-8 byte in `~/.secrets-macbook-pro.env` crashed the env loader. |
| Apple rejects `MARKETING_VERSION` as invalid | The git tag has a `+build` suffix that wasn't stripped. Confirm `archive.sh` line ~30 runs `MARKETING_VERSION="${MARKETING_VERSION%+*}"`. |

## Common gotchas
- **Don't tail the log via Bash and wait** — that blocks the conversation. Always use Monitor with a filtering grep so events arrive incrementally.
- **`source ~/.secrets.env` is not required** — `archive.sh` and `asc-testflight.py` both load the env files themselves (`.env.local` first, then `~/.secrets.env`).
- **Tester notes preview** — App Store Connect truncates after ~4000 chars; keep the doc lean.
- **Never re-trigger the upload after failure-then-success** — duplicate Build NNN uploads will be rejected by Apple. Always check `asc-testflight.py status` first.
