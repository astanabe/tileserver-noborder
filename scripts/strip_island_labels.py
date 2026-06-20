#!/usr/bin/env python3
"""Strip text/label tags from OSM objects, preserving all geometry.

Reads OPL (osmium's one-object-per-line text format) on stdin and writes OPL on
stdout, removing ONLY the tags that render as on-map *text* — names, route refs,
and house numbers — while leaving every object and all of its geometry intact.

Used by scripts/rebuild.sh on the small island-buffer extract (Northern
Territories / Takeshima): the islands keep their rivers, terrain, roads and
buildings, but render with no labels at all (see tileserver-noborder.md §1.3 /
§7.2). The caller has already spatially limited the input to the island buffer
via `osmium extract`, so this strips unconditionally — no per-object geometry
test is needed.

OPL layout: space-separated fields; the tags field is the single field that
begins with an uppercase 'T', holding "key=val,key=val,...". OPL %-encodes any
literal space / comma / '=' / '%' inside keys and values, so splitting on ','
and the first '=' is unambiguous (real separators never appear inside a token).
"""
import sys

# Tag keys whose values become on-map text in the patched MapLibre styles.
_TEXT_BASES = (
    "alt_name", "int_name", "loc_name", "old_name",
    "official_name", "short_name", "nat_name", "reg_name",
)

def _is_text_tag(key):
    """True if `key` produces a rendered label in the patched styles."""
    if key == "name" or key.startswith("name:"):
        return True                                   # name, name:en, name:ja, ...
    for base in _TEXT_BASES:
        if key == base or key.startswith(base + ":"):
            return True
    if key == "ref" or key.startswith("ref:"):
        return True                                   # road / route shields
    if key == "addr:housenumber":
        return True                                   # housenumber symbol layer
    return False

def _strip_tags_field(field):
    """field is "T" + "k=v,k=v,..." (or bare "T"). Drop text-producing tags."""
    body = field[1:]
    if not body:
        return field
    kept = [kv for kv in body.split(",") if not _is_text_tag(kv.split("=", 1)[0])]
    return "T" + ",".join(kept)

def main():
    out = sys.stdout
    for line in sys.stdin:
        stripped = line.rstrip("\n")
        if stripped:
            parts = stripped.split(" ")
            for i, f in enumerate(parts):
                if f.startswith("T"):                 # unique tags field
                    parts[i] = _strip_tags_field(f)
                    break
            stripped = " ".join(parts)
        out.write(stripped)
        out.write("\n" if line.endswith("\n") else "")

if __name__ == "__main__":
    main()
