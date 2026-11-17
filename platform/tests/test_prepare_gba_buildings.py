"""Offline geometry/stream boundary tests; run with a Python containing shapely."""
import importlib.util
import json
from pathlib import Path
import unittest

if importlib.util.find_spec("shapely") is None:
    raise unittest.SkipTest("Optional building-data tools require requirements-geodata.txt")

spec = importlib.util.spec_from_file_location("prepare_gba", Path(__file__).parents[1] / "scripts/prepare_gba_buildings.py")
gba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gba)


def feature(bounds, ident="test"):
    west, south, east, north = bounds
    ring = [gba.project(x, y) for x, y in [(west, south), (east, south), (east, north), (west, north), (west, south)]]
    return {"type": "Feature", "properties": {"source": "ours", "id": ident, "region": "CHN"},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def line(feat):
    return (json.dumps(feat).replace('{"type": "Feature",', '{ "type": "Feature",', 1) + ',\n').encode()


class GBATests(unittest.TestCase):
    def test_projection_roundtrip_and_known_mercator_origin(self):
        self.assertAlmostEqual(gba.project(0, 0)[0], 0)
        self.assertAlmostEqual(gba.project(0, 0)[1], 0)
        for point in [(115.98, 39.05), (115.95, 39.0)]:
            actual = gba.unproject(*gba.project(*point))
            for a, b in zip(point, actual):
                self.assertAlmostEqual(a, b, places=10)

    def test_clip_to_extent_and_reject_outside(self):
        self.assertIsNone(gba.clip_feature(feature([116.5, 39.5, 116.501, 39.501])))
        clipped = gba.clip_feature(feature([115.974, 39.05, 115.981, 39.051]))
        self.assertEqual(clipped['id'], 'ourstestCHN')
        for x, y in clipped['geometry']['coordinates'][0]:
            self.assertGreaterEqual(x, 115.975 - 1e-10)
            self.assertLessEqual(x, 115.981 + 1e-10)

    def test_polygon_crossing_range_boundary_is_read_exactly_once(self):
        data = line(feature([115.98, 39.05, 115.981, 39.051], 'a')) + line(feature([115.98, 39.05, 115.981, 39.051], 'b'))
        boundary = 180
        records = gba.parse_shard(data, 'polygon', set(), boundary)
        records += gba.parse_shard(data[boundary:], 'polygon', set(), len(data))
        self.assertEqual([r['id'] for r in records], ['oursaCHN', 'oursbCHN'])

    def test_height_crossing_boundary_and_id_filter(self):
        data = b'{"oursaCHN": {"height": 12.5, "var": 1.2}, "oursbCHN": {"height": 15.1, "var": 2}, "other": {"height": 99}}'
        keys = {'oursaCHN', 'oursbCHN'}
        values = gba.parse_shard(data, 'height', keys, 20) + gba.parse_shard(data[20:], 'height', keys)
        self.assertEqual(dict(values), {'oursaCHN': {'height': 12.5, 'var': 1.2}, 'oursbCHN': {'height': 15.1, 'var': 2}})


if __name__ == '__main__':
    unittest.main()
