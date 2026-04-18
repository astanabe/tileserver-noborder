# tileserver-noborder

Self-hosted, neutrally-rendered MapLibre vector tile server for Ubuntu 24.04, with **zero runtime dependency on tile.openstreetmap.jp** and an editorial stance that renders disputed land borders identically to sub-national administrative boundaries.

The full build & operations spec is in **[`tileserver-noborder.md`](./tileserver-noborder.md)** (in Japanese). This README only summarizes what the repo contains.

## Repository layout

```
deploy.env.example  Per-deployment config template — copy to deploy.env and edit.
deploy.env          Gitignored. Operator's actual values (USER_NAME, DOMAIN, etc.)
staging/            Gitignored. Output of scripts/render-configs.sh — install
                    commands in tileserver-noborder.md §9.x reference this tree.

scripts/    Executable Python + Bash tools (fetch, buffer, patch, rebuild, mask,
            render-configs). scripts/fetch_osmjp.py is the only OSM.jp-touching
            tool — run once at first-time setup and rarely thereafter.
geojson/    Tracked: README.md (fetch guide) + LICENSE (informational).
            *.geojson are gitignored. Operators fetch hoppo.geojson /
            takeshima.geojson here at first-time setup via scripts/fetch_osmjp.py;
            the data files are never distributed via this repo.
data/       Source-of-truth template for tileserver-gl config.json (default values).
etc/        Source-of-truth templates for systemd units, nginx site config,
            certbot deploy hook, sudoers entry. Mirrors deploy paths under /etc.
web/        Source-of-truth template for the demo HTML page.
```

Templated values inside the bundled files (`etc/`, `data/`, `web/`) — domain `tile.hogehoge.com`, login user `shimotsuki`, build/serving paths under `/work/...` and `/home/shimotsuki/...` — are placeholders that `scripts/render-configs.sh` substitutes with the operator's `deploy.env` values into `staging/` for installation.

## Pipeline

```
[first-time setup, once]
OSM.jp hoppo/takeshima MVT (z=10)
   → scripts/fetch_osmjp.py
   → $REPO/geojson/{hoppo,takeshima}.geojson   (gitignored, not in repo)

[weekly rebuild loop]
$REPO/geojson/{hoppo,takeshima}.geojson
   → buffer_clip.py   → world_minus_islands.poly (geodesic 2 km buffer, AEQD)
planet.osm.pbf
   → osmium extract -p ...                     → clipped.osm.pbf
   → planetiler (--languages=en,ja,ko,ru
                 --transliterate=false)         → final.mbtiles
   → tileserver-gl + patch_style.py-ed Toner-en/Basic-en styles
   → nginx (TLS, proxy_cache, CORS) on tile.hogehoge.com
```

OSM.jp is touched only by `scripts/fetch_osmjp.py`, run once at first-time setup and again only if upstream coastlines need refreshing (year-scale cadence at most). The weekly rebuild does not reach OSM.jp. Style + sprite are pulled directly from the upstream `openmaptiles/maptiler-{basic,toner}-gl-style` GitHub repos (BSD 3-Clause + CC-BY 4.0).

See `tileserver-noborder.md` §1.6 for the canonical version, §1.2/§1.3 for the boundary-neutralization rationale, and §11 for the weekly rebuild loop.

## Embedding the served map

Snippets for embedding in MapLibre GL JS or Leaflet (3 patterns: raster / VectorGrid / maplibre-gl-leaflet) — including the per-pattern attribution requirements — are in `tileserver-noborder.md` §14.

## License

**The repository is uniformly GPL-2.0** ([`LICENSE`](./LICENSE)). No per-directory carve-out applies to anything actually distributed.

The `geojson/` directory contains only `README.md` (fetch guide) and `LICENSE` (informational, describing the license that applies to data fetched per the guide). The actual data files (`hoppo.geojson`, `takeshima.geojson`) are gitignored and never distributed via this repo. Operators fetch them at first-time setup; the fetched copies carry **CC-BY-SA 2.0 + ODbL** at that operator's site only — see [`geojson/LICENSE`](./geojson/LICENSE) and [`geojson/README.md`](./geojson/README.md).

For the **deployed system** built from this toolchain:
- **MBTiles / served tiles**: ODbL (OSM contributors) + CC-BY 4.0 (OpenMapTiles schema)
- **style.json + sprite**: BSD 3-Clause (code) + CC-BY 4.0 (design); per the upstream LICENSE.md examples, the user-visible map credit is `© MapTiler` + `© OpenStreetMap contributors` for Toner-en (an explicit grant in upstream LICENSE.md makes MapTiler the sole required style credit), and `© OpenMapTiles` + `© OpenStreetMap contributors` for Basic-en. No CC-BY-SA share-alike obligation. No Stamen Design credit required.
- **Fonts**: SIL Open Font License 1.1

**This project does not claim copyright on its style modifications.** `scripts/patch_style.py` releases its output under the same upstream licenses (BSD 3-Clause + CC-BY 4.0); no "tileserver-noborder" credit is added or required in the deployed map. The credit text matches upstream LICENSE.md examples verbatim. See `tileserver-noborder.md` §13.6 for the policy statement.

The CC-BY-SA 2.0 obligation on the operator's fetched `geojson/*.geojson` does *not* propagate into the served MBTiles — the polygons are used only as a build-time clip mask, not embedded as content. Full breakdown in `tileserver-noborder.md` §13. Embedding examples (Leaflet / MapLibre GL JS) with the per-style attribution snippets are in §14.
