#!/usr/bin/env python3
"""Patch an OSM.jp-derived MapLibre style for self-hosted, neutral rendering.

Transforms applied (idempotent):
  - Remove OSM.jp runtime dependency (drop hoppo/takeshima sources + 5 layers)
  - Rewrite the openmaptiles source URL to reference a local mbtiles
  - Replace migu1c/migu2m fonts with Noto Sans Regular (latin-only render path)
  - Hide maritime boundaries (boundary.maritime=1) on every boundary layer
  - Neutralize country borders: drop country-only layers and merge admin_level
    2 and 3 into the sub-national layer (admin_sub / boundary_state) so they
    render identically to admin_level=4 prefecture/state borders
  - Cap country labels (place.class=country) at maxzoom=5 (visible only z0-4)
  - Rewrite sprite/glyphs to absolute URLs

See tileserver-noborder.md §1.2, §1.3, §9.3 for design rationale.
"""
import json, argparse, copy

# =========================================================================
# Font replacement
# =========================================================================
def walk_replace_font(obj, src, dst):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text-font" and isinstance(v, list):
                obj[k] = [dst if (isinstance(x,str) and x == src) else x for x in v]
            elif k == "text-font" and isinstance(v, dict) and "stops" in v:
                for s in v["stops"]:
                    s[1] = [dst if (isinstance(x,str) and x == src) else x for x in s[1]]
            else:
                walk_replace_font(v, src, dst)
    elif isinstance(obj, list):
        for x in obj:
            walk_replace_font(x, src, dst)

# =========================================================================
# Append maritime!=1 to a boundary layer's filter (AND combine)
# =========================================================================
MARITIME_GUARD = ["!=", "maritime", 1]

def add_filter_clause(layer, clause):
    """Append `clause` to a layer's existing filter via AND.
       - No filter         -> set to ["all", clause]
       - ["all", ...]      -> append (deduplicated)
       - any other form    -> wrap as ["all", existing, clause]
    """
    f = layer.get("filter")
    if f is None:
        layer["filter"] = ["all", clause]
    elif isinstance(f, list) and len(f) > 0 and f[0] == "all":
        if clause not in f[1:]:
            f.append(clause)
    else:
        layer["filter"] = ["all", copy.deepcopy(f), clause]

# =========================================================================
# Inject extra admin_level values into a filter expression
# =========================================================================
def inject_admin_levels(f, extra_levels):
    """Extend admin_level conditions to accept additional levels.

    Patterns handled:
      ["in", "admin_level", 4, 6, 8]        -> ["in", "admin_level", 2, 3, 4, 6, 8]
      ["==", "admin_level", 4]              -> ["in", "admin_level", 2, 3, 4]
      ["all", ..., ["==","admin_level",4]]  -> recursed
    """
    if not isinstance(f, list) or len(f) == 0:
        return f
    op = f[0]
    if op == "in" and len(f) >= 2 and f[1] == "admin_level":
        values = list(f[2:])
        for v in extra_levels:
            if v not in values:
                values.append(v)
        return ["in", "admin_level"] + sorted(values)
    if op == "==" and len(f) == 3 and f[1] == "admin_level":
        base = f[2]
        values = sorted(set([base] + list(extra_levels)))
        return ["in", "admin_level"] + values
    if op in ("all", "any", "none"):
        return [op] + [inject_admin_levels(sub, extra_levels) for sub in f[1:]]
    if op == "!" and len(f) == 2:
        return ["!", inject_admin_levels(f[1], extra_levels)]
    return f

# =========================================================================
# Country-border neutralization: merge admin_level 2 and 3 into the
# admin_level=4 layer rather than rendering them with their own style
# =========================================================================
# Country-only (admin_level<=2) layer IDs across both styles
COUNTRY_ONLY_LAYER_IDS = {
    # Maptiler-Basic-en
    "admin_country_z0-4", "admin_country_z5-",
    # Maptiler-Toner-en
    "boundary_country_z0-4", "boundary_country_z5-",
}
# Sub-national (admin_level=4 etc.) layer IDs
SUB_LAYER_IDS = {
    "admin_sub",       # Maptiler-Basic-en
    "boundary_state",  # Maptiler-Toner-en
}
# maxzoom for country-name labels (visible at z0-4 only)
COUNTRY_LABEL_MAXZOOM = 5

def filter_matches_class(f, class_value):
    """True if filter contains ["==", "class", class_value] anywhere."""
    if not isinstance(f, list) or len(f) == 0:
        return False
    op = f[0]
    if op == "==" and len(f) == 3 and f[1] == "class" and f[2] == class_value:
        return True
    if op in ("all", "any"):
        return any(filter_matches_class(sub, class_value) for sub in f[1:])
    return False

def neutralize_country_boundaries(style):
    """Render country borders (admin_level=2,3) with the same style as
       prefecture/state borders (admin_level=4), and zoom-cap country labels.

    Returns (removed_layers, merged_layers, country_label_layers).
    """
    # (A) Drop admin_level<=2 dedicated layers
    before = len(style["layers"])
    style["layers"] = [l for l in style["layers"]
                       if l.get("id") not in COUNTRY_ONLY_LAYER_IDS]
    removed = before - len(style["layers"])

    # (B) Merge admin_level=2,3 into admin_sub/boundary_state filter [§1.2.2]
    #     and re-apply the maritime!=1 guard [§1.2.4 — the load-bearing one].
    #     Reason: the merge brings admin_level=2 maritime ways (e.g. between
    #     Nemuro and the Northern Territories) into this layer's draw set,
    #     so the maritime guard is required to suppress them.
    #     add_filter_clause() is idempotent, so a duplicate call is safe.
    merged = 0
    for l in style["layers"]:
        if l.get("id") in SUB_LAYER_IDS:
            l["filter"] = inject_admin_levels(l.get("filter"), [2, 3])
            add_filter_clause(l, MARITIME_GUARD)
            merged += 1

    # (C) Cap country-name labels (place.class=country) to low zoom only
    country_labels = 0
    for l in style["layers"]:
        if l.get("type") != "symbol":
            continue
        if l.get("source-layer") != "place":
            continue
        if filter_matches_class(l.get("filter"), "country"):
            l["maxzoom"] = COUNTRY_LABEL_MAXZOOM
            country_labels += 1

    return removed, merged, country_labels

