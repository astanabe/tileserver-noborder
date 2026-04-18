# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **重要 / IMPORTANT — Language policy:**
> - **Conversational prompts with the user: Japanese.** All replies, questions, confirmations, progress updates, and error explanations directed at the user must be written in Japanese.
> - **Files in this repository: English.** `CLAUDE.md`, `README.md`, and all source code comments (including comments inside scripts embedded in `tileserver-noborder.md`) must be written in English.
> - **Exception: `tileserver-noborder.md` prose remains Japanese** — it is the existing user-facing spec (see "Editing conventions" below). Only its embedded code/comments are English.

> **重要 / IMPORTANT — sudo policy:**
> - **Never invoke `sudo` from the Bash tool.** Anything that needs root (install/cp/chown to `/etc`, `systemctl`, `nginx -t`, `certbot`, `visudo`, etc.) must be presented to the user as a fenced bash block — label it clearly ("以下を実行して下さい") and pause until the user reports completion (or shares output).
> - Non-sudo work proceeds normally: file edits in the repo, scripts in user-writable dirs, venv setup under `/work/shimotsuki/...` or `$HOME`, git operations.
> - Reason: the user wants direct control over privileged operations on this host (their personal Ubuntu machine that hosts the actual tile server). Auto-executing sudo would bypass that control, and a mistake under sudo can be hard to undo.

## Repository nature

This repo holds a deployable toolset (Python + Bash scripts plus systemd / nginx / sudoers / tileserver-gl configs) and one canonical document, `tileserver-noborder.md`, that walks through using them to stand up a self-hosted MapLibre vector tile server on Ubuntu 24.04.

The doc references the bundled artifacts by repo path; it no longer embeds them. Tools are run from `$REPO/scripts/`; configs are installed to their target paths under `/etc`, `/home/shimotsuki/tileserver-gl/data`, etc., via `install`/`cp`. There is still no traditional build system, test suite, package manifest, or CI.

```
$REPO/
├── deploy.env.example  Per-deployment config template (USER_NAME, DOMAIN, BUILD_ROOT, ...)
├── deploy.env          gitignored, operator-edited copy of the above
├── staging/            gitignored, output of scripts/render-configs.sh
│                       (rendered etc/, data/, web/ with deploy.env values applied)
├── scripts/        Executable tools (Python + Bash). scripts/fetch_osmjp.py is the
│                   only OSM.jp-touching tool — run once at first-time setup, rarely after.
│                   scripts/render-configs.sh produces staging/ from etc/+data/+web/.
│                   scripts/rebuild.sh sources deploy.env at runtime (no rendering needed).
├── geojson/        Tracked: only README.md (fetch guide) + LICENSE (informational).
│                   Untracked: hoppo.geojson / takeshima.geojson — operators fetch these at
│                   first-time setup; they are gitignored, never distributed via the repo.
├── data/           Template for tileserver-gl config (rendered to staging/data/).
├── etc/            Template tree mirroring deploy paths (rendered to staging/etc/).
└── web/            Template for static demo HTML (rendered to staging/web/).
```

The deployed system itself (PBF, generated MBTiles, tileserver-gl npm install, certificates, nginx caches) lives at external paths (`/work/shimotsuki/planetiler`, `/home/shimotsuki/tileserver-gl`, `/home/shimotsuki/http/tile.hogehoge.com`, `/etc/...`) — **not inside this repo**.

When the user asks to "run", "test", or "build" something, they probably mean executing parts of the spec on a target host (commonly the same Ubuntu host the repo is checked out on), not synthesizing anything new in the working directory.

## Editing conventions

