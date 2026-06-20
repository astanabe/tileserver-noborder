#!/usr/bin/env bash
# Build the eight PBF font stacks required by the patched Maptiler Basic / Toner
# styles (see tileserver-noborder.md §9.2):
#
#   - Noto Sans Regular / Bold / Italic / Bold Italic
#   - Nunito Regular / Bold / Semi Bold / Extra Bold
#
# All four Nunito weights are required: Toner references Nunito Regular/Bold in
# text-font stops, and server-side raster rendering (serve_rendered: true)
# 500s a tile if any referenced stack is missing.
#
# The patched styles render only "{name:latin}", so no glyphs beyond Latin +
# Greek + Cyrillic are needed. We pull three variable TTFs from google/fonts,
# instance the exact weights with fontTools.varLib.instancer, and feed the
# eight static TTFs to openmaptiles/fonts' generate.js to emit PBF stacks.
#
# Upstream sources:
#   - google/fonts/ofl/notosans/NotoSans[wdth,wght].ttf        (Regular, Bold)
#   - google/fonts/ofl/notosans/NotoSans-Italic[wdth,wght].ttf (Italic, Bold Italic)
#   - google/fonts/ofl/nunito/Nunito[wght].ttf                 (Regular, Bold, Semi Bold, Extra Bold)
#
# Build tool:
#   - openmaptiles/fonts/generate.js (only this file is taken from OMT)
#
# Prerequisites:
#   - Node.js + npm (installed in §3 for tileserver-gl)
#   - $REPO/venv (the Python venv created in §3, for fontTools)
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
# Resolve the font install dir from deploy.env (TILESERVER_DATA) when present,
# else fall back to a per-user default. No hardcoded username.
# shellcheck disable=SC1091
[ -f "$REPO/deploy.env" ] && . "$REPO/deploy.env"
OUTPUT_DIR="${OUTPUT_DIR:-${TILESERVER_DATA:-$HOME/tileserver-gl/data}/fonts}"
WORK="${WORK:-/tmp/tileserver-fonts-build}"
OMT_FONTS_REF="${OMT_FONTS_REF:-master}"   # commit / tag in openmaptiles/fonts
GF_RAW="https://raw.githubusercontent.com/google/fonts/main/ofl"

echo "[build_fonts] work dir: $WORK"
rm -rf "$WORK"
mkdir -p "$WORK"/{noto-sans,nunito}
cd "$WORK"

# -----------------------------------------------------------------------------
# (1) generate.js + its npm deps (fontnik, glyph-pbf-composite).
#     Nothing else from openmaptiles/fonts.
# -----------------------------------------------------------------------------
echo "[build_fonts] fetching generate.js from openmaptiles/fonts@$OMT_FONTS_REF"
curl -fsSL -o generate.js \
    "https://raw.githubusercontent.com/openmaptiles/fonts/${OMT_FONTS_REF}/generate.js"
cat > package.json <<'EOF'
{
  "name": "tileserver-noborder-font-build",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "@mapbox/glyph-pbf-composite": "0.0.3",
    "fontnik": "0.7.2"
  }
}
EOF
echo "[build_fonts] npm install (fontnik + glyph-pbf-composite)"
env -u VIRTUAL_ENV PATH=/usr/bin:/bin:/usr/local/bin npm install --silent

# -----------------------------------------------------------------------------
# (2) Fetch the three upstream variable fonts from google/fonts.
#     URL-encoded square brackets ('%5B' / '%5D') are required.
# -----------------------------------------------------------------------------
echo "[build_fonts] downloading variable fonts from google/fonts"
curl -fsSL -o noto-sans/_NotoSans-Variable.ttf \
    "${GF_RAW}/notosans/NotoSans%5Bwdth,wght%5D.ttf"
curl -fsSL -o noto-sans/_NotoSans-Italic-Variable.ttf \
    "${GF_RAW}/notosans/NotoSans-Italic%5Bwdth,wght%5D.ttf"
curl -fsSL -o nunito/_Nunito-Variable.ttf \
    "${GF_RAW}/nunito/Nunito%5Bwght%5D.ttf"

# -----------------------------------------------------------------------------
# (3) Instance each variable font at the specific (weight, width) we need.
#     fontTools.varLib.instancer drops the variation axes and produces a
#     plain static TTF containing only the target instance's glyph outlines.
#
#     File names are chosen so generate.js' automatic CamelCase splitter
#     ("NotoSans-BoldItalic.ttf" -> "Noto Sans Bold Italic") picks the
#     correct output stack name with no fonts.json needed.
# -----------------------------------------------------------------------------
# shellcheck disable=SC1091
source "$REPO/venv/bin/activate"
python3 -c "import fontTools" 2>/dev/null || pip install --quiet fonttools

echo "[build_fonts] instancing 8 static TTFs"
fonttools varLib.instancer --quiet noto-sans/_NotoSans-Variable.ttf \
    wght=400 wdth=100 -o noto-sans/NotoSans-Regular.ttf
fonttools varLib.instancer --quiet noto-sans/_NotoSans-Variable.ttf \
    wght=700 wdth=100 -o noto-sans/NotoSans-Bold.ttf
fonttools varLib.instancer --quiet noto-sans/_NotoSans-Italic-Variable.ttf \
    wght=400 wdth=100 -o noto-sans/NotoSans-Italic.ttf
fonttools varLib.instancer --quiet noto-sans/_NotoSans-Italic-Variable.ttf \
    wght=700 wdth=100 -o noto-sans/NotoSans-BoldItalic.ttf
fonttools varLib.instancer --quiet nunito/_Nunito-Variable.ttf \
    wght=400 -o nunito/Nunito-Regular.ttf
fonttools varLib.instancer --quiet nunito/_Nunito-Variable.ttf \
    wght=700 -o nunito/Nunito-Bold.ttf
fonttools varLib.instancer --quiet nunito/_Nunito-Variable.ttf \
    wght=600 -o nunito/Nunito-SemiBold.ttf
fonttools varLib.instancer --quiet nunito/_Nunito-Variable.ttf \
    wght=800 -o nunito/Nunito-ExtraBold.ttf

rm noto-sans/_NotoSans-Variable.ttf
rm noto-sans/_NotoSans-Italic-Variable.ttf
rm nunito/_Nunito-Variable.ttf
deactivate

# -----------------------------------------------------------------------------
# (4) Run generate.js. With no fonts.json in either subdir, it auto-detects
#     one output stack per TTF, named by the CamelCase rule above.
# -----------------------------------------------------------------------------
echo "[build_fonts] running generate.js (this takes ~1-2 min)"
env -u VIRTUAL_ENV PATH=/usr/bin:/bin:/usr/local/bin node generate.js 2>&1 | tail -5

# -----------------------------------------------------------------------------
# (5) Install the eight stacks (overwriting any existing).
# -----------------------------------------------------------------------------
echo "[build_fonts] installing to $OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
for F in "Noto Sans Regular"   "Noto Sans Bold" \
         "Noto Sans Italic"    "Noto Sans Bold Italic" \
         "Nunito Regular"      "Nunito Bold" \
         "Nunito Semi Bold"    "Nunito Extra Bold"; do
    rm -rf "$OUTPUT_DIR/$F"
    cp -r "_output/$F" "$OUTPUT_DIR/"
done

echo "[build_fonts] done. Installed:"
ls "$OUTPUT_DIR"
