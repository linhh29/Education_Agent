"""First-install configuration contracts; no credentials or provider calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from companion import CompanionService, ModelClient, ProductError

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.config = json.loads((ROOT / 'config/runtime.json').read_text())
        (self.root / 'config/runtime.json').write_text(json.dumps(self.config))
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_blank_install_has_no_fixture_or_spending_and_missing_key_never_calls_provider(self):
        service = CompanionService(self.root, self.root / 'data/local-db.json', app.empty_db)
        self.addCleanup(service.close)
        self.assertEqual(service.store.read()['profiles'], {})
        self.assertEqual(service.store.read()['messages'], {})
        self.assertEqual(service.model.occupied(), 0)
        self.assertFalse(service.model.public_status()['keyConfigured'])
        with patch('companion.build_opener') as network:
            with self.assertRaises(ProductError) as caught:
                service.model.complete({'currentText': '合成问题'}, 'no-key')
            self.assertEqual(caught.exception.code, 'key_unavailable')
            network.assert_not_called()
        self.assertEqual(service.model.ledger.read()['calls'], {})

    def test_private_paths_resolve_from_checkout_and_shared_ledger_keeps_occupied_budget(self):
        self.config['budget'].update(prior_cny=2.735356, additional_limit_cny=47)
        (self.root / 'config/runtime.local.json').write_text(json.dumps(self.config))
        ledger = self.root / 'shared.json'
        ledger.write_text(json.dumps({'priorCny': 2.735356, 'calls': {'old': {'occupiedCny': 9}}}))
        key = self.root / 'private-key'
        key.write_text('offline-auth-placeholder')
        with patch.dict(os.environ, {'EDUCATION_AGENT_KEY_FILE': 'private-key',
                                     'EDUCATION_AGENT_LEDGER': str(ledger)}):
            client = ModelClient(self.root)
            self.assertEqual(client.config['key_file'], str(key.resolve()))
            self.assertAlmostEqual(client.limit(), 49.735356)
            self.assertAlmostEqual(client.occupied(), 11.735356)
            self.assertEqual(client.ledger.path, ledger)
            self.assertTrue(client.public_status()['keyConfigured'])
        self.assertEqual(json.loads(ledger.read_text())['calls'], {'old': {'occupiedCny': 9}})

    def test_explicit_configuration_and_unreadable_key_fail_without_fallback(self):
        self.config['key_file'] = 'missing-key'
        (self.root / 'other.json').write_text(json.dumps(self.config))
        with patch.dict(os.environ, {'EDUCATION_AGENT_CONFIG': 'other.json'}):
            client = ModelClient(self.root)
            with self.assertRaises(ProductError) as caught:
                client.read_key()
            self.assertEqual(caught.exception.code, 'key_unavailable')
        with patch.dict(os.environ, {'EDUCATION_AGENT_CONFIG': 'missing.json'}):
            with self.assertRaises(ProductError) as caught:
                ModelClient(self.root)
            self.assertEqual(caught.exception.code, 'model_config')


if __name__ == '__main__':
    unittest.main()