- **Language inside `tileserver-noborder.md`: Japanese for prose, English for code.** Keep new narrative/explanatory text in Japanese to match the rest of the document. Code, identifiers, CLI flags, and **comments inside referenced scripts/configs** stay in English. (Other repo files — `CLAUDE.md`, `README.md`, and the contents of `scripts/`, `etc/`, `data/`, `web/` — are English-only; see the language policy at the top.)
- **Placeholders are deliberate.** `tile.hogehoge.com` (domain), `shimotsuki` (login user), and the `/work/...` / `/home/shimotsuki/...` paths appear throughout `etc/`, `data/`, `web/` — these are the **default values** that `scripts/render-configs.sh` substitutes via `deploy.env`. Edit `deploy.env` (gitignored) to change them, never the source files directly. Do not "fix" the source files to look more realistic.
- **`deploy.env` is the single source of per-deployment truth.** All deployment-specific paths (USER_NAME, DOMAIN, BUILD_ROOT, TILESERVER_HOME/DATA, HTTP_ROOT, REPO) live there. `scripts/rebuild.sh` sources it at runtime; `scripts/render-configs.sh` reads it to produce `staging/`. Adding a new deployment-specific path means: (1) add it to `deploy.env.example`, (2) add a substitution rule in `render-configs.sh`, (3) reference it from the template files via the default value. Don't introduce alternative config mechanisms.
- **Section numbering is load-bearing.** The doc cross-references sections heavily (e.g., "§1.2.4", "§9.3", "§10.2.1 (C)", "§12.1"). Renumbering or reordering sections requires updating every back-reference.
- **Keep tools and the doc in sync.** When editing a `scripts/*.py` interface (CLI flags, output names, defaults), update the matching argument table or invocation example in `tileserver-noborder.md`. Same for any `etc/*` config — the doc's "what's in this file" prose summary should still be true.
- **The `$REPO` env var is the contract.** The doc assumes `REPO=/home/shimotsuki/tileserver-noborder` (declared in §2.3). `scripts/rebuild.sh` and the `tileserver-rebuild.service` systemd unit also hardcode this default. If any of them change the convention, change all three together.
- **Idempotency matters in `scripts/`.** `patch_style.py`, `apply_sea_mask.py`, etc. are designed so that re-running them doesn't compound effects. New transforms should follow the same convention.
- The doc deliberately uses **prose ASCII tree/flow diagrams** (e.g., §1.5 deployment topology, §1.6 pipeline) rather than image embeds.

## The architectural idea (why this project exists)

Two design goals run through the entire spec and explain choices that otherwise look arbitrary. Understanding them is necessary before changing anything substantive.

### 1. Eliminate OSM.jp runtime dependency

The reference Japanese tile styles (Maptiler-Toner-en, Maptiler-Basic-en) normally fetch a `hoppo` and `takeshima` overlay tileset from `tile.openstreetmap.jp` at render time. This repo's whole point is to serve tiles with **zero runtime calls to OSM.jp** and **zero rebuild-time calls**:

- **Style + sprite**: pulled directly from upstream `openmaptiles/maptiler-{basic,toner}-gl-style` GitHub repos at pinned tags (BSD 3-Clause + CC-BY 4.0, share-alike-free), not from OSM.jp's customized fork.
- **Island polygons**: fetched at first-time setup by `scripts/fetch_osmjp.py` into `$REPO/geojson/`. The data files are **gitignored** (`.gitignore` excludes `geojson/*.geojson`) so the repo itself never carries OSM.jp-derived content. The weekly rebuild reads from this directory but never re-fetches.

OSM.jp is therefore touched **only** by `scripts/fetch_osmjp.py`: once at first-time setup, and again only on rare upstream coastline corrections (year-scale cadence). Nothing in `scripts/rebuild.sh` or the §9.1 style-setup steps reaches OSM.jp.

Practical consequence: any change that re-introduces a network source, an `<img>` overlay, a style `source` pointing at osm.jp, an OSM.jp call from `scripts/rebuild.sh`, an OSM.jp fetch outside `scripts/fetch_osmjp.py`, or commits the `geojson/*.geojson` data files to the repo is a regression.

### 2. Boundary/label neutralization for disputed territories

