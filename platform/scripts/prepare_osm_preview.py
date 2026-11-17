"""Convert an OSM API map XML into a clipped, attributed building preview."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, LineString, box, mapping
from shapely.ops import polygonize, unary_union


def convert(xml_path, bounds):
    root = ET.parse(xml_path).getroot()
    nodes = {n.get('id'): (float(n.get('lon')), float(n.get('lat'))) for n in root.findall('node')}
    ways = {w.get('id'): w for w in root.findall('way')}
    tags = lambda obj: {t.get('k'): t.get('v') for t in obj.findall('tag')}
    building = lambda obj: tags(obj).get('building') not in (None, 'no')
    points = lambda way: [nodes[n.get('ref')] for n in way.findall('nd')]
    region = box(*bounds)
    features, used, skipped = [], set(), 0

    def append(geom, obj, kind):
        if not geom.is_valid:
            geom = geom.buffer(0)
        geom = geom.intersection(region)
        if geom.is_empty or geom.geom_type not in ('Polygon', 'MultiPolygon'):
            return
        props = tags(obj)
        props.update(dataset='OpenStreetMap', footprint_license='ODbL-1.0')
        features.append({'type': 'Feature', 'id': f'osm:{kind}/{obj.get("id")}',
                         'properties': props, 'geometry': mapping(geom)})

    for relation in root.findall('relation'):
        if not building(relation) or tags(relation).get('type') != 'multipolygon':
            continue
        try:
            members = [m for m in relation.findall('member') if m.get('type') == 'way']
            outer = [LineString(points(ways[m.get('ref')])) for m in members if m.get('role') in ('outer', '')]
            inner = [LineString(points(ways[m.get('ref')])) for m in members if m.get('role') == 'inner']
            geom = unary_union(list(polygonize(outer)))
            if inner:
                geom = geom.difference(unary_union(list(polygonize(inner))))
            append(geom, relation, 'relation')
            used.update(m.get('ref') for m in members)
        except (KeyError, ValueError):
            skipped += 1
    for ident, way in ways.items():
        if ident in used or not building(way):
            continue
        try:
            ring = points(way)
            if len(ring) < 4 or ring[0] != ring[-1]:
                skipped += 1
                continue
            append(Polygon(ring), way, 'way')
        except (KeyError, ValueError):
            skipped += 1
    return {'type': 'FeatureCollection', 'metadata': {
        'source': 'https://www.openstreetmap.org/copyright', 'attribution': '© OpenStreetMap contributors',
        'license': 'ODbL-1.0', 'region': '北京国贸（对照展示，非雄安）',
        'bounds_wgs84': bounds, 'output_crs': 'EPSG:4326',
        'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'incomplete_geometries_skipped': skipped,
        'source_url': 'https://api.openstreetmap.org/api/0.6/map?bbox=' + ','.join(map(str, bounds)),
        'height_note': 'OSM 标签未经测绘核验；缺失高度按楼层×3m或9m示意。'}, 'features': features}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bounds', type=float, nargs=4, default=[116.452, 39.901, 116.468, 39.916])
    args = parser.parse_args()
    data = convert(args.xml, args.bounds)
    args.output.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    print(f"Saved {len(data['features'])} footprints; skipped {data['metadata']['incomplete_geometries_skipped']} incomplete geometries")
