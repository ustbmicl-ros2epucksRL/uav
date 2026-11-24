"""Load real WGS84 GeoJSON building footprints into the 2000m × 2000m local ENU scene frame.

Unifies visual 3D polygon extrusion and backend 3D ray-box occlusion calculation so that
what is displayed in the Three.js Digital Twin matches what coverage_core computes.
"""
import copy
import functools
import json
import math

from . import store

EARTH_M = 6378137.0
RAD = math.pi / 180.0

SCENE_ANCHORS = {
    'urban': dict(latitude=39.0510, longitude=115.9810, label='雄安新区 · 高密度建筑片区锚点'),
    'corridor': dict(latitude=39.0015, longitude=115.9515, label='雄安新区 · 物流主干廊道锚点'),
    'electromagnetic': dict(latitude=39.0485, longitude=115.9865, label='雄安新区 · 复杂电磁共址区锚点'),
}
BEIJING_ANCHOR = dict(latitude=39.9015, longitude=116.4520, label='北京国贸 · 高层建筑群实测轮廓对照锚点')

BUILDING_SOURCES = {
    'synthetic': dict(
        id='synthetic',
        label='新区规划高密建筑模型（25–105 m）',
        note='固定随机种子生成的参数化高层建筑群（与申报书基准算例一致）',
    ),
    'xiongan_gba': dict(
        id='xiongan_gba',
        label='雄安真实建筑轮廓（GlobalBuildingAtlas）',
        note='裁剪自 GlobalBuildingAtlas 雄安片区真实多边形轮廓（2019 影像基期模型估计高度，非测绘数据）',
    ),
    'beijing_osm': dict(
        id='beijing_osm',
        label='高层摩天楼群真实轮廓（OSM 对照）',
        note='OpenStreetMap 真实多边形高层建筑轮廓与楼高标注，用于极端城市峡谷遮挡对照（未测绘核验）',
    ),
}


def to_local_enu(lon, lat, anchor_lat, anchor_lon):
    """Exact same local tangent projection as static/imagery-ground.js localFrame()."""
    scale_e = EARTH_M * math.cos(anchor_lat * RAD) * RAD
    east = (lon - anchor_lon) * scale_e + 1000.0
    north = (lat - anchor_lat) * EARTH_M * RAD + 1000.0
    return east, north


def _parse_height(props):
    raw = props.get('height')
    if raw is not None and raw != '':
        try:
            val = float(str(raw).lower().replace('m', '').strip())
            if 0 < val <= 1000:
                return val, bool(props.get('height_estimated', False))
        except ValueError:
            pass
    levels = props.get('building:levels') or props.get('levels')
    if levels is not None and levels != '':
        try:
            val = float(levels) * 3.0
            if 0 < val <= 1000:
                return val, True
        except ValueError:
            pass
    return 9.0, True


@functools.lru_cache(maxsize=8)
def load_real_layer(source_id, scene_id):
    if source_id == 'beijing_osm':
        filename = 'buildings-beijing.geojson'
        anchor = dict(BEIJING_ANCHOR)
        # Center the 269 CBD high-rises around the 2km × 2km [0, 2000] domain center (1000, 1000)
        center_lat, center_lon = 39.9085, 116.4600
    else:
        filename = 'buildings.geojson'
        base_anchor = SCENE_ANCHORS.get(scene_id, SCENE_ANCHORS['urban'])
        anchor = dict(base_anchor)
        center_lat = base_anchor['latitude'] + 0.008
        center_lon = base_anchor['longitude'] + 0.010

    path = store.ROOT / 'static' / filename
    if not path.is_file():
        return dict(anchor=anchor, buildings=[], footprints=[], total_raw=0)

    collection = json.loads(path.read_text())
    candidates = []
    for index, feature in enumerate(collection.get('features', [])):
        geom = feature.get('geometry') or {}
        gtype = geom.get('type')
        if gtype not in ('Polygon', 'MultiPolygon'):
            continue
        props = feature.get('properties') or {}
        height, estimated = _parse_height(props)
        polygons = [geom['coordinates']] if gtype == 'Polygon' else geom['coordinates']
        for poly_idx, rings in enumerate(polygons):
            if not rings or not rings[0]:
                continue
            outer = [to_local_enu(pt[0], pt[1], center_lat, center_lon) for pt in rings[0]]
            xs = [pt[0] for pt in outer]
            ys = [pt[1] for pt in outer]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if x1 < 40 or x0 > 1960 or y1 < 40 or y0 > 1960:
                continue
            cx0 = max(0.0, round(x0, 1))
            cy0 = max(0.0, round(y0, 1))
            cw = round(min(2000.0, x1) - cx0, 1)
            cd = round(min(2000.0, y1) - cy0, 1)
            if cw < 4.0 or cd < 4.0:
                continue
            local_rings = [
                [[round(px, 1), round(py, 1)] for px, py in (to_local_enu(pt[0], pt[1], center_lat, center_lon) for pt in ring)]
                for ring in rings[:4] if len(ring) >= 4
            ]
            if not local_rings:
                continue
            volume_score = cw * cd * height
            candidates.append((
                volume_score,
                dict(x=cx0, y=cy0, w=cw, d=cd, h=round(height, 1)),
                dict(id=str(feature.get('id') or f'{index}-{poly_idx}'),
                     name=str(props.get('name') or f'建筑 {index + 1}'),
                     h=round(height, 1), estimated=estimated, rings=local_rings),
            ))

    candidates.sort(key=lambda item: -item[0])
    # Keep top 160 buildings by volume for fast vectorized ray-box occlusion while preserving dominant blockers
    chosen = candidates[:160]
    return dict(
        anchor=dict(latitude=round(center_lat, 6), longitude=round(center_lon, 6), label=anchor['label']),
        buildings=[item[1] for item in chosen],
        footprints=[item[2] for item in chosen],
        total_raw=len(candidates),
    )


def anchor_for(scene_id, building_source='synthetic'):
    if building_source in ('xiongan_gba', 'beijing_osm'):
        return load_real_layer(building_source, scene_id)['anchor']
    return dict(SCENE_ANCHORS.get(scene_id, SCENE_ANCHORS['urban']))


def apply_building_source(scene, building_source='synthetic'):
    """Return a scene copy configured with the requested building_source without mutating stored scenes."""
    if building_source == 'synthetic':
        return scene
    if building_source not in BUILDING_SOURCES:
        raise ValueError('不支持的建筑数据来源')
    adapted = copy.deepcopy(scene)
    layer = load_real_layer(building_source, scene['id'])
    if layer['buildings']:
        adapted['buildings'] = layer['buildings']
    adapted['building_source'] = building_source
    adapted['building_source_label'] = BUILDING_SOURCES[building_source]['label']
    adapted['building_source_note'] = BUILDING_SOURCES[building_source]['note']
    adapted['geographic_anchor'] = layer['anchor']
    return adapted
