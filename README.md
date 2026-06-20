# tileserver-noborder

Self-hosted, neutrally-rendered MapLibre tile server (vector **and** raster) for Ubuntu 24.04, with **zero dependency on tile.openstreetmap.jp** — not at runtime, not at rebuild time, not even at first-time setup. Its editorial stance renders disputed land borders identically to sub-national administrative boundaries, hides every over-water boundary line, and renders disputed islands (Northern Territories, Takeshima, Senkaku) with their geography but **no text labels**.

The full build & operations spec is in **[`tileserver-noborder.md`](./tileserver-noborder.md)** (in Japanese). This README only summarizes what the repo contains.

## Repository layout

```
deploy.env.example  Per-deployment config template — copy to deploy.env and edit.
deploy.env          Gitignored. Operator's actual values (USER_NAME, DOMAIN, etc.)
staging/            Gitignored. Output of scripts/render-configs.sh — install
                    commands in tileserver-noborder.md §9.x reference this tree.

scripts/    Executable Python + Bash tools: buffer_clip, patch_style,
            strip_island_labels, residual_label_ids, build_fonts, rebuild,
            render-configs, apply_sea_mask. Disputed-area regions are explicit
            coordinates in rebuild.sh — no OSM.jp fetch. fetch_osmjp.py + geojson/
            are legacy (the optional buffer_clip.py --inputs path).
geojson/    Legacy/optional. Tracked: README.md + LICENSE. *.geojson are gitignored
            and never distributed; only needed for the fetched-polygon --inputs path.
data/       Source-of-truth template for tileserver-gl config.json (default values).
etc/        Source-of-truth templates for systemd units, nginx site config,
            certbot deploy hook, sudoers entry. Mirrors deploy paths under /etc.
web/        Source-of-truth template for the demo HTML page.
```

Templated values inside the bundled files (`etc/`, `data/`, `web/`) — domain `tile.hogehoge.com`, login user `foobar`, build/serving paths under `/work/...` and `/home/foobar/...` — are placeholders that `scripts/render-configs.sh` substitutes with the operator's `deploy.env` values into `staging/` for installation. `USER_NAME` defaults to the current login user, so a same-user deployment needs no username edit.

## Pipeline

```
[yearly rebuild loop — no OSM.jp anywhere]
disputed-area coordinates (hardcoded in rebuild.sh)
   → buffer_clip.py --polygon/--bbox        → world_minus_islands.poly
                                             + islands_buffered.geojson
planet.osm.pbf  (planet.passportcontrol.net mirror, OSM.org fallback)
   → osmium extract -p (clip islands out) + residual removeid
   → osmium extract islands → strip_island_labels.py (drop name/ref/housenumber)
       → osmium merge                       → clipped.osm.pbf (islands de-labeled)
   → planetiler (--languages=en --transliterate=false)        → final.mbtiles
   → tileserver-gl (vector + raster, serve_rendered:true)
       + patch_style.py-ed Toner-en/Basic-en styles
   → nginx (TLS, proxy_cache, CORS) → Cloudflare → tile.hogehoge.com
```

The build never reaches OSM.jp. Style + sprite are pulled directly from the upstream `openmaptiles/maptiler-{basic,toner}-gl-style` GitHub repos (BSD 3-Clause + CC-BY 4.0); fonts from `google/fonts`. See `tileserver-noborder.md` §1.6 for the canonical pipeline and §11 for the yearly rebuild.

## Disputed-area handling

- **Borders** (§1.2): every boundary renders like a prefecture/state line (`admin_level` 2/3 merged into 4); country labels show only at z0–4; **all over-water boundary lines are hidden** by drawing the opaque water fill above the boundary layers (bridges/tunnels stay visible). The `maritime` attribute alone is insufficient, so this layer-order mask is the catch-all.
- **Islands** (§1.3 / §7.2): Northern Territories (`--polygon`), Takeshima and Senkaku (`--bbox`). Within these regions geometry is **kept** but all text-producing tags (`name`/`name:*`/route `ref`/`addr:housenumber`) are stripped, so rivers/terrain/roads render with no labels at all.

## Vector + raster

`serve_rendered: true` serves raster PNG tiles (`/styles/{id}/{z}/{x}/{y}.png`) and a raster TileJSON in addition to vector tiles and the GL style — so raster-only clients (e.g. the WordPress "Leaflet Map" plugin) can consume the map. Headless raster rendering uses tileserver-gl's `maplibre-gl-native` under Xvfb (§9.5/§9.6).

## Embedding the served map

Snippets for MapLibre GL JS, Leaflet (raster XYZ, VectorGrid, `maplibre-gl-leaflet`) and the WordPress Leaflet Map plugin — with the per-pattern attribution requirements — are in `tileserver-noborder.md` §14.

## License

**The repository is uniformly GPL-2.0** ([`LICENSE`](./LICENSE)). No per-directory carve-out applies to anything actually distributed.

The `geojson/` directory is legacy/optional and contains only `README.md` (fetch guide) and `LICENSE` (informational). The default build defines disputed-area regions from coordinates and touches none of it. If the optional `buffer_clip.py --inputs` path is used, the fetched `hoppo.geojson` / `takeshima.geojson` are gitignored and never distributed; those copies carry **CC-BY-SA 2.0 + ODbL** at the operator's site only — see [`geojson/LICENSE`](./geojson/LICENSE) and [`geojson/README.md`](./geojson/README.md).

For the **deployed system** built from this toolchain:
- **MBTiles / served tiles**: ODbL (OSM contributors) + CC-BY 4.0 (OpenMapTiles schema)
- **style.json + sprite**: BSD 3-Clause (code) + CC-BY 4.0 (design); per the upstream LICENSE.md examples, the user-visible map credit is `© MapTiler` + `© OpenStreetMap contributors` for Toner-en (an explicit grant in upstream LICENSE.md makes MapTiler the sole required style credit), and `© OpenMapTiles` + `© OpenStreetMap contributors` for Basic-en. No CC-BY-SA share-alike obligation. No Stamen Design credit required.
- **Fonts**: SIL Open Font License 1.1

**This project does not claim copyright on its style modifications.** `scripts/patch_style.py` releases its output under the same upstream licenses (BSD 3-Clause + CC-BY 4.0); no "tileserver-noborder" credit is added or required in the deployed map. See `tileserver-noborder.md` §13.6 for the policy statement. Full license breakdown in §13.
