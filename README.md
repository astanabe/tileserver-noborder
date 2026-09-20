# tileserver-noborder

Self-hosted, neutrally-rendered MapLibre tile server (vector **and** raster) for Ubuntu 24.04 / 26.04. Its editorial stance renders disputed land borders identically to sub-national administrative boundaries, hides every over-water boundary line, and renders disputed islands (Northern Territories, Takeshima, Senkaku) with their geography but **no text labels**.

The full build & operations spec is in **[`tileserver-noborder.md`](./tileserver-noborder.md)** (in Japanese). This README only summarizes what the repo contains.

## Repository layout

```
deploy.env.example  Per-deployment config template — copy to deploy.env and edit.
deploy.env          Gitignored. Operator's actual values (USER_NAME, DOMAIN, etc.)
staging/            Gitignored. Output of scripts/render-configs.sh — install
                    commands in tileserver-noborder.md §8.x reference this tree.

scripts/    Executable Python + Bash tools: buffer_clip, patch_style,
            strip_island_labels, residual_label_ids, build_fonts, rebuild,
            render-configs, apply_sea_mask. Disputed-area regions are lon/lat
            coordinates in rebuild.sh.
data/       Source-of-truth template for tileserver-gl config.json (default values).
etc/        Source-of-truth templates for systemd units, nginx site config,
            certbot deploy hook, sudoers entry. Mirrors deploy paths under /etc.
web/        Source-of-truth template for the demo HTML page.
```

Templated values inside the bundled files (`etc/`, `data/`, `web/`) — domain `tile.hogehoge.com`, login user `foobar`, build/serving paths under `/work/...` and `/home/foobar/...` — are placeholders that `scripts/render-configs.sh` substitutes with the operator's `deploy.env` values into `staging/` for installation. `USER_NAME` defaults to the current login user, so a same-user deployment needs no username edit.

## Pipeline

```
[yearly rebuild loop]
disputed-area coordinates (in rebuild.sh)
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

Style + sprite come from the upstream `openmaptiles/maptiler-{basic,toner}-gl-style` GitHub repos (BSD 3-Clause + CC-BY 4.0); fonts from `google/fonts`. See `tileserver-noborder.md` §1.6 for the canonical pipeline and §10 for the yearly rebuild.

## Disputed-area handling

- **Borders** (§1.2): every boundary renders like a prefecture/state line (`admin_level` 2/3 merged into 4); country labels show only at z0–4; **all over-water boundary lines are hidden** by drawing the opaque water fill above the boundary layers (bridges/tunnels stay visible). The `maritime` attribute alone is insufficient, so this layer-order mask is the catch-all.
- **Islands** (§1.3 / §6.2): Northern Territories (`--polygon`), Takeshima and Senkaku (`--bbox`). Within these regions geometry is **kept** but all text-producing tags (`name`/`name:*`/route `ref`/`addr:housenumber`) are stripped, so rivers/terrain/roads render with no labels at all.

## Vector + raster

`serve_rendered: true` serves raster PNG tiles (`/styles/{id}/{z}/{x}/{y}.png`) and a raster TileJSON in addition to vector tiles and the GL style — so raster-only clients (e.g. the WordPress "Leaflet Map" plugin) can consume the map. Headless raster rendering uses tileserver-gl's `maplibre-gl-native` under Xvfb (§8.5/§8.6).

## Embedding the served map

Snippets for MapLibre GL JS, Leaflet (raster XYZ, VectorGrid, `maplibre-gl-leaflet`) and the WordPress Leaflet Map plugin — with the per-pattern attribution requirements — are in `tileserver-noborder.md` §13.

## License

**The repository is uniformly GPL-2.0** ([`LICENSE`](./LICENSE)).

For the **deployed system** built from this toolchain:
- **MBTiles / served tiles**: ODbL (OSM contributors) + CC-BY 4.0 (OpenMapTiles schema)
- **style.json + sprite**: BSD 3-Clause (code) + CC-BY 4.0 (design); per the upstream LICENSE.md examples, the user-visible map credit is `© MapTiler` + `© OpenStreetMap contributors` for Toner-en (an explicit grant in upstream LICENSE.md makes MapTiler the sole required style credit), and `© OpenMapTiles` + `© OpenStreetMap contributors` for Basic-en. No Stamen Design credit required.
- **Fonts**: SIL Open Font License 1.1

**This project does not claim copyright on its style modifications.** `scripts/patch_style.py` releases its output under the same upstream licenses (BSD 3-Clause + CC-BY 4.0); no "tileserver-noborder" credit is added or required in the deployed map. See `tileserver-noborder.md` §12.4 for the policy statement. Full license breakdown in §12.
