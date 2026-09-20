#!/usr/bin/env python3
"""Build the disputed-area clip region and emit an osmium .poly file.

The region is the union of:
  --bbox     lon/lat rectangles;
  --polygon  arbitrary lon/lat outlines.
Coordinates are used exactly as given (no buffering).

The .poly puts the whole world as the outer ring and each region as a hole,
so `osmium extract -p` selects everything outside the regions. The companion
GeoJSON (--debug) is the same region union; rebuild.sh steps (3.5)/(3.6) use
it as the extract mask for the island residual / label-strip passes.

See tileserver-noborder.md §5 for context.
"""
import argparse, json
from shapely.geometry import box, Polygon, MultiPolygon, mapping
from shapely.ops import unary_union

def polys_of(g):
    if isinstance(g, Polygon): return [g]
    if isinstance(g, MultiPolygon): return list(g.geoms)
    return []

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", action="append", default=[], metavar="W,S,E,N",
                    help="lon/lat rectangle (min_lon,min_lat,max_lon,max_lat). "
                         "Repeatable. E.g. Takeshima "
                         "--bbox 131.84,37.22,131.89,37.26 ; Senkaku "
                         "--bbox 123.29,25.59,123.77,26.02")
    ap.add_argument("--polygon", action="append", default=[],
                    metavar="lon,lat;lon,lat;...",
                    help="Arbitrary lon/lat polygon (>=3 semicolon-separated "
                         "'lon,lat' vertices). Repeatable. E.g. the Northern "
                         "Territories outline.")
    ap.add_argument("--out",   required=True, help="Output .poly path (e.g. $BUILD_ROOT/build/world_minus_islands.poly)")
    ap.add_argument("--debug", required=True, help="Output region GeoJSON path (e.g. $BUILD_ROOT/build/islands_buffered.geojson)")
    args = ap.parse_args()

    # The strip region is the union of the --bbox rectangles and --polygon
    # outlines.
    parts = []
    for b in args.bbox:
        w, s, e, n = (float(v) for v in b.split(","))
        parts.append(box(w, s, e, n))
    for p in args.polygon:
        pts = [tuple(float(v) for v in xy.split(",")) for xy in p.split(";") if xy]
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)              # repair self-intersections
        parts.append(poly)
    if not parts:
        ap.error("no region defined: pass at least one of --bbox / --polygon")
    region = unary_union(parts)

    with open(args.debug, "w") as fh:
        json.dump({"type":"FeatureCollection","features":[
            {"type":"Feature","properties":{},"geometry":mapping(region)}
        ]}, fh)

    world = box(-180.0, -89.9, 180.0, 89.9)
    holes = [list(p.exterior.coords) for p in polys_of(region)]
    outer = list(world.exterior.coords)

    with open(args.out, "w") as fh:
        fh.write("world_minus_islands\n")
        fh.write("outer\n")
        for x, y in outer:
            fh.write(f"   {x:.7f}   {y:.7f}\n")
        fh.write("END\n")
        for i, ring in enumerate(holes, 1):
            fh.write(f"!hole_{i}\n")
            for x, y in ring:
                fh.write(f"   {x:.7f}   {y:.7f}\n")
            fh.write("END\n")
        fh.write("END\n")
    print(f"wrote {args.out} (holes={len(holes)})")
