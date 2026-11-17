"""Clip the official GBA release to the two platform demo extents.

Requires shapely. Uses HTTP byte ranges on release files, never the viewer WFS.
Only small filtered shards persist in --cache; interrupted runs can resume.
Full regional tiles must be scanned: a missing shard is a failure, not no data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

from shapely.geometry import box, shape, mapping
from shapely.ops import transform, unary_union

TILE = "e115_n40_e120_n35"
BASE = "https://huggingface.co/datasets/zhu-xlab/"
SOURCES = [
    ("polygon", f"GBA.LoD1/resolve/main/Polygon/asiaeast/{TILE}.geojson", 10487781103),
    ("odbl", f"GBA.ODbLPolygon/resolve/main/asiaeast/{TILE}.geojson", 179649375),
    ("height", f"GBA.LoD1/resolve/main/LoD1/asiaeast/{TILE}.json", 1549396891),
]
BOUNDS = [[115.975, 39.046, 116.008, 39.074], [115.945, 38.996, 115.978, 39.024]]
R = 6378137.0
CHUNK = 32 * 1024 * 1024
OVERLAP = 1024 * 1024


def project(lon, lat, z=None):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def unproject(x, y, z=None):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


REGION = unary_union([box(*project(b[0], b[1]), *project(b[2], b[3])) for b in BOUNDS])
PROJECTED_BOUNDS = [(*project(b[0], b[1]), *project(b[2], b[3])) for b in BOUNDS]


def clip_feature(feature):
    geometry = feature["geometry"]
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    points = [p for rings in polygons for p in rings[0]]
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    if not any(x1 >= a and x0 <= c and y1 >= b and y0 <= d for a, b, c, d in PROJECTED_BOUNDS):
        return None
    geom = shape(feature["geometry"])
    # Cheap envelope rejection before polygon operations.
    if not REGION.intersects(geom.envelope):
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    geom = geom.intersection(REGION)
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    props = feature["properties"]
    key = str(props.get("source", "")) + str(props.get("id", "")) + str(props.get("region", ""))
    return {"type": "Feature", "id": key, "properties": props,
            "geometry": mapping(transform(unproject, geom))}


def fetch_shard(source, start, cache, keys):
    kind, path, size = source
    dest = cache / f"{kind}-{start}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    end = min(size - 1, start + CHUNK + OVERLAP - 1)
    with tempfile.TemporaryDirectory(prefix="gba-range-") as tmp:
        raw, headers = Path(tmp) / "range", Path(tmp) / "headers"
        subprocess.run(["curl", "-L", "--retry", "3", "--retry-all-errors", "--max-time", "180", "--max-filesize", str(CHUNK + OVERLAP),
                        "-fsS", "-D", str(headers), "-r", f"{start}-{end}",
                        BASE + path + f"?range_start={start}", "-o", str(raw)], check=True)
        if raw.stat().st_size != end - start + 1 or f"bytes {start}-{end}/{size}" not in headers.read_text().lower():
            raise ValueError("Server did not return the requested byte range")
        data = raw.read_bytes()
    found = parse_shard(data, kind, keys)
    temp = dest.with_suffix(".tmp")
    temp.write_text(json.dumps(found, ensure_ascii=False))
    temp.replace(dest)
    return found


def parse_shard(data, kind, keys, chunk_size=CHUNK):
    """Consume record starts in the core range and finish them in the overlap."""
    found = []
    if kind == "height":
        # Values in the official file are flat objects containing height and var.
        for match in re.finditer(rb'"([^"\\]+)"\s*:\s*(\{[^{}]*\})', data):
            if match.start() >= chunk_size:
                break
            key = match[1].decode()
            if key in keys:
                found.append([key, json.loads(match[2])])
    else:
        offset = 0
        for line in data.splitlines(keepends=True):
            pos = offset
            offset += len(line)
            if pos >= chunk_size:
                break
            if not line.startswith(b'{ "type": "Feature",'):
                continue
            feature = json.loads(line.strip().rstrip(b","))
            clipped = clip_feature(feature)
            if clipped:
                clipped["properties"]["footprint_license"] = "ODbL-1.0" if kind == "odbl" else "CC-BY-NC-4.0"
                found.append(clipped)
    return found


def prepare(cache, output, workers=4):
    cache.mkdir(parents=True, exist_ok=True)
    features, heights = {}, {}
    for source in SOURCES:
        kind, _, size = source
        starts = list(range(0, size, CHUNK))
        failed = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = [pool.submit(fetch_shard, source, start, cache, set(features)) for start in starts]
            for n, future in enumerate(as_completed(jobs), 1):
                try:
                    values = future.result()
                except Exception as error:
                    failed.append(str(error))
                    print(f"{kind}: shard failed ({len(failed)}); other shards continue", flush=True)
                    continue
                if kind == "height":
                    heights.update(values)
                else:
                    features.update((f["id"], f) for f in values)
                print(f"{kind}: {n}/{len(starts)} shards; buildings={len(features)}, heights={len(heights)}", flush=True)
        if failed:
            raise RuntimeError(f"{len(failed)} {kind} shards failed; rerun with the same cache. Output was not changed. First error: {failed[0]}")
    missing = []
    for key, feature in features.items():
        props = feature["properties"]
        height = heights.get(key, {}).get("height")
        if height is not None and math.isfinite(height) and 0 < height <= 1000:
            variance = heights[key].get("var")
            variance = variance if variance is not None and math.isfinite(variance) else None
            props.update(height=round(height, 2), height_estimated=True,
                         height_source="GlobalBuildingAtlas 模型估计（非测绘）", height_variance=variance)
        else:
            missing.append(key)
        props["dataset"] = "GlobalBuildingAtlas"
    result = {"type": "FeatureCollection", "metadata": {
        "source": "https://github.com/zhu-xlab/GlobalBuildingAtlas", "tile": TILE,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(), "bounds_wgs84": BOUNDS,
        "input_crs": "EPSG:3857", "output_crs": "EPSG:4326", "height_reference": "height above local flat ground, metres",
        "imagery_epoch_note": "GBA primarily uses 2019 imagery, supplemented by 2018; not a current building survey",
        "height_license": "CC-BY-NC-4.0", "attribution": "GlobalBuildingAtlas — Zhu, Chen, Zhang, Shi & Wang (2025)",
        "height_missing_count": len(missing), "usage": "非商业研究演示；模型估计，非测绘、非飞行避障依据",
        "files_scanned": [BASE + s[1] for s in SOURCES]}, "features": sorted(features.values(), key=lambda f: f["id"])}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    temp.replace(output)
    print(f"Saved {len(features)} buildings to {output}; SHA256={hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/gba-xiongan-cache"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "static/buildings.geojson")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    prepare(args.cache, args.output, args.workers)
