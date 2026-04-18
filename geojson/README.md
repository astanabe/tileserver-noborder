# geojson/ — fetch guide & provenance

This directory holds island polygon GeoJSON files used by `scripts/buffer_clip.py` to build the `world_minus_islands.poly` clip mask. The repository **does not distribute these files** (they are listed in `.gitignore`); each operator fetches them once at first-time setup directly from OpenStreetMap Japan.

## Why not bundled

- Keeps the repository uniformly under GPL-2.0 (no CC-BY-SA 2.0 / ODbL carve-out inside the distributed tree).
- Keeps the project's "zero OSM.jp dependency" claim precise: OSM.jp is touched only when the operator chooses to fetch, not by virtue of cloning this repo.
- Makes data freshness an explicit operator decision (date-stamped by the operator's own fetch).

## Files (after fetch)

| File | Source layer |
|---|---|
| `hoppo.geojson` | `island` layer of `https://tile.openstreetmap.jp/data/hoppo.json` (z=10) |
| `takeshima.geojson` | `island` layer of `https://tile.openstreetmap.jp/data/takeshima.json` (z=10) |

`scripts/fetch_osmjp.py` is the canonical tool. Its output strips all attributes (`properties` is `{}` on every feature) and keeps geometry only — z=10 resolution (~150 m) is sufficient because the 2 km buffer in `buffer_clip.py` absorbs any sub-grid imprecision.

## Fetch procedure (first-time setup)

```bash
# In a Python venv with: requests mercantile mapbox-vector-tile shapely pyproj
"$REPO/scripts/fetch_osmjp.py" \
    --tilejson https://tile.openstreetmap.jp/data/hoppo.json \
    --layer island --zoom 10 --out "$REPO/geojson/hoppo.geojson"

"$REPO/scripts/fetch_osmjp.py" \
    --tilejson https://tile.openstreetmap.jp/data/takeshima.json \
    --layer island --zoom 10 --out "$REPO/geojson/takeshima.geojson"
```

The fetched files are gitignored, so they will not appear in `git status`. Re-run the same commands if you need to refresh from upstream (year-scale frequency at most — see "Refresh cadence" below).

## License of the fetched data

The fetched GeoJSON files are derivatives of OpenStreetMap Japan vector tiles. They carry **two simultaneous obligations**, even though they are not redistributed via this repository:

1. **CC-BY-SA 2.0** — per OSMFJ's site-wide license declaration (<https://www.openstreetmap.jp/terms_and_privacy>): "地図 (画像・タイル) のデータは、クリエーティブコモンズライセンス CC-BY-SA 2.0 で提供されます".
2. **ODbL 1.0** — the underlying geographic data originates from OpenStreetMap (© OpenStreetMap contributors).

If the operator chooses to redistribute the fetched files (e.g. mirror them to another repository, ship them as part of a distribution image, etc.), both licenses apply. See `geojson/LICENSE` for the formal terms.

## Scope: where these obligations propagate

CC-BY-SA 2.0 / ODbL share-alike applies to **the fetched GeoJSON files** and to direct derivatives that copy their geometry (`world_minus_islands.poly`, `islands_buffered.geojson` — build-time intermediates).

It does **not** propagate into:

- `clipped.osm.pbf` (`osmium extract -p` produces a subset of `planet.osm.pbf`; the polygons are used as a clip mask, not embedded)
- `final.mbtiles` and tiles served from it (Planetiler output contains only OSM data, not OSM.jp polygons)

The deployed map's user-facing attribution does not need to credit OSMFJ. See `tileserver-noborder.md` §13 for the full attribution map.

## Refresh cadence

Disputed-island geometries change at year-scale at most, and any drift well under 2 km is absorbed by the geodesic buffer in `buffer_clip.py`. Refresh only when there is a confirmed upstream coastline correction. The weekly rebuild (`scripts/rebuild.sh`) does NOT re-fetch; it consumes whatever is in this directory.
