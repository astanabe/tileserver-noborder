#!/usr/bin/env python3
"""Patch an OSM.jp-derived MapLibre style for self-hosted, neutral rendering.

Transforms applied (idempotent):
  - Remove OSM.jp runtime dependency (drop hoppo/takeshima sources + 5 layers)
  - Rewrite the openmaptiles source URL to reference a local mbtiles
  - Replace migu1c/migu2m fonts with Noto Sans Regular (latin-only render path)
  - Rewrite every text-field that references a name:* attribute to prefer the
    English name, falling back to name:latin:
        ["coalesce", ["get","name:en"], ["get","name:latin"]]
    This fixes places whose default name is already Latin but differs from
    English (Greenland: name="Kalaallit Nunaat", name:en="Greenland",
    name:latin="Kalaallit Nunaat" — a bare {name:latin} would show the
    Greenlandic name). Both branches are Latin-script, so no CJK/Cyrillic
    glyphs re-enter the font stack, and there is deliberately no {name}
    fallback (it could be non-Latin). Drops any {name:nonlatin} companion so
    labels render English-only (see tileserver-noborder.md §1.1, §8).
  - Hide maritime boundaries (boundary.maritime=1) on every boundary layer
  - Mask over-water boundary lines by moving the water fill above the boundary
    layers, then lifting transportation back above it (catches maritime=0 strait
    lines; keeps bridges/tunnels visible)
  - Neutralize country borders: drop country-only layers and merge admin_level
    2 and 3 into the sub-national layer (admin_sub / boundary_state) so they
    render identically to admin_level=4 prefecture/state borders
  - Cap country labels (place.class=country) at maxzoom=5 (visible only z0-4)
  - Point sprite/glyphs at tileserver-gl-local paths (kept domain-agnostic;
    tileserver-gl absolutizes them at serve time from the request host)

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
# Rewrite text-field to prefer name:en, then name:latin, on any layer that
# references a name:* attribute. Leaves non-name text-fields (e.g.
# "{housenumber}") untouched. Idempotent: already-rewritten values are not
# re-counted.
#
# name:latin is the latinization of the DEFAULT name; for a place whose
# default name is already Latin script (Greenland = "Kalaallit Nunaat",
# Germany = "Deutschland") planetiler keeps that name in name:latin and does
# NOT substitute name:en. Preferring name:en fixes those; the name:latin
# fallback still covers places transliterated from a non-Latin default
# (Tokyo). No {name} fallback: it may be non-Latin and would reintroduce
# glyphs the latin-only font stack does not carry.
# =========================================================================
ENGLISH_NAME_FIELD = ["coalesce", ["get", "name:en"], ["get", "name:latin"]]

def _references_name(v):
    """True if the text-field value references any name:* attribute,
    whether as a {name:xx} template string or inside a ['get','name:xx']
    style expression / stops function."""
    return "name:" in json.dumps(v, ensure_ascii=False)

def normalize_text_field(obj):
    count = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text-field":
                if v != ENGLISH_NAME_FIELD and _references_name(v):
                    obj[k] = copy.deepcopy(ENGLISH_NAME_FIELD)
                    count += 1
            else:
                count += normalize_text_field(v)
    elif isinstance(obj, list):
        for x in obj:
            count += normalize_text_field(x)
    return count

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
    #     and re-apply the maritime!=1 guard (the merge pulls admin_level=2
    #     maritime ways into this layer; add_filter_clause is idempotent).
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
# Sea mask: hide every over-water boundary line [§1.2.5]
# =========================================================================
def mask_sea_boundaries(style):
    """Hide every over-water boundary line, regardless of `maritime`.

    The `maritime!=1` guard only catches maritime=1 boundaries; internal strait
    prefecture lines and the Hokkaido<->Northern Territories line are maritime=0.
    Move the opaque `water` fill above the boundary line layers (the sea then
    covers any boundary over water; land boundaries stay visible), then lift
    every `transportation` layer back above `water` so bridges/tunnels crossing
    water stay visible. Land rendering is unchanged. Idempotent; reorder only.
    Returns (water_moved, transport_lifted).
    """
    layers = style["layers"]

    def water_index():
        i = next((i for i, l in enumerate(layers) if l.get("id") == "water"), None)
        if i is None:
            i = next((i for i, l in enumerate(layers)
                      if l.get("type") == "fill"
                      and l.get("source-layer") == "water"), None)
        return i

    def last_boundary_index():
        idx = [i for i, l in enumerate(layers)
               if l.get("type") == "line" and l.get("source-layer") == "boundary"]
        return idx[-1] if idx else None

    wi = water_index()
    last_b = last_boundary_index()
    if wi is None or last_b is None:
        return 0, 0

    # 1. Raise water above the boundary lines.
    water_moved = 0
    if wi <= last_b:
        water = layers.pop(wi)                       # indices above wi shift down
        layers.insert(last_boundary_index() + 1, water)
        water_moved = 1

    # 2. Lift every transportation layer above the water mask so bridges (and
    #    the tunnel segments the same brunnel-agnostic road layers draw) stay
    #    visible over water; only boundary lines remain masked.
    wi = water_index()
    lift_idx = [i for i, l in enumerate(layers)
                if i < wi
                and l.get("source-layer") == "transportation"]
    lifted = [layers[i] for i in lift_idx]           # preserve relative order
    for i in reversed(lift_idx):                      # remove bottom-up
        del layers[i]
    wi = water_index()                                # recompute after removals
    for off, l in enumerate(lifted):
        layers.insert(wi + 1 + off, l)

    return water_moved, len(lifted)


# =========================================================================
# Main
# =========================================================================
def main(inp, outp, style_id, mbtiles_id):
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

    # 4.5. [§1.1] Rewrite text-field to coalesce(name:en, name:latin),
    #      dropping the {name:nonlatin} companion that upstream styles include.
    #      Prefers the real English name over the latinized default name (so
    #      Greenland renders "Greenland", not "Kalaallit Nunaat"), while still
    #      rendering empty when neither Latin name exists — matching
    #      --transliterate=false's intent and keeping CJK / Cyrillic glyph
    #      requirements out of the font stack entirely.
    text_fields_normalized = normalize_text_field(st)

    # 5. [§1.2.1] Apply maritime!=1 guard to every boundary-layer-referencing
    #    layer. At this stage the dedicated admin_level=2 layers still exist
    #    and are also patched; they get removed by the next step.
    boundary_layers_patched = 0
    for l in st["layers"]:
        if l.get("source-layer") == "boundary":
            add_filter_clause(l, MARITIME_GUARD)
            boundary_layers_patched += 1

    # 6. [§1.2.2-4] Country-border neutralization: (A) remove admin_country_* /
    #    boundary_country_*, (B) merge admin_level=2,3 into admin_sub /
    #    boundary_state (+ maritime guard), (C) cap country labels at maxzoom=5.
    removed, merged, country_labels = neutralize_country_boundaries(st)

    # 6.5 [§1.2.5] Mask every over-water boundary line (maritime!=1 misses the
    #     maritime=0 strait lines); keeps bridges/tunnels visible.
    water_moved, transport_lifted = mask_sea_boundaries(st)

    # 7. Sprite/glyphs as tileserver-gl-local paths (domain stays out of the
    #    file; tileserver-gl absolutizes them at serve time from the request
    #    Host/X-Forwarded-Proto, §13). sprite must be a non-http path or
    #    tileserver-gl 5.x won't serve it (/styles/<id>/sprite.* -> 400);
    #    "<id>/sprite" resolves under sprites/<id>/sprite.*.
    st["sprite"] = f"{style_id}/sprite"
    # glyphs: no "fonts/" prefix — tileserver-gl prepends the protocol itself
    # (serve_rendered -> "fonts://" + value); a prefix doubles it and 500s every
    # labeled raster tile ("Invalid range").
    st["glyphs"] = "{fontstack}/{range}.pbf"

    # Attribution per upstream LICENSE.md: Toner requires only "© MapTiler",
    # Basic requires "© OpenMapTiles"; both require "© OpenStreetMap
    # contributors" (ODbL). This project claims no copyright on its style
    # modifications, so no extra credit is added (policy: §13.6).
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
    print(f"  text-field -> coalesce(en,latin) : {text_fields_normalized}")
    print(f"  water raised above boundaries    : {'moved' if water_moved else 'already above'}")
    print(f"  transportation lifted over mask  : {transport_lifted}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--style-id", required=True,
                    help="Style identifier embedded in sprite URL (e.g. maptiler-toner-en)")
    ap.add_argument("--mbtiles-id", default="openmaptiles")
    args = ap.parse_args()
    main(args.input, args.output, args.style_id, args.mbtiles_id)
