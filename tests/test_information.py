"""Controlled synthetic responses only; these tests do not access providers."""
import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from yantra.configuration import ConfigStore
from yantra.extensions import ExtensionCatalog, ExtensionRuntime, ToolSpec, interactive_extensions
from yantra import information


class InformationTests(unittest.TestCase):
    def setUp(self):
        # Python HTTP and socket paths must be mocked, including optional SDKs.
        for target in ('socket.create_connection', 'socket.socket.connect', 'httpx.HTTPTransport.handle_request'):
            blocker = patch(target, side_effect=AssertionError('unmocked external call forbidden'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def test_interactive_import_keeps_disabled_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disabled.json'
            path.write_text(json.dumps(information.extension_specs()['fuyao']))
            catalog = ExtensionCatalog(ConfigStore(Path(directory) / 'config.json'))
            stdout = io.StringIO()
            with patch('yantra.extensions.ask', side_effect=['add', 'tools', 'fuyao', str(path),
                    'finance,thesis,verification', 'back']), contextlib.redirect_stdout(stdout):
                asyncio.run(interactive_extensions(catalog))
            self.assertFalse(catalog.store.load()['tools']['fuyao']['enabled'])
            self.assertIn('保持停用', stdout.getvalue())
            self.assertNotIn('并启用', stdout.getvalue())

    def test_specs_validate_and_examples_match(self):
        for name, spec in information.extension_specs(absolute=False).items():
            with self.subTest(provider=name):
                ToolSpec.model_validate(spec)
                path = Path(__file__).parents[1] / 'examples/v033/information' / (name+'.example.json')
                if name not in information.SERVICE_METADATA:
                    self.assertEqual(json.loads(path.read_text()), spec)
                else:
                    path = Path(__file__).parents[1] / 'examples/v042/information' / (name+'.example.json')
                    self.assertEqual(json.loads(path.read_text()), spec)
                    self.assertFalse(spec['enabled'])
                    self.assertEqual(spec['cost_status'], 'unknown')
        self.assertFalse(information.extension_specs()['fuyao']['enabled'])
        self.assertFalse(information.extension_specs()['eastmoney']['enabled'])

    def test_fuyao_header_query_and_original_units(self):
        raw = {'code': 0, 'data': {'item': [{'currency': 'CNY', 'total_profit': 123.4}]}}
        with patch.dict('os.environ', FUYAO_API_KEY='synthetic-secret'), patch.object(information, 'request_json', return_value=raw) as request:
            result = information.fetch('fuyao', {'operation': 'income-statements',
                'parameters': {'thscode': '600001.SH', 'period': 'annual', 'limit': 3}})
        request.assert_called_once_with('https://fuyao.aicubes.cn/api/a-share/financials/income-statements',
            headers={'X-api-key': 'synthetic-secret'}, parameters={'thscode': '600001.SH', 'period': 'annual', 'limit': 3})
        self.assertEqual(result['data'], raw)
        self.assertIn('retrieved_at', result)

    def test_fuyao_business_error_is_not_success(self):
        with patch.dict('os.environ', FUYAO_API_KEY='synthetic-secret'), patch.object(information, 'request_json', return_value={'code': 2003, 'message': 'synthetic-secret'}):
            with self.assertRaisesRegex(ValueError, '业务错误') as caught:
                information.fetch('fuyao', {'operation': 'ticker-search', 'parameters': {'q': '示例'}})
        self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_eastmoney_official_post_contract(self):
        with patch.dict('os.environ', MX_APIKEY='synthetic-secret'), patch.object(information, 'request_json', return_value={'title': '合成新闻'}) as request:
            result = information.fetch('eastmoney', {'query': '示例公司公告'})
        request.assert_called_once_with('https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search', method='POST',
            headers={'apikey': 'synthetic-secret'}, payload={'query': '示例公司公告'})
        self.assertEqual(result['data']['title'], '合成新闻')

    def test_miaoxiang_data_payload_preserves_response(self):
        raw = {'status': 0, 'data': {'data': {'rawTable': {'value': ['123.4500', None]},
                                           'unit': '供应商原始单位'}}}
        with patch.dict('os.environ', MX_APIKEY='synthetic-secret'), \
                patch.object(information, 'request_json', return_value=raw) as request:
            result = information.fetch('eastmoney_data', {'toolQuery': '受控测试查询'})
        request.assert_called_once_with('https://mkapi2.dfcfs.com/finskillshub/api/claw/query',
            method='POST', headers={'apikey': 'synthetic-secret'}, payload={'toolQuery': '受控测试查询'})
        self.assertEqual(result['data'], raw)

    def test_legacy_search_snapshot_credential_and_new_tools_require_mx_key(self):
        with patch.dict('os.environ', {'EASTMONEY_APIKEY': 'legacy-synthetic'}, clear=True), \
                patch.object(information, 'request_json', return_value={'status': 0}) as request:
            information.fetch('eastmoney', {'query': '受控测试查询'})
            self.assertEqual(request.call_args.kwargs['headers'], {'apikey': 'legacy-synthetic'})
            request.reset_mock()
            with self.assertRaisesRegex(ValueError, 'MX_APIKEY'):
                information.fetch('eastmoney_data', {'toolQuery': '受控测试查询'})
            request.assert_not_called()

    def test_miaoxiang_business_errors_and_wrong_fields_rejected(self):
        for provider, (_, field) in information.EASTMONEY_ENDPOINTS.items():
            for body in ({'code': 113, 'message': 'synthetic-secret'},
                         {'status': 114, 'message': 'synthetic-secret'}, {'code': '115'},
                         {'success': False, 'status': 0, 'code': 0}):
                with self.subTest(provider=provider, body=body), patch.dict('os.environ', MX_APIKEY='synthetic-secret'), \
                        patch.object(information, 'request_json', return_value=body):
                    with self.assertRaisesRegex(ValueError, '业务错误') as caught:
                        information.fetch(provider, {field: '受控测试查询'})
                    self.assertNotIn('synthetic-secret', str(caught.exception))
            with patch.object(information, 'request_json') as request:
                with self.assertRaisesRegex(ValueError, '参数无效'):
                    information.fetch(provider, {field: '受控测试查询', 'api_key': 'synthetic-secret'})
                request.assert_not_called()

    def test_miaoxiang_command_receives_key_and_redacts_echoed_response(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', MX_APIKEY='synthetic-mx-secret'):
                store = ConfigStore(Path(directory) / 'config.json')
                catalog = ExtensionCatalog(store)
                spec = information.extension_specs()['eastmoney_data']
                spec['enabled'] = True
                # Run the real command entry with a controlled HTTP replacement in the child.
                bootstrap = ("from yantra import information\n"
                    "import socket\n"
                    "def blocked(*args, **kwargs): raise AssertionError('unmocked external call forbidden')\n"
                    "socket.create_connection = blocked\n"
                    "socket.socket.connect = blocked\n"
                    "def mock(url, **kwargs):\n"
                    "    assert url.endswith('/api/claw/query')\n"
                    "    assert kwargs['method'] == 'POST'\n"
                    "    assert kwargs['headers']['apikey'] == 'synthetic-mx-secret'\n"
                    "    return {'status': 0, 'data': kwargs['payload'], 'echo': kwargs['headers']['apikey']}\n"
                    "information.request_json = mock\n"
                    "raise SystemExit(information.main())\n")
                spec['command'] = [sys.executable, '-c', bootstrap, 'eastmoney_data']
                await catalog.add('tools', 'eastmoney_data', spec)
                runtime = ExtensionRuntime(catalog.snapshot(Path(directory) / 'run'))
                result = await runtime.call('ext_eastmoney_data', {'toolQuery': '受控测试查询'}, 10)
                self.assertEqual(result['data']['data'], {'toolQuery': '受控测试查询'})
                self.assertEqual(result['data']['echo'], '[REDACTED]')
                self.assertNotIn('synthetic-mx-secret', json.dumps(result))
        asyncio.run(check())

    def test_cls_requires_no_credential(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(information, 'request_json', return_value={'data': []}) as request:
            result = information.fetch('cls', {})
        request.assert_called_once_with('https://api.98dou.cn/api/hotlist/cls/all')
        self.assertEqual(result['provider'], 'cls')

    def test_cls_explicit_failure_is_not_success(self):
        with patch.object(information, 'request_json', return_value={'success': False, 'msg': 'upstream failure'}):
            with self.assertRaisesRegex(ValueError, '失败状态'):
                information.fetch('cls', {})

    def test_invalid_arguments_rejected_before_network_or_import(self):
        cases = [('fuyao', {'operation': '../secret', 'parameters': {}}),
            ('fuyao', {'operation': 'prices-snapshot', 'parameters': {'thscodes': '600001'}}),
            ('fuyao', {'operation': 'ticker-search', 'parameters': {'q': '示例', 'api_key': 'secret'}}),
            ('eastmoney', {'query': 'q', 'url': 'https://example.invalid'}),
            ('eastmoney_screen', {'keyword': 'excluded service'}),
            ('akshare', {'function': 'os.system', 'parameters': {}}),
            ('akshare', {'function': 'stock_news_em', 'parameters': {'symbol': 'q'}, 'limit': 0}),
            ('cls', {'token': 'secret'})]
        with patch.object(information, 'request_json') as request, patch.object(information.importlib, 'import_module') as module:
            for provider, arguments in cases:
                with self.subTest(provider=provider, arguments=arguments), self.assertRaisesRegex(ValueError, '参数无效'):
                    information.fetch(provider, arguments)
            request.assert_not_called()
            module.assert_not_called()

    def test_missing_and_placeholder_credentials(self):
        for value in ('', '<YOUR_KEY>', 'replace_me', 'YOUR_FUYAO_API_KEY'):
            with self.subTest(value=value), patch.dict('os.environ', FUYAO_API_KEY=value), patch.object(information, 'request_json') as request:
                with self.assertRaisesRegex(ValueError, 'FUYAO_API_KEY'):
                    information.fetch('fuyao', {'operation': 'ticker-search', 'parameters': {'q': '示例'}})
                request.assert_not_called()

    def test_http_redirect_error_and_size_limit(self):
        original = httpx.Client
        for status, content, expected in [(302, b'key=synthetic-secret', 'HTTP 302'),
                (403, b'key=synthetic-secret', 'HTTP 403'), (200, b'x'*900001, '900 KB')]:
            transport = httpx.MockTransport(lambda request: httpx.Response(status, content=content))
            with self.subTest(status=status, expected=expected), patch.object(information.httpx, 'Client',
                    side_effect=lambda **kwargs: original(transport=transport, **kwargs)):
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    information.request_json('https://example.invalid')
                self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_akshare_null_serialization_truncation_and_stdout(self):
        # pandas is a dependency of the optional AKShare package, imported only here.
        try:
            import pandas as pd
        except ImportError:
            self.skipTest('optional AKShare/pandas dependency is not installed')
        frame = pd.DataFrame({'日期': [pd.Timestamp('2026-01-01'), pd.Timestamp('2026-01-02')], '收盘': [float('nan'), 12.3]})
        def synthetic_history(**kwargs):
            print('provider progress')
            return frame
        fake = SimpleNamespace(stock_zh_a_hist=synthetic_history)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(information.importlib, 'import_module', return_value=fake), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = information.fetch('akshare', {'function': 'stock_zh_a_hist',
                'parameters': {'symbol': '600001', 'start_date': '20260101', 'end_date': '20260102', 'adjust': 'qfq'}, 'limit': 1})
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('provider progress', stderr.getvalue())
        self.assertEqual(result['data']['total_rows'], 2)
        self.assertTrue(result['data']['truncated'])
        self.assertIsNone(result['data']['records'][0]['收盘'])
        self.assertEqual(result['data']['parameters']['adjust'], 'qfq')

    def test_akshare_dates_and_missing_installation(self):
        with patch.object(information.importlib, 'import_module', return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, 'end_date'):
                information.fetch('akshare', {'function': 'stock_zh_a_hist',
                    'parameters': {'symbol': '600001', 'start_date': '20260201', 'end_date': '20260101'}})
        with patch.object(information.importlib, 'import_module', side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(ValueError, 'requirements-information'):
                information.fetch('akshare', {'function': 'stock_news_em', 'parameters': {'symbol': '示例'}})

    def test_extension_registration_and_subprocess_invalid_input(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                store = ConfigStore(Path(directory)/'config.json')
                catalog = ExtensionCatalog(store)
                spec = information.extension_specs()['cls']
                await catalog.add('tools', 'cls', spec)
                runtime = ExtensionRuntime(catalog.snapshot(Path(directory)/'run'))
                self.assertIn('ext_cls', runtime.permissions('finance'))
                with self.assertRaisesRegex(ValueError, 'JSON Schema'):
                    await runtime.call('ext_cls', {'query': 'unrequested'}, 5)
                # Exercise the actual JSON command process without a network request.
                spec['input_schema'] = {'type': 'object'}
                await catalog.add('tools', 'cls', spec)
                runtime = ExtensionRuntime(catalog.snapshot(Path(directory)/'run2'))
                with self.assertRaisesRegex(ValueError, '退出码 1'):
                    await runtime.call('ext_cls', {'query': 'unrequested'}, 5)
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
