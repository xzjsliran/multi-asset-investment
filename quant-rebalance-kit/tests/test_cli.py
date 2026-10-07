"""以独立进程验证持仓导入、确认记录及计划交接；全部为人工数据。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import pandas as pd

from test_rebalance import fixture, ROOT


class CommandWorkflow(unittest.TestCase):
    def test_import_review_confirm_and_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,d,a,r,q=fixture();r['mode']='live'
            pd.DataFrame(a['positions']).to_csv(p/'positions.csv',index=False)
            pd.DataFrame(a['cash']).to_csv(p/'cash.csv',index=False)
            for name,value in [('strategy',c),('request',r),('quotes',q)]:
                (p/(name+'.json')).write_text(json.dumps(value))
            data=p/'data';data.mkdir()
            d.prices.rename_axis('date').reset_index().melt(id_vars='date',var_name='code',value_name='close_adjusted').to_csv(data/'prices.csv',index=False)
            pd.DataFrame({'date':d.calendar}).to_csv(data/'calendar.csv',index=False)
            cli=[sys.executable,str(ROOT/'quant-rebalance-kit/scripts/run.py')]
            def run(args):return subprocess.run(cli+args,capture_output=True,text=True)
            imported=run(['import-account','--positions-csv',str(p/'positions.csv'),'--cash-csv',str(p/'cash.csv'),'--as-of',a['as_of'],'--out',str(p/'account.json')])
            self.assertEqual(imported.returncode,0,imported.stderr)
            common=['--config',str(p/'strategy.json'),'--account',str(p/'account.json'),'--request',str(p/'request.json')]
            reviewed=run(['review',*common,'--out',str(p/'review')])
            self.assertEqual(reviewed.returncode,0,reviewed.stderr)
            cf=p/'review/confirmation.json'
            args=['plan',*common,'--data',str(data),'--quotes',str(p/'quotes.json'),'--confirmation',str(cf),'--out',str(p/'plan')]
            self.assertEqual(run(args).returncode,2)
            self.assertFalse((p/'plan').exists())
            approval=json.loads(cf.read_text());self.assertFalse(approval['confirmed'])
            approval.update(confirmed=True,confirmed_at=r['as_of'],user_statement='自动测试的合成确认记录，不是实际客户账户。')
            cf.write_text(json.dumps(approval))
            completed=run(args)
            self.assertEqual(completed.returncode,0,completed.stderr)
            plan=json.loads((p/'plan/plan.json').read_text())
            self.assertEqual([(x['side'],x['quantity']) for x in plan['orders']],[('sell',30),('buy',30)])
            self.assertEqual(run(args).returncode,2)  # 已有结果不能被覆盖。

    def test_import_preserves_leading_zero_and_rejects_kit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'positions.csv').write_text('sleeve,code,quantity,available_quantity\nequity,000001.SZ,100,100\n')
            (p/'cash.csv').write_text('sleeve,currency,amount\ncash,CNY,200\n')
            args=[sys.executable,str(ROOT/'quant-rebalance-kit/scripts/run.py'),'import-account','--positions-csv',str(p/'positions.csv'),'--cash-csv',str(p/'cash.csv'),'--as-of','2025-06-30T16:00:00+08:00','--source','simulation','--out']
            result=subprocess.run(args+[str(p/'account.json')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((p/'account.json').read_text())['positions'][0]['code'],'000001.SZ')
            blocked=subprocess.run(args+[str(ROOT/'quant-rebalance-kit/assets/test-account-output.json')],capture_output=True,text=True)
            self.assertEqual(blocked.returncode,2)
            self.assertIn('kit以外',blocked.stderr)


if __name__=='__main__':unittest.main()
