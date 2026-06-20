#!/usr/bin/env python3
"""Build the disputed-area clip region and emit an osmium .poly file.

The region is the union of three optional sources:
  --inputs   GeoJSON files, unioned. Used VERBATIM by default; a geodesic
             buffer (Azimuthal Equidistant, metric-accurate at all latitudes)
             is applied only when BUFFER_KM > 0 / --buffer-m > 0;
  --bbox     lon/lat rectangles, taken verbatim (unbuffered);
  --polygon  arbitrary lon/lat outlines, taken verbatim (unbuffered).

Range expansion is therefore OFF by default everywhere. To buffer --inputs
geometry, set BUFFER_KM to the desired distance in kilometres (e.g. BUFFER_KM=2).

The deployment (rebuild.sh) now defines every area from explicit coordinates
(--bbox / --polygon), so NO external GeoJSON is needed — the build has no
OSM.jp dependency at all. --inputs is kept only for ad-hoc use.

The .poly puts the whole world as the outer ring and each region as a hole,
so `osmium extract -p` selects everything inside the regions. The companion
GeoJSON (--debug) is the SAME region union, consumed by rebuild.sh §3.5/§3.6
to strip labels inside it.

See tileserver-noborder.md §6 for context.
"""
import argparse, json, os
from shapely.geometry import shape, box, Polygon, MultiPolygon, mapping
from shapely.ops import transform, unary_union
import pyproj

# Default geodesic buffer for --inputs geometry, taken from the BUFFER_KM env
# var (value is in KILOMETRES). BUFFER_KM=0 (the default) disables buffering
# entirely — --inputs geometry is then used verbatim, like --bbox / --polygon.
# Only the optional --inputs path is affected; --bbox / --polygon are never
# buffered.
try:
    _DEF_BUFFER_M = float(os.environ.get("BUFFER_KM", "0")) * 1000.0
except ValueError:
    _DEF_BUFFER_M = 0.0

def geodetic_buffer(geom, meters):
    cx, cy = geom.centroid.x, geom.centroid.y
    aeqd  = pyproj.CRS(f"+proj=aeqd +lat_0={cy} +lon_0={cx} +datum=WGS84 +units=m")
    wgs84 = pyproj.CRS("EPSG:4326")
    fwd = pyproj.Transformer.from_crs(wgs84, aeqd, always_xy=True).transform
    bck = pyproj.Transformer.from_crs(aeqd, wgs84, always_xy=True).transform
    return transform(bck, transform(fwd, geom).buffer(meters, resolution=16, join_style=1))

def polys_of(g):
    if isinstance(g, Polygon): return [g]
    if isinstance(g, MultiPolygon): return list(g.geoms)
    return []

def load_union(paths):
    gs = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for f in json.load(fh)["features"]:
                g = shape(f["geometry"])
                if not g.is_valid: g = g.buffer(0)
                gs.append(g)
    return unary_union(gs)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=[],
                    help="Optional input GeoJSON files (unioned, used verbatim unless "
                         "a buffer is requested). Omit to define the region purely "
                         "from --bbox / --polygon coordinates (no external GeoJSON).")
    ap.add_argument("--buffer-m", type=float, default=_DEF_BUFFER_M,
                    help="Geodesic buffer (METRES) applied to --inputs geometry only. "
                         "Default comes from the BUFFER_KM env var (km*1000); "
                         "0 = no buffer (verbatim). --bbox/--polygon are never buffered.")
    ap.add_argument("--bbox", action="append", default=[], metavar="W,S,E,N",
                    help="lon/lat rectangle (min_lon,min_lat,max_lon,max_lat), added "
                         "UNBUFFERED (verbatim). Repeatable. E.g. Takeshima "
                         "--bbox 131.84,37.22,131.89,37.26 ; Senkaku "
                         "--bbox 123.29,25.59,123.77,26.02")
    ap.add_argument("--polygon", action="append", default=[],
                    metavar="lon,lat;lon,lat;...",
                    help="Arbitrary lon/lat polygon (>=3 semicolon-separated "
                         "'lon,lat' vertices), added UNBUFFERED (verbatim). "
                         "Repeatable. E.g. the Northern Territories outline.")
    ap.add_argument("--out",   required=True, help="Output .poly path (e.g. $BUILD_ROOT/build/world_minus_islands.poly)")
    ap.add_argument("--debug", required=True, help="Output region GeoJSON path (e.g. $BUILD_ROOT/build/islands_buffered.geojson)")
    args = ap.parse_args()

    # The strip region is the union of: buffered input GeoJSON (optional) +
    # verbatim --bbox rectangles + verbatim --polygon outlines.
    parts = []
    if args.inputs:
        merged = load_union(args.inputs)
        if args.buffer_m > 0:                       # 0 (default) => verbatim, no expansion
            merged = (geodetic_buffer(merged, args.buffer_m)
                      .simplify(0.0002, preserve_topology=True))
        parts.append(merged)
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
        ap.error("no region defined: pass at least one of --inputs / --bbox / --polygon")
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
