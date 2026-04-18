#!/usr/bin/env python3
"""Expand OSM.jp MVT island polygons into a GeoJSON FeatureCollection.

Geometry-only output — attributes are discarded. The result is consumed by
buffer_clip.py to build a geodesic 2 km buffer used as an osmium clip mask.
See tileserver-noborder.md §5 for context.
"""
import argparse, json, sys, time
import requests, mercantile, mapbox_vector_tile
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

def tile_to_wgs84(tile_bytes, z, x, y, layer_name):
    dec = mapbox_vector_tile.decode(tile_bytes)
    if layer_name not in dec: return []
    layer = dec[layer_name]
    extent = layer.get("extent", 4096)
    b = mercantile.bounds(x, y, z)
    dx, dy = b.east - b.west, b.north - b.south
    def T(c): return [b.west + c[0]/extent*dx, b.north - c[1]/extent*dy]
    def Tg(g):
        t = g["type"]
        if t == "Polygon":
            return {"type":"Polygon","coordinates":[[T(c) for c in r] for r in g["coordinates"]]}
        if t == "MultiPolygon":
            return {"type":"MultiPolygon","coordinates":[[[T(c) for c in r] for r in p] for p in g["coordinates"]]}
        return None
    out = []
    for f in layer.get("features", []):
        tg = Tg(f["geometry"])
        if tg: out.append({"type":"Feature","properties":{},"geometry":tg})
    return out

def fetch(tilejson_url, layer, zoom, outfile, sleep=0.03):
    meta = requests.get(tilejson_url, timeout=30).json()
    w,s,e,n = meta["bounds"]
    url_tpl = meta["tiles"][0]
    lmeta = next((l for l in meta["vector_layers"] if l["id"] == layer), None)
    if not lmeta: sys.exit(f"layer '{layer}' not found")
    z = max(min(zoom, lmeta["maxzoom"]), lmeta["minzoom"])
    tiles = list(mercantile.tiles(w, s, e, n, z))
    print(f"fetch {tilejson_url} layer={layer} z={z} tiles={len(tiles)}", file=sys.stderr)

    polys = []
    for i, t in enumerate(tiles):
        url = url_tpl.format(z=t.z, x=t.x, y=t.y)
        try:
            r = requests.get(url, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.content:
            continue
        try:
            for f in tile_to_wgs84(r.content, t.z, t.x, t.y, layer):
                g = shape(f["geometry"])
                if not g.is_valid: g = g.buffer(0)
                if not g.is_empty: polys.append(g)
        except Exception as ex:
            print(f"  warn {url}: {ex}", file=sys.stderr)
        if (i+1) % 50 == 0:
            print(f"  progress {i+1}/{len(tiles)}", file=sys.stderr)
        time.sleep(sleep)

    merged = unary_union(polys) if polys else None
    feats = []
    if merged is not None and not merged.is_empty:
        geoms = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
        feats = [{"type":"Feature","properties":{},"geometry":mapping(g)} for g in geoms]
    with open(outfile, "w", encoding="utf-8") as fh:
        json.dump({"type":"FeatureCollection","features":feats}, fh)
    print(f"wrote {outfile} ({len(feats)} polygons)", file=sys.stderr)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tilejson", required=True)
    ap.add_argument("--layer", required=True)
    ap.add_argument("--zoom", type=int, default=10)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    fetch(args.tilejson, args.layer, args.zoom, args.out)
