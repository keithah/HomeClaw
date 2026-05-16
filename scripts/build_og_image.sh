#!/usr/bin/env bash
# Generate the OpenGraph preview card (1200x630) for homeclaw.omarknows.app.
# Layout: gradient backdrop + HomeClaw icon (left) + title/tagline/App Store
# badge (right).
#
# Output: site/public/og-image.png

set -euo pipefail

ICON_SRC="${ICON_SRC:-docs/images/homeclaw-icon.png}"
OUT="${OUT:-site/public/og-image.png}"

# Canvas + colors (same gradient as the App Store screenshots).
W=1200
H=630
GRADIENT_TOP="#2c3e50"
GRADIENT_BOT="#4ca1af"

# Layout (left column = icon, right column = text).
ICON_SIZE=280
ICON_X=110
ICON_Y=175       # vertical offset so icon centers

TEXT_X=460       # left edge of right column
TITLE_Y=170      # baseline-ish
TAG_Y=280
BADGE_Y=400

TITLE_FONT="/System/Library/Fonts/SFNS.ttf"
MONO_FONT="/System/Library/Fonts/SFNSMono.ttf"

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
require_cmd magick

[[ -f "$ICON_SRC" ]] || { echo "Icon not found at $ICON_SRC" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
TMP_DIR=$(mktemp -d -t homeclaw_og.XXXXXX)
cleanup() { trash "$TMP_DIR" 2>/dev/null || true; }
trap cleanup EXIT

# 1. Build the gradient backdrop.
BG="$TMP_DIR/bg.png"
magick -size "${W}x${H}" "gradient:${GRADIENT_TOP}-${GRADIENT_BOT}" "$BG"

# 2. Resize the icon, add a soft drop shadow.
ICON="$TMP_DIR/icon.png"
magick "$ICON_SRC" -resize "${ICON_SIZE}x${ICON_SIZE}" "$ICON"

ICON_SHADOW="$TMP_DIR/icon_shadow.png"
magick "$ICON" \
  \( +clone -background black -shadow 70x18+0+12 \) \
  +swap -background none -layers merge +repage \
  "$ICON_SHADOW"

# 3. Composite icon onto backdrop.
WITH_ICON="$TMP_DIR/with_icon.png"
magick "$BG" "$ICON_SHADOW" -geometry "+${ICON_X}+${ICON_Y}" -composite "$WITH_ICON"

# 4. Render text + badge with ImageMagick's text APIs.
magick "$WITH_ICON" \
  -fill white -font "$TITLE_FONT" -pointsize 100 \
  -annotate "+${TEXT_X}+${TITLE_Y}" "HomeClaw" \
  -fill "#cfe5e8" -font "$MONO_FONT" -pointsize 34 \
  -annotate "+${TEXT_X}+${TAG_Y}" "CLI, MCP, and AI for HomeKit" \
  -fill "#0d4f5c" \
  -draw "roundrectangle ${TEXT_X},$((BADGE_Y - 38)) $((TEXT_X + 470)),$((BADGE_Y + 22)) 28,28" \
  -fill "#cff8ff" -font "$TITLE_FONT" -pointsize 28 \
  -annotate "+$((TEXT_X + 30))+${BADGE_Y}" "On the Mac App Store ↗" \
  "$OUT"

echo "Wrote $OUT"
magick identify "$OUT"
