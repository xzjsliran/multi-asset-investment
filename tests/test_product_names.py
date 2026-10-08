"""分发内容的投资研究命名规则。"""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shared_names', ROOT/'multi-asset-investment/scripts/build_folder.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class ProductNames(unittest.TestCase):
    def test_rejects_internal_paths_and_nested_labels(self):
        for path in ['assets/friend-demo.json', 'assets/classroom_demo.json', 'references/课堂指南.md']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                bundle.validate_product_names(path, b'{}')
        for name in ['friend qdii demo', 'classroom_demo', '朋友组合', '增长与通胀四状态讨论示例']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.validate_product_names('assets/allocation.json', json.dumps({'sleeves':[{'name': name}]}))

    def test_accepts_investment_names_and_format_examples(self):
        bundle.validate_product_names('assets/macro-cycle.example.json', json.dumps({'name':'增长与通胀四状态配置策略'}))
        bundle.validate_product_names('assets/request.schema.json', json.dumps({'properties':{'mode':{'enum':['live','simulation']}}}))


if __name__ == '__main__':
    unittest.main()
