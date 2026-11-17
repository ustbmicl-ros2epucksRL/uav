"""Prepare, validate and publish the platform's Xiongan reference buildings.

The satellite overlay is a visual review step, not an automated accuracy claim.
Run through buildings.sh to use an isolated environment outside iCloud.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile

from shapely.geometry import box, shape
from shapely.ops import unary_union
from prepare_gba_buildings import BOUNDS, prepare

PLATFORM = Path(__file__).resolve().parents[1]


def validate(path):
    data = json.loads(path.read_text())
    features = data.get('features', [])
    if data.get('type') != 'FeatureCollection' or not 0 < len(features) <= 5000:
        raise ValueError('需要含 1–5000 个要素的 FeatureCollection；空数据不会发布')
    metadata = data.get('metadata', {})
    if metadata.get('output_crs') != 'EPSG:4326' or metadata.get('bounds_wgs84') != BOUNDS:
        raise ValueError('坐标系或展示范围不符合当前雄安配置')
    region = unary_union([box(*bounds) for bounds in BOUNDS]).buffer(1e-9)
    identifiers, heights, polygons, missing, invalid = set(), [], 0, 0, 0
    for feature in features:
        ident = feature.get('id')
        if not ident or ident in identifiers:
            raise ValueError('建筑编号缺失或重复')
        identifiers.add(ident)
        geom = shape(feature['geometry'])
        if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon') or not region.covers(geom):
            raise ValueError(f'{ident}: 几何无效或坐标超出裁剪范围')
        polygons += len(geom.geoms) if geom.geom_type == 'MultiPolygon' else 1
        props = feature.get('properties', {})
        h = props.get('height')
        if h is None:
            missing += 1
        elif isinstance(h, bool) or not isinstance(h, (int, float)) or not math.isfinite(h) or not 0 < h <= 1000:
            invalid += 1
        else:
            heights.append(h)
            if props.get('height_estimated') is not True or not props.get('height_source'):
                raise ValueError(f'{ident}: 缺少估计高度来源标识')
    if invalid:
        raise ValueError(f'{invalid} 个高度无效；未发布')
    if missing != metadata.get('height_missing_count'):
        raise ValueError('高度缺失数量与元数据不一致')
    return {
        'checked_utc': datetime.now(timezone.utc).isoformat(),
        'automated_validation': 'passed', 'feature_count': len(features),
        'polygon_count': polygons, 'height_matched': len(heights), 'height_missing': missing,
        'height_range_m': [min(heights), max(heights)] if heights else None,
        'crs': 'EPSG:4326', 'bounds_wgs84': BOUNDS,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'visual_review': 'requires_manual_review',
        'visual_review_note': '自动校验不证明与卫星影像吻合；需俯视检查偏移、旧村拆迁和新增建筑。',
        'height_note': '模型估计，相对局部地面；不是海拔或测绘高度。',
    }


def run(args):
    if args.check_only:
        report = validate(args.output)
    else:
        # Keep the current served layer intact until download and validation succeed.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.building-pipeline-', dir=args.output.parent) as directory:
            candidate = Path(directory) / 'buildings.geojson'
            prepare(args.cache, candidate, args.workers)
            report = validate(candidate)
            candidate.replace(args.output)
    report.update(data_file=str(args.output.resolve()), preview_url=args.preview_url)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(args.report)
    print(f"\n校验通过：{report['feature_count']} 个要素 / {report['polygon_count']} 个轮廓分量；"
          f"{report['height_matched']} 个高度已匹配，{report['height_missing']} 个缺失。")
    print(f"数据：{args.output.resolve()}\n报告：{args.report.resolve()}")
    print(f"人工核对入口：{args.preview_url}\n选择雄安范围 → 勾选‘仅看轮廓（核对底图）’。")
    print('注意：GBA 主要影像基期为 2019；自动通过不代表当前建筑现状或现场精度达标。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='只校验已有 GeoJSON，不下载、不替换数据')
    parser.add_argument('--cache', type=Path, default=Path('/tmp/gba-xiongan-cache'))
    parser.add_argument('--output', type=Path, default=PLATFORM / 'static/buildings.geojson')
    parser.add_argument('--report', type=Path, default=PLATFORM / 'outputs/building-validation.json')
    parser.add_argument('--workers', type=int, choices=range(1, 13), default=4, metavar='1..12')
    parser.add_argument('--preview-url', default='http://127.0.0.1:8772/network/static/building-preview.html')
    arguments = parser.parse_args()
    try:
        run(arguments)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        parser.exit(1, f'建筑数据处理失败：{error}\n')