# =========================================================================
# Main
# =========================================================================
def main(inp, outp, style_id, mbtiles_id, public_url):
    with open(inp, encoding="utf-8") as fh:
        st = json.load(fh)

    # 1. Point the openmaptiles source at the local mbtiles
    st["sources"]["openmaptiles"] = {
        "type": "vector",
        "url": f"mbtiles://{{{mbtiles_id}}}"
    }

    # 2. Drop hoppo, takeshima sources
    for s in ("hoppo", "takeshima"):
        st["sources"].pop(s, None)

    # 3. Drop layers that referenced the removed sources
    drop_ids = {"island-hoppo","island-hoppo-name",
                "island-takeshima","island-takeshima-name","island-takeshima-poi"}
    drop_sources = {"hoppo","takeshima"}
    st["layers"] = [l for l in st["layers"]
                    if l.get("id") not in drop_ids
                    and l.get("source") not in drop_sources]

    # 4. Font replacement
    walk_replace_font(st, "migu1c-regular", "Noto Sans Regular")
    walk_replace_font(st, "migu2m-regular", "Noto Sans Regular")

    # 5. [§1.2.1] Apply maritime!=1 guard to every boundary-layer-referencing
    #    layer. At this stage the dedicated admin_level=2 layers still exist
    #    and are also patched; they get removed by the next step.
    boundary_layers_patched = 0
    for l in st["layers"]:
        if l.get("source-layer") == "boundary":
            add_filter_clause(l, MARITIME_GUARD)
            boundary_layers_patched += 1

    # 6. [§1.2.2, §1.2.3, §1.2.4] Country-border neutralization (always on).
    #    neutralize_country_boundaries() performs:
    #      (A) remove admin_country_* / boundary_country_*
    #      (B) extend admin_sub / boundary_state filter (admin_level=2,3 merged)
    #          + re-apply maritime!=1 guard (§1.2.4 — load-bearing)
    #      (C) cap place.class=country labels at maxzoom=5
    removed, merged, country_labels = neutralize_country_boundaries(st)

    # 7. Rewrite sprite/glyphs to absolute URLs
    if public_url:
        st["sprite"] = f"{public_url.rstrip('/')}/styles/{style_id}/sprite"
        st["glyphs"] = f"{public_url.rstrip('/')}/fonts/{{fontstack}}/{{range}}.pbf"
    else:
        st["sprite"] = "{styleJsonFolder}/sprite"
        st["glyphs"] = "{fontstack}/{range}.pbf"

    # Attribution — per the upstream LICENSE.md credit examples:
    #   Toner LICENSE.md grants an exception making "© MapTiler" the sole
    #   required style copyright (no OpenMapTiles, no Stamen). Basic LICENSE.md
    #   has no such exception, so "© OpenMapTiles" is the required style
    #   credit. "© OpenStreetMap contributors" is required by ODbL on the
    #   underlying tile data in both cases.
    #
    # Policy: this project does NOT claim copyright on its style modifications
    # (boundary neutralization, font replacement, URL rewrites, etc.). The
    # patched style is released under the same upstream licenses (BSD 3-Clause
    # + CC-BY 4.0). Therefore the credit string here matches upstream LICENSE.md
    # examples verbatim — no "tileserver-noborder" or "Modified by ..." credit
    # is added or required of downstream consumers. See tileserver-noborder.md
    # §13.6 for the full policy statement.
    #
    #   Sources:
    #     https://github.com/openmaptiles/maptiler-toner-gl-style/blob/master/LICENSE.md
    #     https://github.com/openmaptiles/maptiler-basic-gl-style/blob/master/LICENSE.md
    st.setdefault("metadata", {})
    style_credit = (
        '<a href="https://www.maptiler.com/copyright/">&copy; MapTiler</a>'
        if "toner" in style_id else
        '<a href="https://openmaptiles.org/">&copy; OpenMapTiles</a>'
    )
    st["metadata"]["attribution"] = (
        f'{style_credit} | '
        '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a>'
    )

    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)
    print(f"patched -> {outp}")
    print(f"  style_id                         : {style_id}")
    print(f"  sources                          : {list(st['sources'].keys())}")
    print(f"  layers                           : {len(st['layers'])}")
    print(f"  boundary layers w/ maritime-guard: {boundary_layers_patched}")
    print(f"  country-only layers removed      : {removed}")
    print(f"  sub-national layers merged       : {merged}")
    print(f"  country labels (maxzoom=5)       : {country_labels}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--style-id", required=True,
                    help="Style identifier embedded in sprite URL (e.g. maptiler-toner-en)")
    ap.add_argument("--mbtiles-id", default="openmaptiles")
    ap.add_argument("--public-url", default="")
    args = ap.parse_args()
    main(args.input, args.output, args.style_id, args.mbtiles_id, args.public_url)
