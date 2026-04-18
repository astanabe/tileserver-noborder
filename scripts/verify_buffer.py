#!/usr/bin/env python3
"""Sanity-check a 2 km island buffer GeoJSON.

A point ~1 km north of Etorofu's northern tip should fall *inside* the
buffered polygon (i.e. inside the .poly hole, so removed by osmium);
a point ~3 km north should fall *outside*.

Reference tip: lon=147.85864, lat=45.58811 (northernmost vertex of the
hoppo dataset, geojson/hoppo.geojson). 1 deg latitude ~ 111 km, so
+0.009 deg ~ 1 km, +0.027 deg ~ 3 km.

See tileserver-noborder.md §6.
"""
import argparse, json, sys
from shapely.geometry import shape, Point

TIP_LON = 147.85864
TIP_LAT = 45.58811
KM_PER_DEG_LAT = 111.0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", required=True,
                    help="Path to islands_buffered.geojson produced by buffer_clip.py")
    args = ap.parse_args()

    with open(args.geojson, encoding="utf-8") as fh:
        b = shape(json.load(fh)["features"][0]["geometry"])

    p_1km = Point(TIP_LON, TIP_LAT + 1.0/KM_PER_DEG_LAT)
    p_3km = Point(TIP_LON, TIP_LAT + 3.0/KM_PER_DEG_LAT)

    inside_1km  = b.contains(p_1km)
    outside_3km = not b.contains(p_3km)
    print(f"1km north of Etorofu (expect inside hole) : {inside_1km}")
    print(f"3km north of Etorofu (expect outside hole): {outside_3km}")
    sys.exit(0 if (inside_1km and outside_3km) else 1)
