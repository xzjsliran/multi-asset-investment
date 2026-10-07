import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from quantkit.ifind import IfindClient, decode_rpc
from quantkit.credentials import save_credential, get_credential, status


class OptionalIfind(unittest.TestCase):
    def test_local_credentials_permission_and_redacted_status(self):
        with tempfile.TemporaryDirectory() as temp, patch('pathlib.Path.home',return_value=Path(temp)), patch.dict(os.environ,{},clear=True):
            save_credential('ifind','unit-test-only-secret')
            self.assertEqual(get_credential('ifind'),'unit-test-only-secret')
            self.assertNotIn('unit-test-only-secret',json.dumps(status()))
            if os.name != 'nt':self.assertEqual((Path(temp)/'.config/multi-assets/credentials.json').stat().st_mode & 0o777,0o600)
            with patch.dict(os.environ,{'IFIND_API_KEY':'environment-test'}):self.assertEqual(get_credential('ifind'),'environment-test')

    def test_budget_counts_failed_attempts_and_cache_does_not_charge(self):
        with tempfile.TemporaryDirectory() as temp:
            c=IfindClient(temp,max_calls=1,token='unit-test-token');c.schemas['edb']={'get_edb_data':{}}
            with patch.object(c,'_post',side_effect=RuntimeError('timeout')) as send:
                self.assertFalse(c.call('edb','get_edb_data',{'query':'first'})['ok'])
                self.assertTrue(c.call('edb','get_edb_data',{'query':'first'})['cached'])
                with self.assertRaisesRegex(ValueError,'次数'):c.call('edb','get_edb_data',{'query':'second'})
                self.assertEqual(send.call_count,1)
            self.assertEqual(json.loads(Path(temp,'budget.json').read_text())['attempted_calls'],1)

    def test_tool_permissions_do_not_spend_and_response_secrets_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            c=IfindClient(temp,token='unit-test-token');c.schemas['stock']={'get_stock_info':{}}
            with self.assertRaisesRegex(ValueError,'清单'):c.call('stock','unknown',{})
            self.assertFalse(Path(temp,'budget.json').exists())
            with patch.object(c,'_post',return_value={'content':[{'text':'unit-test-token'}],'auth_token':'anything'}):
                c.call('stock','get_stock_info',{'query':'company'})
            self.assertFalse(any('unit-test-token' in p.read_text() for p in Path(temp).glob('*.json')))

    def test_sse_and_tool_error_supported(self):
        class Response:
            text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
            def json(self):raise ValueError()
        self.assertEqual(decode_rpc(Response())['result'],{'tools':[]})
        with tempfile.TemporaryDirectory() as temp:
            c=IfindClient(temp,token='unit-test-token');c.schemas['stock']={'get_stock_info':{}}
            with patch.object(c,'_post',return_value={'isError':True,'content':[]}):self.assertFalse(c.call('stock','get_stock_info',{})['ok'])

if __name__ == '__main__':unittest.main()
