#!/usr/bin/env bash
# Yearly Planetiler rebuild driver.
#
# Sources $REPO/deploy.env at startup for all deployment-specific paths
# (BUILD_ROOT, TILESERVER_DATA, USER_NAME, etc.). REPO is derived from
# this script's own location, so the script works regardless of where the
# repo is cloned. Privileged steps (systemctl restart, nginx cache purge)
# are gated by /etc/sudoers.d/tileserver-rebuild.
#
# See tileserver-noborder.md §11 for context.
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$REPO/deploy.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "       Copy deploy.env.example to deploy.env and edit your values." >&2
    exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

: "${USER_NAME:?USER_NAME must be set in $ENV_FILE}"
: "${BUILD_ROOT:=/work/${USER_NAME}/planetiler}"
: "${TILESERVER_HOME:=/home/${USER_NAME}/tileserver-gl}"
: "${TILESERVER_DATA:=${TILESERVER_HOME}/data}"

cd "$BUILD_ROOT"

# (1) Refresh the planet PBF.
#     Set SKIP_PLANET_DOWNLOAD=1 to rebuild on the EXISTING pbf/global.osm.pbf
#     (e.g. to re-apply a pipeline change without re-downloading ~80 GB).
if [[ "${SKIP_PLANET_DOWNLOAD:-0}" == 1 ]]; then
    echo "rebuild: SKIP_PLANET_DOWNLOAD=1 — reusing existing pbf/global.osm.pbf"
    if [[ ! -f pbf/global.osm.pbf ]]; then
        echo "ERROR: pbf/global.osm.pbf not found; cannot skip the download." >&2
        exit 1
    fi
else
    cd pbf
    # Primary: planet.passportcontrol.net (Japan-domestic OSM PBF mirror).
    # Fall back to the canonical planet.openstreetmap.org if the mirror is
    # unreachable. Override either via PLANET_URL / PLANET_URL_FALLBACK.
    : "${PLANET_URL:=https://planet.passportcontrol.net/pbf/planet-latest.osm.pbf}"
    : "${PLANET_URL_FALLBACK:=https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf}"
    wget -N "$PLANET_URL" -O global.osm.pbf \
      || wget -N "$PLANET_URL_FALLBACK" -O global.osm.pbf
    cd "$BUILD_ROOT"
fi

# (2) Regenerate the .poly clip mask from EXPLICIT coordinates (no external
#     GeoJSON — the build has no OSM.jp dependency). Disputed areas:
#       - Northern Territories: 8-point lon/lat polygon (covers Kunashir,
#         Iturup, Shikotan, Habomai; excludes mainland Hokkaido / Nemuro).
#       - Takeshima: lon/lat rectangle.
#       - Senkaku Islands: lon/lat rectangle (Chinese-derived en label, §1.3).
#     Source coords are lat,lon; passed here as lon,lat. All taken verbatim.
source "$REPO/venv/bin/activate"
"$REPO/scripts/buffer_clip.py" \
    --polygon "146.10,44.90;145.26,43.85;145.51,43.49;145.83,43.41;145.88,43.32;146.59,43.14;149.60,45.19;148.70,46.10" \
    --bbox 131.84,37.22,131.89,37.26 \
    --bbox 123.29,25.59,123.77,26.02 \
    --out   "$BUILD_ROOT/build/world_minus_islands.poly" \
    --debug "$BUILD_ROOT/build/islands_buffered.geojson"
deactivate

# (3) Clip
osmium extract --overwrite \
    -p build/world_minus_islands.poly --strategy=smart \
    -o pbf/clipped.osm.pbf pbf/global.osm.pbf

# (3.5) Remove label/POI-producing features that survived the clip via
#       relation completion (e.g. Habomai archipelago multipolygon pulled in
#       because sibling relations reference mainland Hokkaido features).
#       natural=coastline silhouette ways are deliberately NOT in the list.
#       See tileserver-noborder.md §7 / §7.1.
osmium extract --overwrite --strategy=simple \
    -p build/islands_buffered.geojson \
    -o /tmp/resid.osm.pbf pbf/clipped.osm.pbf
osmium cat /tmp/resid.osm.pbf -f opl -o /tmp/resid.opl --overwrite
"$REPO/scripts/residual_label_ids.py" --opl /tmp/resid.opl > /tmp/rm_ids.txt
if [[ -s /tmp/rm_ids.txt ]]; then
    osmium removeid --id-file=/tmp/rm_ids.txt \
        -o pbf/clipped_final.osm.pbf --overwrite pbf/clipped.osm.pbf
    mv pbf/clipped_final.osm.pbf pbf/clipped.osm.pbf
fi

# (3.6) Re-add the island-buffer geometry with all text stripped: islands keep
#       their rivers/terrain/roads/buildings but render no labels. Steps (3)/(3.5)
#       left `clipped` free of name-bearing island features, so this de-labeled
#       copy is the sole label source; any duplicate is nameless geometry (e.g.
#       coastline), so the merge is label-safe. See §1.3 / §7.2.
osmium extract --overwrite --strategy=smart \
    -p build/islands_buffered.geojson \
    -o pbf/islands.osm.pbf pbf/global.osm.pbf
osmium cat pbf/islands.osm.pbf -f opl -o - --overwrite \
  | "$REPO/scripts/strip_island_labels.py" \
  | osmium cat -F opl -f pbf -o pbf/islands_notext.osm.pbf --overwrite -
osmium merge --overwrite \
    pbf/clipped.osm.pbf pbf/islands_notext.osm.pbf \
    -o pbf/clipped_with_islands.osm.pbf
mv pbf/clipped_with_islands.osm.pbf pbf/clipped.osm.pbf

# (4) Planetiler (full-planet build)
# Heap fixed at 32 GiB - 32 MiB (just under the CompressedOops cutoff) so
# 32-bit object pointers stay enabled and remaining RAM is available as OS
# page cache for mmap-backed storage. Override via PLANETILER_XMX in
# deploy.env if the host has drastically different RAM (e.g. 100g on a
# 128+ GB box using --storage=ram). See tileserver-noborder.md §8.
: "${PLANETILER_XMX:=32736m}"
java -Xms"$PLANETILER_XMX" -Xmx"$PLANETILER_XMX" -jar src/planetiler.jar \
    --osm_path=pbf/clipped.osm.pbf \
    --download --force \
    --storage=mmap --nodemap-storage=mmap --nodemap-type=array \
    --languages=en \
    --transliterate=false \
    --output=mbtiles/final.new.mbtiles

# (5) Atomic swap (cross-filesystem safe)
#     /work and /home may live on different filesystems. rename(2) is only
#     atomic within a single FS, so stage the new file *on the serving FS*
#     first, then rename in place.
cp mbtiles/final.new.mbtiles "$TILESERVER_DATA/openmaptiles.mbtiles.new"
mv -f "$TILESERVER_DATA/openmaptiles.mbtiles.new" "$TILESERVER_DATA/openmaptiles.mbtiles"
rm -f mbtiles/final.new.mbtiles

# (6) Restart tileserver-gl to reopen the SQLite handle
sudo /usr/bin/systemctl restart tileserver-gl.service

# (7) Purge nginx tile cache so stale tiles aren't served
sudo /usr/bin/find /var/cache/nginx/tiles -type f -delete
sudo /usr/bin/systemctl reload nginx.service
