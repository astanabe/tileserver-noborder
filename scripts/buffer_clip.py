#!/usr/bin/env python3
"""Geodesic-buffer the union of island GeoJSONs and emit an osmium .poly file.

The .poly defines the entire world as the outer ring and each buffered
island as a hole, so `osmium extract -p` deletes everything within
{buffer_m} meters of any island. Buffer is computed in an Azimuthal
Equidistant projection centered on the geometry's centroid so the
distance is metric-accurate at all latitudes.

See tileserver-noborder.md §6 for context.
"""
import argparse, json
from shapely.geometry import shape, box, Polygon, MultiPolygon, mapping
from shapely.ops import transform, unary_union
import pyproj

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
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--buffer-m", type=float, default=2000.0)
    ap.add_argument("--out", default="/work/shimotsuki/planetiler/build/world_minus_islands.poly")
    ap.add_argument("--debug", default="/work/shimotsuki/planetiler/build/islands_buffered.geojson")
    args = ap.parse_args()

    merged   = load_union(args.inputs)
    buffered = geodetic_buffer(merged, args.buffer_m).simplify(0.0002, preserve_topology=True)

    with open(args.debug, "w") as fh:
        json.dump({"type":"FeatureCollection","features":[
            {"type":"Feature","properties":{},"geometry":mapping(buffered)}
        ]}, fh)

    world = box(-180.0, -89.9, 180.0, 89.9)
    holes = [list(p.exterior.coords) for p in polys_of(buffered)]
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
