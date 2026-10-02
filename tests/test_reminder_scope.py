"""Scope contracts exercise real storage/retrieval with a local, inert provider."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import app
from companion import CompanionService, ProductError, stamp


class ReminderScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'config').mkdir()
        config = json.loads((app.ROOT / 'config/runtime.json').read_text())
        config['key_file'] = str(root / 'disabled-key')
        (root / 'config/runtime.json').write_text(json.dumps(config))
        self.s = CompanionService(root, root / 'db.json', app.seed_db)
        self.addCleanup(self.s.close)
        self.child = self.s.save_profile({'nickname': '合成范围', 'age': 5})['id']
        self.other = self.s.save_profile({'nickname': '合成隔离', 'age': 6})['id']
        with self.s.store.transaction() as db:
            for cid, child in [('ca', self.child), ('cb', self.child), ('foreign', self.other)]:
                db['conversations'][cid] = {'id': cid, 'childId': child, 'title': cid, 'startedAt': stamp()}
        self.contexts = []

        def complete(context, rid, purpose='chat'):
            self.contexts.append((purpose, copy.deepcopy(context)))
            if purpose == 'recall':
                ids = [m['id'] for m in context['generalRecords'] + context['topicRecords']]
                return {'intent': 'ordinary', 'memoryIds': ids, 'conversationIds': []}, {}
            return {'answer': '这是受控回归回答。', 'topic': '合成范围', 'memory': [],
                    'usedMemoryIds': [], 'suggestion': None, 'needsParent': False}, {'model': 'controlled', 'durationMs': 0}
        self.s.model.complete = Mock(side_effect=complete)

    def add(self, **extra):
        return self.s.add_reminder({'childId': self.child, 'summary': '合成提醒', **extra})

    def test_missing_foreign_deleted_binding_cannot_silently_widen(self):
        with self.s.store.transaction() as db:
            db['conversations']['cb']['deleted'] = True
        before = self.s.store.read()['memoryItems']
        for cid in ['', 'foreign', 'cb']:
            with self.subTest(cid=cid), self.assertRaises(ProductError) as err:
                self.add(scope='conversation', conversationId=cid)
            self.assertEqual(err.exception.code, 'reminder_conversation')
        self.assertEqual(self.s.store.read()['memoryItems'], before)

    def test_actual_answer_context_obeys_session_scope_and_keeps_normal_reminders(self):
        session = self.add(scope='conversation', conversationId='ca')
        general = self.add(scope='general')
        topic = self.add(scope='topic', topic='合成范围')
        for cid, expected in [('ca', {session['id'], general['id'], topic['id']}),
                              ('cb', {general['id'], topic['id']})]:
            self.s.chat({'childId': self.child, 'conversationId': cid, 'requestId': 'scope_request_' + cid,
                         'userText': '一个相关的合成问题'})
            actual = next(c for purpose, c in reversed(self.contexts) if purpose == 'chat')
            self.assertEqual({m['id'] for m in actual['candidateMemories']}, expected)
        self.assertEqual(self.s.store.read()['memoryItems'][session['id']]['conversationId'], 'ca')

    def test_edit_withdraw_restore_and_selected_history_keep_binding(self):
        m = self.add(scope='conversation', conversationId='ca')
        changed = self.s.update_memory(m['id'], {'childId': self.child, 'summary': '新说明'})
        self.assertEqual((changed['scope'], changed['conversationId']), ('conversation', 'ca'))
        withdrawn = self.s.update_memory(m['id'], {'childId': self.child, 'action': 'withdraw'})
        restored = self.s.update_memory(m['id'], {'childId': self.child, 'action': 'restore',
                    'expectedVersion': withdrawn['version'], 'requestId': 'scope_restore_once'})
        self.assertEqual((restored['summary'], restored['conversationId']), ('新说明', 'ca'))
        historical = self.s.update_memory(m['id'], {'childId': self.child, 'action': 'restore_version',
                     'expectedVersion': restored['version'], 'historyIndex': 0, 'requestId': 'scope_restore_history'})
        self.assertEqual((historical['scope'], historical['conversationId']), ('conversation', 'ca'))
        self.assertEqual(historical['summary'], m['summary'])
        self.assertEqual(len(historical['history']), 4)

    def test_topic_general_and_legacy_form_contracts(self):
        self.assertEqual(self.add(topic='植物')['scope'], 'topic')
        self.assertEqual(self.add()['scope'], 'general')
        self.assertEqual(self.add(scope='general', topic='标签')['scope'], 'general')
        with self.assertRaises(ProductError):
            self.add(scope='topic', topic='')
        m = self.add(scope='conversation', conversationId='ca')
        updated = self.s.update_memory(m['id'], {'childId': self.child, 'summary': '适用所有聊天', 'scope': 'general'})
        self.assertEqual(updated['scope'], 'general')
        self.assertIsNone(updated['conversationId'])


if __name__ == '__main__':
    unittest.main()
