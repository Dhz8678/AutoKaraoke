import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from autokaraoke import config, llm


class LlmApiTests(unittest.TestCase):
    def test_base_full_url_root_and_query_normalization(self):
        examples = {
            'https://openrouter.ai/api/v1/': 'https://openrouter.ai/api/v1/chat/completions',
            'http://127.0.0.1:8000': 'http://127.0.0.1:8000/v1/chat/completions',
            'http://localhost:1234/v1': 'http://localhost:1234/v1/chat/completions',
            'https://example.com/proxy/v1/chat/completions/': 'https://example.com/proxy/v1/chat/completions',
            'https://example.com/v1?version=2': 'https://example.com/v1/chat/completions?version=2',
        }
        for base, endpoint in examples.items():
            with self.subTest(base=base):
                self.assertEqual(config.llm_chat_url(base), endpoint)
        for value in ('file:///tmp/model', 'example.com/v1', 'https://host:abc/v1', 'https://host/a\nb', 'https://host/#fragment'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.llm_chat_url(value)

    def _request(self, environment):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"lines": []}'}}],
                                             'model': 'custom/model', 'usage': {}})

        with patch.dict(os.environ, environment), httpx.Client(transport=httpx.MockTransport(respond)) as client, \
                patch.object(llm.httpx, 'post', side_effect=client.post), \
                patch.object(config, 'OPENROUTER_MODEL', 'custom/model'):
            reply = llm.chat_json('Return JSON', 'test lyrics', retries=0)
        return requests[0], reply

    def test_custom_api_gets_its_own_url_key_model_and_standard_body(self):
        request, reply = self._request({'AUTOKARAOKE_LLM_BASE_URL': 'https://domestic.example/v1',
                                       'AUTOKARAOKE_LLM_API_KEY': 'test-custom-key',
                                       'OPENROUTER_API_KEY': 'old-provider-key'})
        self.assertEqual(str(request.url), 'https://domestic.example/v1/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer test-custom-key')
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'custom/model')
        self.assertNotIn('usage', body)
        self.assertNotIn('reasoning', body)
        self.assertEqual(reply.data, {'lines': []})
        self.assertIsNone(reply.cost)

    def test_keyless_local_api_does_not_receive_the_old_openrouter_key(self):
        request, _ = self._request({'AUTOKARAOKE_LLM_BASE_URL': 'http://127.0.0.1:8000/v1',
                                   'AUTOKARAOKE_LLM_API_KEY': '', 'OPENROUTER_API_KEY': 'old-key'})
        self.assertNotIn('Authorization', request.headers)

    def test_default_openrouter_remains_compatible_with_old_settings(self):
        request, _ = self._request({'AUTOKARAOKE_LLM_BASE_URL': '', 'AUTOKARAOKE_LLM_API_KEY': '',
                                   'OPENROUTER_API_KEY': 'legacy-test-key'})
        self.assertEqual(str(request.url), 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer legacy-test-key')
        body = json.loads(request.content)
        self.assertIn('usage', body)
        self.assertIn('reasoning', body)

    def test_setting_custom_url_does_not_enable_llm_until_user_enables_it(self):
        values = {'AUTOKARAOKE_LLM_BASE_URL': 'http://localhost:8000/v1',
                  'AUTOKARAOKE_LLM_API_KEY': '', 'AUTOKARAOKE_EXPERIMENTAL_LLM': ''}
        with patch.dict(os.environ, values):
            self.assertFalse(config.llm_available())
            os.environ['AUTOKARAOKE_EXPERIMENTAL_LLM'] = '1'
            self.assertTrue(config.llm_available())

    def test_settings_save_and_reopen_custom_endpoint_without_real_network(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from autokaraoke.gui.dialogs import SettingsDialog

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, 'CONFIG_DIR', Path(directory)), \
                patch.object(config, 'OPENROUTER_MODEL', 'old-model'), patch.dict(os.environ):
            settings = SettingsDialog()
            settings.experimental.setChecked(True)
            settings.api_url.setText('http://localhost:8000/v1')
            settings.key.setText('')
            settings.model.setText('my-local-model')
            settings.accept()
            saved = (Path(directory) / '.env').read_text(encoding='utf-8')
            self.assertIn('AUTOKARAOKE_LLM_BASE_URL=http://localhost:8000/v1', saved)
            self.assertIn('AUTOKARAOKE_LLM_MODEL=my-local-model', saved)
            reopened = SettingsDialog()
            self.assertEqual(reopened.api_url.text(), 'http://localhost:8000/v1')
            self.assertEqual(reopened.model.text(), 'my-local-model')
            self.assertEqual(reopened.key.text(), '')
            reopened.close()
        self.assertIsNotNone(app)


if __name__ == '__main__':
    unittest.main()
