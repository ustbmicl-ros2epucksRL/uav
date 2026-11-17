import importlib.util
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if importlib.util.find_spec('shapely') is None:
    raise unittest.SkipTest('Optional building-data tools require requirements-geodata.txt')
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import building_pipeline as pipeline


def sample():
    return {'type': 'FeatureCollection', 'metadata': {'output_crs': 'EPSG:4326', 'bounds_wgs84': pipeline.BOUNDS, 'height_missing_count': 0},
            'features': [{'type': 'Feature', 'id': 'test', 'properties': {'height': 6, 'height_estimated': True, 'height_source': 'test estimate'},
                          'geometry': {'type': 'Polygon', 'coordinates': [[[115.98, 39.05], [115.981, 39.05], [115.981, 39.051], [115.98, 39.05]]]}}]}


class PipelineTests(unittest.TestCase):
    def audit(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.geojson'
            path.write_text(json.dumps(data))
            return pipeline.validate(path)

    def test_valid_data_requires_separate_visual_review(self):
        report = self.audit(sample())
        self.assertEqual(report['height_matched'], 1)
        self.assertEqual(report['visual_review'], 'requires_manual_review')

    def test_reject_duplicate_ids_and_wrong_coordinates(self):
        data = sample()
        data['features'] *= 2
        with self.assertRaisesRegex(ValueError, '重复'):
            self.audit(data)
        data = sample()
        data['features'][0]['geometry']['coordinates'] = [[[10, 10], [11, 10], [11, 11], [10, 10]]]
        with self.assertRaisesRegex(ValueError, '超出'):
            self.audit(data)

    def test_invalid_height_and_missing_provenance_are_rejected(self):
        data = sample()
        data['features'][0]['properties']['height'] = -1
        with self.assertRaisesRegex(ValueError, '高度无效'):
            self.audit(data)
        data = sample()
        data['features'][0]['properties']['height_estimated'] = False
        with self.assertRaisesRegex(ValueError, '来源标识'):
            self.audit(data)

    def test_failed_download_preserves_published_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'published.geojson'
            output.write_text('existing layer')
            args = SimpleNamespace(check_only=False, output=output, cache=Path(directory) / 'cache', workers=1)
            with patch.object(pipeline, 'prepare', side_effect=RuntimeError('download interrupted')):
                with self.assertRaises(RuntimeError):
                    pipeline.run(args)
            self.assertEqual(output.read_text(), 'existing layer')


if __name__ == '__main__':
    unittest.main()
