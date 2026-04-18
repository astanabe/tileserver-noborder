#!/usr/bin/env python3
"""Inject a sea-color mask layer over the buffered island region.

After the buffer-erase step in §1.3, islands appear as background-colored
silhouettes in the sea. This script overlays a fill layer in the buffer
area painted in the same color as `water`, making islands visually merge
with the sea. Idempotent: an existing `jp-sea-mask` layer is replaced.

See tileserver-noborder.md §12.1.
"""
import argparse, json, pathlib

# Default sea color per style ID
DEFAULT_COLORS = {
    "maptiler-toner-en":  "#000000",                  # same black as water
    "maptiler-basic-en":  "hsl(205, 56%, 73%)",       # same light blue as water
}

def apply(style_path, mask_geojson, color, source_id="jp-mask", layer_id="jp-sea-mask"):
    style_p = pathlib.Path(style_path)
    st = json.loads(style_p.read_text(encoding="utf-8"))
    mask = json.loads(pathlib.Path(mask_geojson).read_text(encoding="utf-8"))

    st["sources"][source_id] = {"type": "geojson", "data": mask}

    # Insert just above the water layer (below borders)
    idx = next(i for i,l in enumerate(st["layers"]) if l.get("id") == "water") + 1

    # Idempotency: drop any existing instance before re-inserting
    st["layers"] = [l for l in st["layers"] if l.get("id") != layer_id]
    st["layers"].insert(idx, {
        "id": layer_id,
        "type": "fill",
        "source": source_id,
        "paint": {"fill-color": color, "fill-opacity": 1}
    })

    style_p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{style_path}: inserted {layer_id} ({color})")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", required=True, help="Path to style.json to patch")
    ap.add_argument("--mask",  required=True, help="Path to islands_buffered.geojson")
    ap.add_argument("--color", help="Override fill color; defaults derived from --style-id")
    ap.add_argument("--style-id", help="One of: " + ", ".join(DEFAULT_COLORS),
                    choices=list(DEFAULT_COLORS))
    args = ap.parse_args()

    color = args.color
    if color is None:
        if args.style_id is None:
            ap.error("either --color or --style-id is required")
        color = DEFAULT_COLORS[args.style_id]

    apply(args.style, args.mask, color)
