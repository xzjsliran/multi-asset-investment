"""人工样本验证用户确认、时点、长仓、分币种现金与持仓还原。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for kit in ["quant-rebalance-kit", "quant-strategy-kit", "quant-report-kit"]:
    sys.path.insert(0, str(ROOT/kit/"scripts"))

from strategykit.config import validate
from strategykit.data import Dataset
from strategykit.engine import backtest
from rebalancekit.inputs import review
from rebalancekit.planning import make_plan
from rebalancekit.exports import export_plan
from rebalancekit.quotes import extract_quotes
from reportkit.rebalance import validate_plan, export_rebalance
from reportkit.reporting import validate_artifact


def fixture():
    days = pd.bdate_range("2024-10-01", "2025-08-01")
    prices = pd.DataFrame({"A": 100., "B": 100., "C": 100.}, index=days)
    data = Dataset(prices, prices.notna(), days, pd.DataFrame(), pd.DataFrame(), {"source": "synthetic"})
    c = validate({"schema_version": 1, "name": "固定比例资产配置策略", "start": "2025-01-01", "end": "2025-07-31",
                  "initial_capital": 1000, "base_currency": "CNY", "cost_rate": 0, "allocation_rebalance": "monthly",
                  "sleeves": [{"id": sid, "name": sid, "weight": w, "rebalance": "monthly", "assets": [{"code": code, "kind": "etf"}],
                               "selection": {"method": "fixed"}, "weighting": {"method": "equal"}} for sid, code, w in [("equity", "A", .5), ("bond", "B", .5)]]})
    asof = "2025-06-30T16:00:00+08:00"
    account = {"schema_version": 1, "source": "simulation", "as_of": asof,
               "positions": [{"sleeve": "equity", "code": "A", "quantity": 80., "available_quantity": 80.},
                             {"sleeve": "bond", "code": "B", "quantity": 20., "available_quantity": 20.}],
               "cash": [{"sleeve": "cash", "currency": "CNY", "amount": 0.}]}
    request = {"schema_version": 1, "mode": "demo", "as_of": asof, "execution_date": "2025-07-01"}
    quotes = {"schema_version": 1, "fx": [], "quotes": [{"code": code, "price": 10., "price_basis": "raw", "currency": "CNY", "available_at": asof,
              "buy_lot": 1., "sell_lot": 1., "tradable": True, "source": "synthetic"} for code in ["A", "B", "C"]]}
    return c, data, account, request, quotes


class RebalanceRules(unittest.TestCase):
    def test_risk_parity_plan_reuses_historical_decision_and_discloses_estimate(self):
        c,d,a,r,q=fixture()
        rng=np.random.default_rng(21)
        d.prices[['A','B']]=100*np.exp(np.cumsum(rng.normal(0,[.01,.003],(len(d.prices),2)),axis=0))
        c['allocation']={'method':'risk_parity','proxies':{'equity':[{'code':'A','kind':'etf'}],'bond':[{'code':'B','kind':'etf'}]}}
        c=validate(c);history=backtest(c,d)
        p=make_plan(c,d,a,r,q)
        expected=next(x for x in history['regimes'] if x['execution_date']=='2025-07-01')
        self.assertEqual(p['regime']['weights'],expected['weights'])
        np.testing.assert_allclose(p['regime']['risk_shares'],[.5,.5],atol=1e-5)
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'report';plan_dir=Path(temp)/'plan'
            export_plan(p,c,plan_dir)
            export_rebalance(plan_dir,out)
            self.assertIn('目标配置风险估计',(out/'报告.html').read_text())
        c['allocation']['fallback']='hold'
        with self.assertRaisesRegex(ValueError,'previous_allocation'):make_plan(c,d,a,r,q)

    def test_raw_quotes_determine_real_quantities_not_adjusted_prices(self):
        p = make_plan(*fixture())
        self.assertEqual([(x['code'], x['side'], x['quantity']) for x in p['orders']], [('A','sell',30), ('B','buy',30)])
        self.assertEqual(p['account']['equity_cny'], 1000)
        self.assertEqual(validate_plan(p)['content_checks'], 'passed')

    def test_live_requires_actual_user_confirmation(self):
        c,d,a,r,q = fixture(); a['source']='actual'; r['mode']='live'
        with self.assertRaisesRegex(ValueError, '确认'):
            make_plan(c,d,a,r,q)
        info = review(c,a,r)
        approved = {**info['confirmation_template'], 'confirmed': True, 'confirmed_at':r['as_of'], 'user_statement':'确认以上策略及持仓，请计算。'}
        p = make_plan(c,d,a,r,q,approved)
        self.assertTrue(p['confirmation']['actual_account_confirmed'])
        for changed in ['holdings','strategy','time']:
            cc,aa,rr = copy.deepcopy((c,a,r))
            if changed=='holdings': aa['positions'][0]['quantity']+=1
            elif changed=='strategy': cc['sleeves'][0]['rebalance']='weekly'
            else: rr['execution_date']='2025-07-02'
            with self.assertRaisesRegex(ValueError,'不一致'):
                make_plan(cc,d,aa,rr,q,approved)

    def test_simulation_cannot_be_used_as_live_holdings(self):
        c,d,a,r,q = fixture(); r['mode']='live'
        with self.assertRaisesRegex(ValueError,'实际持仓'):
            make_plan(c,d,a,r,q)
        c,d,a,r,q = fixture();a['source']='actual'
        with self.assertRaisesRegex(ValueError,'先完成用户确认'):
            make_plan(c,d,a,r,q)

    def test_all_negative_asset_paths_rejected(self):
        for location in ['holding','available','cash','fixed','sleeve','macro','short','nan']:
            c,d,a,r,q=fixture()
            if location=='holding': a['positions'][0]['quantity']=-1
            elif location=='available': a['positions'][0]['available_quantity']=-1
            elif location=='cash': a['cash'][0]['amount']=-1
            elif location=='fixed': c['sleeves'][0]['assets'][0]['weight']=-.2
            elif location=='sleeve': c['sleeves'][0]['weight']=-.1
            elif location=='macro': c['macro']={'enabled':True,'rules':[{'name':'异常','all':[{'series':'vix','op':'>','threshold':20}],'weights':{'equity':-.1,'bond':1.1}}]}
            elif location=='short': c['allow_short']=True
            else: c['sleeves'][0]['assets'][0]['weight']=float('nan')
            with self.subTest(location=location), self.assertRaises(ValueError):
                make_plan(c,d,a,r,q)

    def test_locked_positions_do_not_create_short_or_cash_overdraft(self):
        c,d,a,r,q=fixture(); a['positions'][0]['available_quantity']=5
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['orders'][0]['quantity'],5)
        self.assertEqual(p['orders'][1]['quantity'],5)
        self.assertEqual(p['holdings'][0]['projected_quantity'],75)
        self.assertGreater(len(p['issues']),0)
        validate_plan(p)

    def test_foreign_cash_is_separate_and_never_implicitly_converted(self):
        c,d,a,r,q=fixture()
        q['quotes'][1]['currency']='USD';q['quotes'][1]['price']=2
        q['fx']=[{'currency':'USD','rate_to_cny':5.,'available_at':r['as_of']}]
        p=make_plan(c,d,a,r,q)
        self.assertTrue(all(x['code']!='B' for x in p['orders']))
        self.assertEqual(p['account']['cash_after_cny'],300)
        self.assertTrue(any('USD' in x['reason'] for x in p['issues']))
        self.assertEqual(p['funding_gaps'][0]['shortfall_native'],60)
        validate_plan(p)

    def test_whole_lots_reserve_remaining_cash(self):
        c,d,a,r,q=fixture()
        q['quotes'][1]['buy_lot']=20
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['orders'][1]['quantity'],20)
        self.assertEqual(p['account']['cash_after_cny'],100)
        validate_plan(p)

    def test_shared_security_nets_to_zero_external_order(self):
        c,d,a,r,q=fixture()
        c['sleeves'][1]['assets'][0]['code']='A'; a['positions'][1]['code']='A'
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['orders'],[])
        self.assertEqual(len(p['allocation_legs']),2)
        self.assertEqual([x['projected_quantity'] for x in p['holdings']],[50,50])
        validate_plan(p)

    def test_weekly_internal_event_preserves_bond_units_and_cash(self):
        c,d,a,r,q=fixture()
        c['allocation_rebalance']='quarterly'
        for s in c['sleeves']: s['rebalance']='quarterly'
        c['sleeves'][0]['rebalance']='weekly';c['sleeves'][0]['assets'].append({'code':'C','kind':'etf'})
        r['as_of']=a['as_of']='2025-07-04T16:00:00+08:00';r['execution_date']='2025-07-07'
        for quote in q['quotes']: quote['available_at']=r['as_of']
        p=make_plan(c,d,a,r,q)
        self.assertFalse(p['event']['is_outer'])
        self.assertEqual(p['event']['participating_sleeves'],['equity'])
        self.assertEqual(next(x for x in p['holdings'] if x['code']=='B')['projected_quantity'],20)
        self.assertFalse(any(x['code']=='B' for x in p['orders']))
        validate_plan(p)

    def test_off_cycle_returns_hold(self):
        c,d,a,r,q=fixture();r['execution_date']='2025-07-02'
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['action'],'hold');self.assertEqual(p['orders'],[])
        self.assertEqual(p['account']['cash_before_cny'],p['account']['cash_after_cny'])

    def test_future_month_end_is_preliminary_and_future_prices_invisible(self):
        c,d,a,r,q=fixture();r['execution_date']='2025-08-01'
        c['sleeves'][0]['assets'].append({'code':'C','kind':'etf'})
        c['sleeves'][0]['weighting']={'method':'min_variance','lookback':40,'min_observations':20}
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['status'],'preliminary')
        self.assertEqual(p['timing']['signal_date'],'2025-06-30')
        changed=copy.deepcopy(d);changed.prices.loc['2025-07-01':,'A']*=1000
        p2=make_plan(c,changed,a,r,q)
        self.assertEqual(p['targets'],p2['targets']);self.assertEqual(p['orders'],p2['orders'])

    def test_future_macro_release_is_invisible(self):
        c,d,a,r,q=fixture()
        c['macro']={'enabled':True,'rebalance':'monthly','rules':[{'name':'高波动','all':[{'series':'vix','op':'>','threshold':20}],'weights':{'equity':.2,'bond':.8}}]}
        d.macro=pd.DataFrame([{'series':'vix','period':'2025-06-30','value':100.,'available_at':pd.Timestamp('2025-07-01T10:00:00+08:00'),'signal_eligible':True}])
        p=make_plan(c,d,a,r,q)
        self.assertTrue(p['regime']['missing'])
        self.assertEqual(p['regime']['weights'],{'equity':.5,'bond':.5})

    def test_expired_calendar_does_not_invent_future_sessions(self):
        c,d,a,r,q=fixture();d.calendar=d.calendar[d.calendar<='2025-06-30']
        with self.assertRaisesRegex(ValueError,'日历'):
            make_plan(c,d,a,r,q)

    def test_initial_allocation_is_explicit(self):
        c,d,a,r,q=fixture();a['positions']=[];a['cash'][0]['amount']=1000
        with self.assertRaisesRegex(ValueError,'initial_allocation'):
            make_plan(c,d,a,r,q)
        r['initial_allocation']=True
        p=make_plan(c,d,a,r,q)
        self.assertEqual(len(p['orders']),2);validate_plan(p)

    def test_stale_or_future_quotes_cannot_be_executable(self):
        c,d,a,r,q=fixture();q['quotes'][0]['available_at']='2025-06-01T16:00:00+08:00'
        p=make_plan(c,d,a,r,q)
        self.assertFalse(any(x['code']=='A' for x in p['orders']))
        q['quotes'][0]['available_at']='2025-07-01T16:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'晚于'):
            make_plan(c,d,a,r,q)

    def test_adjusted_price_cannot_masquerade_as_order_price(self):
        c,d,a,r,q=fixture();q['quotes'][0]['price_basis']='adjusted'
        with self.assertRaisesRegex(ValueError,'复权'):
            make_plan(c,d,a,r,q)

    def test_macro_hold_requires_prior_state(self):
        c,d,a,r,q=fixture()
        c['macro']={'enabled':True,'missing':'hold','rules':[{'name':'高波动','all':[{'series':'vix','op':'>','threshold':20}],'weights':{'equity':.2,'bond':.8}}]}
        with self.assertRaisesRegex(ValueError,'previous_allocation'):
            make_plan(c,d,a,r,q)
        a['strategy_state']={'previous_allocation':{'equity':.3,'bond':.7}}
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['regime']['weights'],{'equity':.3,'bond':.7})

    def test_cash_buffer_retained(self):
        c,d,a,r,q=fixture();r['cash_buffer_cny']=100
        p=make_plan(c,d,a,r,q)
        self.assertEqual(p['account']['cash_after_cny'],100)
        validate_plan(p)

    def test_report_rejects_negative_or_tampered_positions_and_orders(self):
        p=make_plan(*fixture())
        for kind in ['negative','order','cash','target','positive_weight','price','cash_summary']:
            bad=copy.deepcopy(p)
            if kind=='negative': bad['holdings'][0]['projected_quantity']=-1
            elif kind=='order': bad['orders'][0]['quantity']+=1
            elif kind=='cash': bad['cash_after'][0]['amount']+=10
            elif kind=='target': bad['targets'][0]['target_weight']=-.1
            elif kind=='positive_weight': bad['holdings'][0]['current_weight']=.99
            elif kind=='price': bad['orders'][0]['reference_price']+=1
            else: bad['account']['cash_after_cny']+=100
            with self.subTest(kind=kind), self.assertRaises(ValueError): validate_plan(bad)

    def test_generated_plan_does_not_mutate_current_account(self):
        c,d,a,r,q=fixture(); original=copy.deepcopy(a)
        p=make_plan(c,d,a,r,q)
        self.assertEqual(a,original)
        self.assertEqual(p['state_if_fully_executed']['status'],'projection_not_execution')

    def test_export_report_and_hash_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp); c,d,a,r,q=fixture();p=make_plan(c,d,a,r,q)
            export_plan(p,c,tmp/'plan')
            result=export_rebalance(tmp/'plan',tmp/'report')
            self.assertEqual(validate_artifact(tmp/'report')['content_checks'],'passed')
            self.assertIn('只做多',Path(result['html']).read_text())
            self.assertNotIn('朋友示例',Path(result['html']).read_text())
            (tmp/'plan/orders.csv').write_text('tamper')
            with self.assertRaisesRegex(ValueError,'校验'): export_rebalance(tmp/'plan',tmp/'bad')

    def test_fixed_negative_configuration_rejected_even_direct_backtest(self):
        c,d,_,_,_=fixture();c['sleeves'][0]['assets'][0]['weight']=-.1
        with self.assertRaises(ValueError): backtest(c,d)

    def test_quote_extraction_excludes_future_grid_and_invalid_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            pd.DataFrame([{'date':day,'code':'A','close_raw':price,'price_available_at':'2025-06-30T16:00:00+08:00','price_valid':valid,'tradable':True}
                          for day,price,valid in [('2025-06-30',10,True),('2025-07-01',500,True),('2025-06-29',300,False)]]).to_csv(Path(tmp)/'prices.csv',index=False)
            result=extract_quotes(tmp,['A'],'2025-06-30T16:00:00+08:00',{'A':{'currency':'CNY','buy_lot':1,'sell_lot':1}})
            self.assertEqual(result['quotes'][0]['price'],10)

    def test_live_foreign_security_requires_its_local_execution_session(self):
        c,d,a,r,q=fixture();a['source']='actual';r['mode']='live'
        q['quotes'][1]['currency']='USD';q['quotes'][1]['price']=2
        q['fx']=[{'currency':'USD','rate_to_cny':5.,'available_at':r['as_of']}]
        a['cash'].append({'sleeve':'cash','currency':'USD','amount':100})
        approved={**review(c,a,r)['confirmation_template'],'confirmed':True,'confirmed_at':r['as_of'],'user_statement':'确认这份实际策略和持仓。'}
        p=make_plan(c,d,a,r,q,approved)
        self.assertFalse(any(x['code']=='B' for x in p['orders']))
        self.assertTrue(any('当地交易日历' in x['reason'] for x in p['issues']))
        q['quotes'][1]['execution_session']='2025-07-01'
        p=make_plan(c,d,a,r,q,approved)
        self.assertTrue(any(x['code']=='B' for x in p['orders']))

    def test_available_quantity_excess_rejected(self):
        c,d,a,r,q=fixture();a['positions'][0]['available_quantity']=81
        with self.assertRaisesRegex(ValueError,'可卖数量不能超过'):make_plan(c,d,a,r,q)

    def test_pending_orders_require_reconciliation(self):
        c,d,a,r,q=fixture();a['open_orders']=[{'code':'A','side':'sell','quantity':10}]
        with self.assertRaisesRegex(ValueError,'未完成委托'):make_plan(c,d,a,r,q)


if __name__=='__main__':
    unittest.main()
