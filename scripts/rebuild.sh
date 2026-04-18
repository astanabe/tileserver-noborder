#!/usr/bin/env bash
# Weekly Planetiler rebuild driver.
#
# Runs as user `shimotsuki` (set in the systemd unit). Privileged steps
# (systemctl restart, nginx cache purge) are gated by an entry in
# /etc/sudoers.d/tileserver-rebuild.
#
# See tileserver-noborder.md §11 for context.
set -euo pipefail

REPO="${REPO:-/home/shimotsuki/tileserver-noborder}"
TS=/work/shimotsuki/planetiler
cd "$TS"

# (1) Refresh the planet PBF
cd pbf
wget -N https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf -O global.osm.pbf
cd "$TS"

# (2) Regenerate the .poly clip mask from operator-fetched island GeoJSON.
#     $REPO/geojson/*.geojson is gitignored (the repo does not distribute it).
#     The operator fetches it once at first-time setup via
#     scripts/fetch_osmjp.py — see geojson/README.md.
#     Regular rebuilds only consume it; they never re-fetch.
for f in "$REPO/geojson/hoppo.geojson" "$REPO/geojson/takeshima.geojson"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: required GeoJSON not found: $f" >&2
        echo "       Run scripts/fetch_osmjp.py to fetch it." >&2
        echo "       See geojson/README.md for instructions." >&2
        exit 1
    fi
done
source venv/bin/activate
"$REPO/scripts/buffer_clip.py" \
    --inputs "$REPO/geojson/hoppo.geojson" "$REPO/geojson/takeshima.geojson" \
    --buffer-m 2000
deactivate

# (3) Clip
osmium extract --overwrite \
    -p build/world_minus_islands.poly --strategy=smart \
    -o pbf/clipped.osm.pbf pbf/global.osm.pbf

# (4) Planetiler (full-planet build)
java -Xmx100g -jar src/planetiler.jar \
    --osm_path=pbf/clipped.osm.pbf \
    --download --force \
    --storage=mmap --nodemap-storage=mmap --nodemap-type=array \
    --languages=en,ja,ko,ru \
    --transliterate=false \
    --output=mbtiles/final.new.mbtiles

# (5) Atomic swap (cross-filesystem safe)
#     /work and /home may live on different filesystems. rename(2) is only
#     atomic within a single FS, so stage the new file *on the serving FS*
#     first, then rename in place.
DATA=/home/shimotsuki/tileserver-gl/data
cp mbtiles/final.new.mbtiles $DATA/openmaptiles.mbtiles.new
mv -f $DATA/openmaptiles.mbtiles.new $DATA/openmaptiles.mbtiles
rm -f mbtiles/final.new.mbtiles

# (6) Restart tileserver-gl to reopen the SQLite handle
sudo /usr/bin/systemctl restart tileserver-gl.service

# (7) Purge nginx tile cache so stale tiles aren't served
sudo /usr/bin/find /var/cache/nginx/tiles -type f -delete
sudo /usr/bin/systemctl reload nginx.service
