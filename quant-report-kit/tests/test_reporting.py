"""验证客户名称、证据/元信息边界与报告可复核性。"""
import copy
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from test_report import run_fixture
from reportkit.analysis import analyse
from reportkit.exports import add_commentary, export_report, make_snapshot, markdown_report
from reportkit.reporting import validate_report, validate_artifact


class Reporting(unittest.TestCase):
    def test_short_positions_and_negative_targets_rejected(self):
        import pandas as pd
        for table, col in [('weights','weight'),('weights','value'),('targets','target_value'),('selections','within_weight')]:
            r=run_fixture();r.tables[table]=pd.DataFrame([{col:-.1}])
            with self.subTest(table=table,col=col), self.assertRaisesRegex(ValueError,'只做多'):
                analyse({},[r])
        r=run_fixture();r.config['sleeves'][0]['assets'][0]['weight']=-1
        with self.assertRaisesRegex(ValueError,'只做多'):analyse({},[r])

    def test_negative_returns_and_sell_amounts_are_not_short_positions(self):
        import pandas as pd
        r=run_fixture();r.tables['trades']=pd.DataFrame([{'date':'2025-01-03','signal_date':'2025-01-02','code':'001','sleeve':'x','amount':-10,'side':'sell','cost':0}])
        d=analyse({},[r])
        self.assertLess(d['strategies'][0]['metrics']['max_drawdown'],0)
        self.assertTrue(any(x['id']=='long_only' for x in d['disclosures']))

    def test_display_mapping_preserves_original_and_metrics(self):
        r=run_fixture();r.label="朋友示例";r.config['name']='朋友组合教学演示';r.config['sleeves'][0]['name']='股票练习'
        before=copy.deepcopy(r.config)
        d=analyse({'strategies':[{'id':'a','label':'基础配置策略','display':{'sleeves':{'x':'权益配置'}}}]},[r])
        self.assertEqual(r.config,before)
        self.assertAlmostEqual(d['strategies'][0]['metrics']['total_return'],.188)
        self.assertIn('权益配置',[x['name'] for x in d['strategies'][0]['detail']['contributions']])
        self.assertNotIn('朋友',json.dumps(make_snapshot(d),ensure_ascii=False))
        self.assertNotIn('练习',markdown_report(d))

    def test_invalid_client_copy_is_rejected(self):
        r=run_fixture()
        for title in ('三套资产配置策略对比','收益怎样走到今天','策略回测？','朋友示例报告','friend_demo','classroom-demo'):
            with self.assertRaises(ValueError):analyse({'title':title},[r])
        d=analyse({},[r])
        with self.assertRaisesRegex(ValueError,'收益保证'):
            add_commentary(d,{'sections':[{'title':'策略评价','text':'本策略保证盈利','evidence_ids':['F001']}]})

    def test_cost_unknown_is_not_mislabeled_zero(self):
        r=run_fixture();r.config.pop('cost_rate')
        d=analyse({},[r]);cost=next(x['text'] for x in d['disclosures'] if x['id']=='costs')
        self.assertIn('未提供',cost);self.assertNotIn('设为0',cost)
        check=validate_report(d)
        self.assertIn('issuer',check['unprovided_institutional_fields'])
        self.assertNotIn('issuer',d['report'])
        self.assertFalse(check['legal_compliance_certification'])

    def test_source_paths_remain_audit_only(self):
        r=run_fixture();r.path=Path('/confidential/朋友示例')
        d=analyse({},[r]);s=make_snapshot(d)
        self.assertEqual(d['strategies'][0]['source']['directory'],str(r.path))
        self.assertNotIn('/confidential',json.dumps(s,ensure_ascii=False))
        self.assertNotIn('朋友示例',json.dumps(s,ensure_ascii=False))

    def test_same_price_digest_alone_is_not_controlled_experiment(self):
        a,b=run_fixture(),run_fixture('b')
        for r in (a,b):r.documents['manifest']={'price_digest':'same'}
        b.scenario='static'
        self.assertEqual(analyse({},[a,b])['pairs'][0]['comparison_type'],'策略横向比较')
        b.path=a.path
        self.assertEqual(analyse({},[a,b])['pairs'][0]['comparison_type'],'动态权重对照')

    def test_structured_summary_shared_by_outputs(self):
        d=analyse({},[run_fixture()])
        add_commentary(d,{'sections':[{'section':'summary','title':'收益与回撤特征','text':'累计收益18.80%，最大回撤-10.00%。','evidence_ids':['F001']}]})
        self.assertEqual(make_snapshot(d)['quantReport']['summary'],d['summary'])
        self.assertIn(d['summary'][0]['text'],markdown_report(d))
        d['commentary'][0]['evidence_ids']=['F999']
        with self.assertRaisesRegex(ValueError,'事实编号'):validate_report(d)

    def test_missing_required_disclosure_rejected(self):
        d=analyse({},[run_fixture()]);d['disclosures']=[x for x in d['disclosures'] if x['id']!='historical']
        with self.assertRaisesRegex(ValueError,'必需'):validate_report(d)


class ReportFiles(unittest.TestCase):
    def test_file_tamper_detected(self):
        import tempfile
        tmp=Path(os.environ.get('QUANT_REPORT_TEST_TMP',str(Path.home()/'Documents/Codex/temp/quant-report-professional-tests')));tmp.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp) as p:
            p=Path(p);template=p/'template.html';template.write_text('<title>Report</title>__QUANT_REPORT_SNAPSHOT__')
            d=add_commentary(analyse({},[run_fixture()]))
            export_report(d,{},p/'report',template)
            self.assertEqual(validate_artifact(p/'report')['content_checks'],'passed')
            (p/'report/报告解读.md').write_text('modified')
            with self.assertRaisesRegex(ValueError,'校验失败'):validate_artifact(p/'report')


if __name__=='__main__':unittest.main()