The spec takes a specific editorial stance on contested borders (Northern Territories, Takeshima, Kashmir, Thai-Cambodia, etc.). This is implemented across **multiple non-contiguous places**, and you must read all of them together to make safe changes:

- **§1.2.1–§1.2.4** — design rationale and processing order for boundary handling.
- **§1.3** — buffer-erase strategy: features within +2 km of disputed island polygons are deleted from the source PBF (`osmium extract -p`), so they never enter the rendered tiles at all. No labels, no roads, no buildings — only an unlabeled silhouette remains.
- **§9.3 `patch_style.py`** — the style-rewrite that (a) adds `["!=", "maritime", 1]` to every `boundary` layer, (b) deletes the dedicated country-border layers and merges `admin_level=2,3` into the state-border layer (`admin_sub` / `boundary_state`) so country borders render identically to prefecture/state borders, (c) caps `place.class=country` labels at `maxzoom=5`, and (d) strips `hoppo`/`takeshima` sources and the 5 layers that reference them.
- **§10.2.1** — the verification recipes that prove (A) country-only border layers are gone, (B) `admin_level=2,3,4` are merged, (C) `maritime` filter is present on the merged layer, (D) country labels are zoom-capped.
- **§12** — troubleshooting maps user-visible symptoms back to which of (A)/(B)/(C)/(D) is broken.

When changing any of those pieces, also update its counterpart in the other sections, or the verification commands and troubleshooting table will lie.

The `patch_style.py` helpers are designed to be **idempotent** (e.g., `add_filter_clause` deduplicates, `inject_admin_levels` merges into existing `["in", ...]` clauses). New transforms added to that script should follow the same convention so reruns of the patch don't compound.

## Pipeline at a glance (see §1.6 for the canonical version)

```
[first-time setup, once]
OSM.jp hoppo/takeshima MVT (z=10)
   → scripts/fetch_osmjp.py
   → $REPO/geojson/{hoppo,takeshima}.geojson  (gitignored, not in the repo distribution)

[weekly rebuild loop]
$REPO/geojson/{hoppo,takeshima}.geojson
   → buffer_clip.py  → world_minus_islands.poly (geodesic 2km buffer, AEQD)
planet.osm.pbf
   → osmium extract -p ...                    → clipped.osm.pbf
   → planetiler (--languages=en,ja,ko,ru
                 --transliterate=false)        → final.mbtiles
   → tileserver-gl + patched Toner-en/Basic-en styles
   → nginx (TLS, proxy_cache, CORS) on tile.hogehoge.com

# OSM.jp does not appear in the weekly loop. Refresh of geojson/ is a manual,
# year-scale operation via scripts/fetch_osmjp.py.
```

`--transliterate=false` is intentional and explained in §8: it disables ICU romanization so that names without `name:en` render as **empty** rather than as low-quality auto-transliterated Latin. Removing the flag would be a quality regression, not a cleanup.

## Verifying changes

There's no automated check. The lightweight things to do after editing:

- For doc edits: render the file (any Markdown viewer) and skim the table of contents — the headings drive the §-references, so a typo in a heading silently breaks cross-refs.
- For doc edits to a numbered section: grep the doc for old-numbered back-references (e.g., `§9.3`, `§10.2.1`) and update them.
- For `scripts/*.py` edits: at minimum run `python -c "import ast; ast.parse(open('scripts/foo.py').read())"` and `scripts/foo.py --help`. The full integration test is the deployment process itself (run on a target host).
- For `etc/nginx/*` edits: `sudo nginx -t -c <merged-config>` after install — but in practice the user is iterating on the host, so just install and `sudo systemctl reload nginx`.
- For `etc/systemd/*` edits: `systemd-analyze verify <unit>` catches obvious syntax errors.
- For `etc/sudoers.d/*` edits: never skip `sudo visudo -c -f <file>` after install — a broken sudoers can lock you out.
