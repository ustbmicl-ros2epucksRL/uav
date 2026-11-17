import importlib.util
from pathlib import Path
import tempfile
import unittest

if importlib.util.find_spec('shapely') is None:
    raise unittest.SkipTest('Optional building-data tools require requirements-geodata.txt')
spec = importlib.util.spec_from_file_location('osm_preview', Path(__file__).parents[1] / 'scripts/prepare_osm_preview.py')
osm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(osm)


class OSMPreviewTests(unittest.TestCase):
    def test_multipolygon_courtyard_member_dedup_and_missing_nodes(self):
        coordinates = [(1, 1), (9, 1), (9, 9), (1, 9), (3, 3), (7, 3), (7, 7), (3, 7)]
        xml = '<osm>' + ''.join(f'<node id="{i}" lon="{x}" lat="{y}"/>' for i, (x, y) in enumerate(coordinates, 1))
        for ident, refs in [(10, [1, 2, 3, 4, 1]), (11, [5, 6, 7, 8, 5]), (12, [1, 2, 999, 1])]:
            xml += f'<way id="{ident}">' + ''.join(f'<nd ref="{r}"/>' for r in refs) + '<tag k="building" v="yes"/></way>'
        xml += '<relation id="20"><member type="way" ref="10" role="outer"/><member type="way" ref="11" role="inner"/><tag k="type" v="multipolygon"/><tag k="building" v="yes"/><tag k="height" v="12"/></relation></osm>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'map.xml'
            path.write_text(xml)
            result = osm.convert(path, [0, 0, 10, 10])
        self.assertEqual(len(result['features']), 1)
        self.assertEqual(result['features'][0]['id'], 'osm:relation/20')
        self.assertEqual(len(result['features'][0]['geometry']['coordinates']), 2)
        self.assertEqual(result['features'][0]['properties']['height'], '12')
        self.assertEqual(result['metadata']['incomplete_geometries_skipped'], 1)


if __name__ == '__main__':
    unittest.main()
