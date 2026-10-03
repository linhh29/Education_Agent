"""Preference transitions through the real HTTP Handler, with an inert model."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler

import app
from companion import CompanionService
from memory_support import validity


class PreferenceHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'config').mkdir()
        (root / 'config/runtime.json').write_bytes((app.ROOT / 'config/runtime.json').read_bytes())
        self.service = CompanionService(root, root / 'db.json', app.empty_db)
        self.addCleanup(self.service.close)
        self.product = patch.object(app, 'PRODUCT', self.service)
        self.product.start()
        self.addCleanup(self.product.stop)
        self.logging = patch.object(app.Handler, 'log_message')
        self.logging.start()
        self.addCleanup(self.logging.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_port
        self.transport = build_opener(ProxyHandler({}))
        self.memories = []
        self.selection = None
        self.contexts = []
        self.counter = 0
        self.service.model.complete = self.complete
        self.child = self.call('/api/profiles', {'nickname': '合成偏好', 'age': 6})['id']

    def call(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, method=method,
                      headers={'Content-Type': 'application/json'})
        with self.transport.open(req, timeout=5) as response:
            return json.load(response)

    def complete(self, context, request_id, purpose='chat'):
        self.contexts.append((purpose, copy.deepcopy(context)))
        meta = {'model': 'controlled-offline', 'durationMs': 0}
        if purpose == 'recall':
            ids = [m['id'] for m in context['generalRecords'] + context['topicRecords']]
            return {'intent': 'ordinary', 'memoryIds': ids if self.selection is None else self.selection,
                    'conversationIds': []}, meta
        if purpose == 'exploration':
            first = context['messages'][0]
            return {'topic': '合成偏好对话', 'focus': {'text': first['text'], 'sourceMessageIds': [first['id']]},
                    'difficulties': [], 'attempts': [], 'openQuestions': []}, meta
        return {'answer': '收到这次表达。', 'topic': '合成偏好', 'memory': copy.deepcopy(self.memories),
                'usedMemoryIds': [], 'needsParent': False, 'suggestion': None}, meta

    def chat(self, text, memory=None):
        self.counter += 1
        self.memories = [memory] if memory else []
        return self.call('/api/chat', {'childId': self.child, 'requestId': 'preference_http_%04d' % self.counter,
                                      'userText': text})['snapshot']

    def preference(self, text, scope='general', relation='new', related=(), independent=True):
        memory = {'kind': 'preference', 'summary': text, 'quote': text, 'scope': scope,
                  'scopeEvidence': text if scope != 'conversation' else '',
                  'evidenceBasis': 'independent_expression' if independent else 'context_dependent',
                  'evidenceType': 'explicit_preference', 'relation': relation, 'relatedMemoryIds': list(related)}
        data = self.chat(text, memory)
        return next(m for m in data['memories'] if m['quote'] == text)

    def end(self):
        state = self.call('/api/state?childId=' + self.child)
        self.call('/api/conversations/end', {'childId': self.child,
                                            'conversationId': state['activeConversation']['id']})

    def supplied(self):
        return next(c['candidateMemories'] for purpose, c in reversed(self.contexts) if purpose == 'chat')

    def test_stable_personal_preference_crosses_sessions_but_selection_stays_relevant(self):
        old = self.preference('我最爱看恐龙的书')
        self.assertEqual(old['effectiveScope'], 'general')
        self.end()
        self.chat('还记得我爱看什么书吗')
        self.assertEqual([m['id'] for m in self.supplied()], [old['id']])
        self.selection = []
        self.chat('为什么窗户上会有水珠')
        self.assertEqual(self.supplied(), [])

    def test_contextual_correction_retires_old_candidate_without_poisoning_new_evidence(self):
        old = self.preference('我在这两种动物里更喜欢小猫')
        original = self.service.store.read()['memoryItems'][old['id']]
        new = self.preference('刚才说反了，是小狗', relation='correction', related=[old['id']], independent=False)
        self.assertEqual((new['scope'], new['relation']), ('general', 'correction'))
        self.assertEqual(new['supersedes'], [{'id': old['id'], 'version': 1}])
        db = self.service.store.read()
        self.assertEqual(db['memoryItems'][old['id']], original)
        self.assertNotIn(new['id'], validity(db, self.child)[1])
        snapshot = self.call('/api/state?childId=' + self.child)
        history = next(m for m in snapshot['memories'] if m['id'] == old['id'])
        self.assertEqual(history['supersededBy'], new['id'])
        self.assertFalse(history['evidenceStale'])
        self.assertFalse(self.service.parent.valid_result(db, {'memoryVersions': [{'id': old['id'], 'version': 1}]}, self.child))
        self.end()
        self.chat('接着聊我更喜欢的动物')
        self.assertEqual([m['id'] for m in self.supplied()], [new['id']])
        self.assertEqual(self.service.store.read()['messages'][old['sourceMessageIds'][0]]['text'], old['quote'])

    def test_temporary_story_and_example_do_not_override_general_preference(self):
        stable = self.preference('我喜欢安静一点的故事')
        for text in ['这次故事里我更喜欢小狮子', '今天先用小汽车举例']:
            with self.subTest(text=text):
                local = self.preference(text, scope='conversation', relation='local_change', related=[stable['id']])
                self.assertEqual(local['scope'], 'conversation')
                self.assertEqual(local['supersedes'], [])
                self.end()
                self.chat('新一段故事')
                self.assertEqual([m['id'] for m in self.supplied()], [stable['id']])

    def test_scoped_correction_cannot_retire_general_or_parent_reminder(self):
        stable = self.preference('我喜欢机器人')
        parent = self.call('/api/reminders', {'childId': self.child, 'summary': '请保留大人提醒', 'scope': 'general'})
        local = self.preference('这个故事里改成别的主角', scope='conversation', relation='correction', related=[stable['id'], parent['id']])
        self.assertEqual(local['supersedes'], [])
        self.end()
        self.chat('新的相关问题')
        self.assertEqual({m['id'] for m in self.supplied()}, {stable['id'], parent['id']})
        stored = self.service.store.read()['memoryItems'][parent['id']]
        self.assertEqual(stored, parent)

    def test_correction_chain_withdrawal_and_restore_do_not_resurrect_replaced_values(self):
        first = self.preference('我最喜欢红色')
        second = self.preference('刚才说错了，我最喜欢蓝色', relation='correction', related=[first['id']])
        third = self.preference('再改一下，我现在最喜欢绿色', relation='correction', related=[second['id']])
        self.end()
        self.chat('记得我最喜欢哪种颜色吗')
        self.assertEqual([m['id'] for m in self.supplied()], [third['id']])
        withdrawn = self.call('/api/cards/' + third['id'], {'childId': self.child, 'action': 'withdraw'}, 'PATCH')
        self.chat('再聊颜色')
        self.assertEqual(self.supplied(), [])
        restored = self.call('/api/cards/' + third['id'], {'childId': self.child, 'action': 'restore',
            'expectedVersion': withdrawn['version'], 'requestId': 'restore_preference_once'}, 'PATCH')
        self.chat('恢复后聊颜色')
        self.assertEqual([m['id'] for m in self.supplied()], [restored['id']])


if __name__ == '__main__':
    unittest.main()
