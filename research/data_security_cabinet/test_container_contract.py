import copy
import json
import unittest
from container_contract import example, pack, unpack, TEMPLATE_HASH
from security_cabinet import transform, synthetic_data


class TestContainerContract(unittest.TestCase):
    def test_model_and_stats(self):
        data=example(); data['content']['provenance']['source']='private-source-never-release'
        raw=pack(data)
        model=transform(raw,'dataset'); stats=transform(raw,'stats')
        self.assertEqual(model['quality']['usable_rows'],12)
        self.assertEqual(model['container']['template_sha256'],TEMPLATE_HASH)
        self.assertNotIn('splits',stats)
        self.assertNotIn('private-source-never-release',json.dumps(model)+json.dumps(stats))
        groups=[{r['group_id'] for r in model['splits'][name]} for name in ('train','validation','test')]
        self.assertTrue(all(groups));self.assertFalse(groups[0]&groups[1] or groups[1]&groups[2] or groups[0]&groups[2])
        self.assertEqual(transform(synthetic_data(),'dataset')['quality'],model['quality'])

    def test_contract_rejects_substitution(self):
        for field,value in [('template_id','other'),('template_version',True),('template_sha256','0'*64),('mapper','arbitrary-code')]:
            with self.subTest(field=field):
                data=example();data[field]=value
                with self.assertRaises(ValueError):pack(data)
        data=example();data['template']['samples']['misc']['feature_1']['misc']['unit']='meters'
        with self.assertRaises(ValueError):pack(data)

    def test_invalid_values_and_unknown_fields(self):
        for value in [True,float('nan'),float('inf'),'0.5',None]:
            data=example();data['content']['samples'][0]['feature_1']=value
            with self.subTest(value=value),self.assertRaises(ValueError):pack(data)
        data=example();del data['content']['samples'][0]['group_id']
        with self.assertRaises(ValueError):pack(data)
        data=example();data['content']['download_url']='http://internal/'
        with self.assertRaises(ValueError):pack(data)
        data=example();data['content']['provenance']['data_kind']='field-validated'
        with self.assertRaises(ValueError):pack(data)

    def test_duplicate_keys_and_conflicting_groups(self):
        raw=pack(example()).decode().replace('"template_version":1','"template_version":1,"template_version":1')
        with self.assertRaises(ValueError):unpack(raw.encode())
        data=example();data['content']['samples'][1]=copy.deepcopy(data['content']['samples'][0]);data['content']['samples'][1]['group_id']='another'
        with self.assertRaises(ValueError):transform(pack(data),'dataset')

    def test_model_minimum_groups(self):
        data=example()
        for row in data['content']['samples']:row['group_id']='one'
        with self.assertRaises(ValueError):transform(pack(data),'dataset')
