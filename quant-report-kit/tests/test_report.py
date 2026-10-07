"""验证计算口径、比较边界和输入交接；所有样本均为人工数字。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

KIT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT/"scripts"))
from reportkit.inputs import Run, effective_config, load_request, load_run, validate_daily, write_json
from reportkit.analysis import analyse, performance, macro_text
from reportkit.exports import add_commentary, make_snapshot, render_html, export_report


def run_fixture(sid="a", dates=None, nav=None, capital=100, currency="CNY"):
    dates=dates or ["2025-01-02","2025-01-03","2025-01-06","2025-01-07"]
    nav=[1,1.1,0.99,1.188] if nav is None else nav
    d=pd.DataFrame({"date":pd.to_datetime(dates),"nav":nav})
    d["return"]=d["nav"].pct_change().fillna(0); d["equity"]=d["nav"]*capital
    d["pnl_x"]=d["equity"].diff().fillna(0)*.6
    d["pnl_y"]=d["equity"].diff().fillna(0)*.4
    c={"name":sid,"initial_capital":capital,"base_currency":currency,"cost_rate":0,
       "sleeves":[{"id":"x","name":"甲","weight":.6,"assets":[{"code":"001","name":"甲资产"}],"weighting":{"method":"min_variance","lookback":60}},
                  {"id":"y","name":"乙","weight":.4,"assets":[{"code":"002","name":"乙资产"}],"weighting":{"method":"equal"}}]}
    return Run(sid,sid,Path('/synthetic')/sid,"strategy",d,c,{"base_currency":currency,"initial_capital":capital},{},{},{},[])


class Calculations(unittest.TestCase):
    def test_known_return_drawdown_and_recovery(self):
        d,m=performance(run_fixture().daily)
        self.assertAlmostEqual(m["total_return"],.188)
        self.assertAlmostEqual(m["max_drawdown"],-.1)
        self.assertEqual(m["drawdown_peak"],"2025-01-03")
        self.assertEqual(m["drawdown_trough"],"2025-01-06")
        self.assertEqual(m["recovery_date"],"2025-01-07")
        self.assertAlmostEqual(m["annual_volatility"],np.std([0,.1,-.1,.2],ddof=1)*np.sqrt(252))

    def test_common_period_rebases_and_omits_boundary_pnl(self):
        a=run_fixture();b=run_fixture('b',dates=['2025-01-03','2025-01-06','2025-01-07'],nav=[7,6.3,7.56])
        r=analyse({},[a,b]); self.assertEqual(r['period']['start'],'2025-01-03')
        for s in r['strategies']:
            self.assertAlmostEqual(s['metrics']['total_return'],.08)
            self.assertAlmostEqual(sum(x['contribution'] for x in s['detail']['contributions']),.08)
        self.assertAlmostEqual(r['strategies'][0]['original_metrics']['total_return'],.188)

    def test_different_capital_same_normalized_comparison(self):
        a,b=run_fixture(),run_fixture('b',capital=1_000_000)
        r=analyse({},[a,b]);self.assertEqual(r['pairs'][0]['deltas']['total_return'],0)
        self.assertFalse(any('capital' in d['parameter'] for d in r['pairs'][0]['rule_differences']))

    def test_internal_missing_date_rejected(self):
        b=run_fixture('b',dates=['2025-01-02','2025-01-06','2025-01-07'],nav=[1,.99,1.188])
        with self.assertRaisesRegex(ValueError,'交易日期'):analyse({},[run_fixture(),b])

    def test_no_overlap_rejected(self):
        b=run_fixture('b',dates=['2026-01-01','2026-01-02'],nav=[1,1.1])
        with self.assertRaisesRegex(ValueError,'共同区间'):analyse({},[run_fixture(),b])

    def test_user_subperiod_and_month_compound(self):
        r=analyse({'start':'2025-01-03','end':'2025-01-06'},[run_fixture()])
        self.assertAlmostEqual(r['monthly'][0]['return'],-.1)
        self.assertAlmostEqual(r['annual'][0]['return'],-.1)
        self.assertIsNone(r['strategies'][0]['metrics']['recovery_date'])

    def test_bad_contribution_rejected(self):
        r=run_fixture();r.daily.loc[2,'pnl_x']+=1
        with self.assertRaisesRegex(ValueError,'盈亏'):analyse({},[r])

    def test_missing_contribution_rejected(self):
        r=run_fixture();r.daily.loc[2,'pnl_x']=np.nan
        with self.assertRaisesRegex(ValueError,'空值'):analyse({},[r])

    def test_zero_variance_has_no_infinite_sharpe(self):
        r=analyse({},[run_fixture(nav=[1,1,1,1])]);s=r['strategies'][0]
        self.assertIsNone(s['metrics']['sharpe_zero_rf']);self.assertEqual(s['metrics']['max_drawdown'],0)

    def test_nav_only_works_without_invented_details(self):
        r=run_fixture();r.daily=r.daily[['date','nav']]
        d=analyse({},[r])['strategies'][0]['detail']
        self.assertEqual(d['contributions'],[]);self.assertEqual(d['holdings'],[])

    def test_actual_regimes_counted_in_period(self):
        a=run_fixture();a.config['macro']={'enabled':True};a.documents['regimes']=[
            {'execution_date':'2025-01-02','state':'基础配置'}, {'execution_date':'2025-01-06','state':'波动偏高'}, {'execution_date':'2026-01-01','state':'基础配置'}]
        r=analyse({},[a]);f=[f for f in r['facts'] if '检查配置' in f['text']][0]
        self.assertIn('2次',f['text']);self.assertIn('波动偏高1次',f['text'])

    def test_input_dates_and_returns_validated(self):
        base=run_fixture().daily
        for altered in [pd.concat([base,base.iloc[:1]]),base.assign(date=[None,*base['date'][1:]]),base.assign(nav=[1,0,1,1]),base.assign(**{'return':[0,0,0,0]})]:
            with self.assertRaises(ValueError):validate_daily(altered,'test')

    def test_macro_any_and_missing_hold_explained(self):
        text=macro_text({'enabled':True,'missing':'hold','rules':[{'any':[{'series':'vix','op':'>','threshold':20}],'weights':{}}]}, {})
        self.assertIn('至少满足一项',text);self.assertIn('保留上次配置',text)

    def test_weight_amount_mismatch_rejected(self):
        r=run_fixture();r.tables['weights']=pd.DataFrame({'date':r.daily['date'],'sleeve':'x','code':'001','weight':1.,'value':100.})
        with self.assertRaisesRegex(ValueError,'持仓金额'):analyse({},[r])

    def test_incorrect_weights_total_rejected(self):
        r=run_fixture();r.tables['weights']=pd.DataFrame({'date':r.daily['date'],'sleeve':'x','code':'001','weight':.9})
        with self.assertRaisesRegex(ValueError,'合计'):analyse({},[r])


class Files(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ.get('QUANT_REPORT_TEST_TMP', str(Path.home()/'Documents/Codex/temp/quant-report-kit-tests')))
        root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.path=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def save_run(self,sid='a',currency='CNY'):
        r=run_fixture(sid,currency=currency);p=self.path/sid;p.mkdir()
        r.daily.to_csv(p/'daily.csv',index=False)
        write_json(p/'strategy.json',r.config)
        write_json(p/'handoff.json',{'schema_version':1,'strategy_name':sid,'base_currency':currency,'initial_capital':100,'scenarios':[{'id':'strategy','label':sid}]})
        return p

    def test_currency_mismatch_rejected(self):
        a,b=self.save_run(),self.save_run('b','USD');p=self.path/'config.json'
        write_json(p,{'schema_version':1,'strategies':[{'id':'a','result_dir':str(a)},{'id':'b','result_dir':str(b)}]})
        with self.assertRaisesRegex(ValueError,'币种'):load_request(p)

    def test_alternative_does_not_borrow_primary_details(self):
        p=self.save_run();h=json.loads((p/'handoff.json').read_text());h['scenarios'].append({'id':'equal_weight','label':'等权'});write_json(p/'handoff.json',h)
        (p/'equal_weight_daily.csv').write_bytes((p/'daily.csv').read_bytes())
        pd.DataFrame([{'date':'2025-01-02','weight':1,'code':'001'}]).to_csv(p/'weights.csv',index=False)
        r=load_run({'id':'equal','result_dir':str(p),'scenario':'equal_weight'},self.path)
        self.assertEqual(r.tables,{});self.assertEqual(r.config['sleeves'][0]['weighting']['method'],'equal')

    def test_handoff_shape_mismatch_rejected(self):
        p=self.save_run();h=json.loads((p/'handoff.json').read_text());h['files']=[{'path':'daily.csv','rows':999}];write_json(p/'handoff.json',h)
        with self.assertRaisesRegex(ValueError,'清单'):load_run({'id':'a','result_dir':str(p)},self.path)

    def test_conflicting_currency_metadata_rejected(self):
        p=self.save_run();h=json.loads((p/'handoff.json').read_text());h['base_currency']='USD';write_json(p/'handoff.json',h)
        with self.assertRaisesRegex(ValueError,'币种不一致'):load_run({'id':'a','result_dir':str(p)},self.path)

    def test_evidence_references_required(self):
        d=analyse({},[run_fixture()])
        with self.assertRaisesRegex(ValueError,'evidence'):add_commentary(d,{'sections':[{'title':'解释','text':'测试','evidence_ids':['F999']}]})
        add_commentary(d,{'sections':[{'title':'解释','text':'收益和回撤来自历史结果。','evidence_ids':['F001']}]})

    def test_html_payload_safe_and_recovers_exactly(self):
        d=add_commentary(analyse({'title':'</script><script>alert(1)</script>'},[run_fixture()]))
        snapshot=make_snapshot(d);s=render_html(snapshot,KIT/'assets/report-template.html')
        block=re.search(r'<script type="application/json" id="data-app-reviewed-snapshot">(.*?)</script>',s,re.S)[1]
        self.assertNotIn('<script>',block);self.assertEqual(json.loads(block),snapshot)
        self.assertNotIn('__QUANT_REPORT_SNAPSHOT__',s)
        self.assertNotIn('name="data-app-local-thread"',s)
        digest=re.search(r'name="data-app-snapshot-sha256" content="([a-f0-9]+)"',s)[1]
        self.assertEqual(digest,hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest())

    def test_export_keeps_source_and_roundtrips(self):
        p=self.save_run();before={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir()}
        r=load_run({'id':'a','result_dir':str(p)},self.path);d=add_commentary(analyse({},[r]));out=self.path/'report'
        export_report(d,{},out,KIT/'assets/report-template.html')
        self.assertEqual(before,{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir()})
        saved=json.loads((out/'report_data.json').read_text());self.assertEqual(saved['period'],d['period'])
        with self.assertRaisesRegex(ValueError,'已有'):export_report(d,{},out,KIT/'assets/report-template.html')

    def test_cli_import_external_nav_and_generate_single(self):
        source=self.path
        pd.DataFrame({'day':['2025-01-01','2025-01-02','2025-01-03'],'unit':[2,2.1,1.9]}).to_csv(source/'nav.csv',index=False)
        args=[sys.executable,str(KIT/'scripts/run.py')]
        a=subprocess.run(args+['import-nav','--csv',str(source/'nav.csv'),'--date-column','day','--nav-column','unit','--currency','CNY','--name','外部策略','--out',str(self.path/'adapted')],capture_output=True,text=True)
        self.assertEqual(a.returncode,0,a.stderr)
        b=subprocess.run(args+['single','--result',str(self.path/'adapted'),'--out',str(self.path/'report')],capture_output=True,text=True)
        self.assertEqual(b.returncode,0,b.stderr)
        d=json.loads((self.path/'report/report_data.json').read_text());self.assertAlmostEqual(d['strategies'][0]['metrics']['total_return'],-.05)


if __name__=='__main__':unittest.main()
