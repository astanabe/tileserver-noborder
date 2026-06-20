#!/usr/bin/env python3
"""List OSM element IDs whose direct tags produce labels / POIs.

Input is an OPL dump (from `osmium cat -f opl`) of the residual inside the
disputed-island buffer. Output is one element ID per line (e.g. ``w22880716``,
``r7273565``), suitable for ``osmium removeid --id-file=-``.

Unlike ``osmium tags-filter``, this does NOT include elements that are only
referenced as members of a matching relation. Only elements that themselves
carry a label-producing tag are listed — so removing the output IDs won't
break island-silhouette coastlines that happen to be members of a named
archipelago multipolygon.

See tileserver-noborder.md §7 for context.
"""
import argparse
import re
import sys

# Presence of any of these keys on an element produces a rendered label /
# POI / line / polygon in the OpenMapTiles schema.
TARGET_KEYS = {"name", "place", "amenity", "tourism", "shop",
               "highway", "building", "waterway"}
# Pairs where only a specific value matters (plain ``natural=`` is common
# and non-labeled, so match ``natural=peak`` only).
TARGET_KV = {("natural", "peak")}

OPL_LINE = re.compile(r"([nwr]\d+)\s.*\sT(\S*)")


def ids_from_opl(path):
    ids = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = OPL_LINE.match(line)
            if not m:
                continue
            for kv in m.group(2).split(","):
                key, _, val = kv.partition("=")
                if key in TARGET_KEYS or (key, val) in TARGET_KV:
                    ids.add(m.group(1))
                    break
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--opl", required=True,
                    help="OPL file produced by `osmium cat -f opl` on the residual PBF")
    args = ap.parse_args()
    for i in sorted(ids_from_opl(args.opl)):
        print(i)


if __name__ == "__main__":
    main()
