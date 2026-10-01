"""Parent isolation/ownership/lifecycle regressions; providers are controlled."""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from companion import CompanionService, ProductError, stamp

ROOT = Path(__file__).resolve().parents[1]


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = CompanionService(ROOT, Path(self.tmp.name) / 'db.json', app.seed_db)
        self.a = self.s.save_profile({'nickname': '合成甲', 'age': 5})['id']
        self.b = self.s.save_profile({'nickname': '合成乙', 'age': 6})['id']
        self.n = 0
        now = stamp()
        with self.s.store.transaction() as db:
            for cid, child, text in [('ca', self.a, '我没明白为什么会倒'), ('cb', self.b, '另一档案私有表达')]:
                db['conversations'][cid] = {'id': cid, 'childId': child, 'startedAt': now, 'title': '合成问题'}
                db['messages']['u'+cid] = {'id': 'u'+cid, 'childId': child, 'conversationId': cid, 'role': 'user', 'text': text, 'status': 'completed', 'createdAt': now}
            db['messages']['old'] = {'id': 'old', 'childId': self.a, 'conversationId': 'ca', 'role': 'user', 'text': '很早的提问', 'status': 'completed', 'createdAt': '2020-01-01T00:00:00+00:00'}

    def child_state(self):
        return {k: v for k, v in self.s.store.read().items() if not k.startswith('parent') or k == 'parentFeedback'}

    def response(self, **extra):
        return {'answer': '原话是“我没明白为什么会倒”，仅说明当时的困惑。', 'advice': '', 'sources': ['uca'], 'activity': None, 'draft': None, **extra}

    def selector(self, **extra):
        return {'mode': 'recent', 'fromDate': '', 'toDate': '', 'memoryIds': [], 'conversationIds': ['ca'], 'inheritActivity': False, **extra}

    def ask(self, answer=None, selection=None, child=None):
        self.n += 1
        rid = 'parent_test_%08d' % self.n
        contexts = []
        def model(context, request_id, purpose='chat'):
            contexts.append((purpose, copy.deepcopy(context)))
            if purpose == 'parent_select':
                return selection or self.selector(), {'model': 'controlled'}
            self.assertEqual(purpose, 'parent_answer')
            return answer or self.response(), {'model': 'controlled'}
        with patch.object(self.s.model, 'complete', side_effect=model), patch.object(self.s, 'chat', side_effect=AssertionError('child chat called')):
            self.s.parent.begin({'childId': child or self.a, 'requestId': rid, 'text': '最近怎么样'})
            for _ in range(200):
                snapshot = self.s.parent.snapshot(child or self.a)
                if not snapshot['pending']:
                    return snapshot, contexts
                time.sleep(.005)
        self.fail('parent query did not finish')

    def test_read_only_query_and_activity_leave_all_child_state_unchanged(self):
        before = self.child_state()
        result, contexts = self.ask(self.response(activity={'title': '纸的形状', 'materials': '一张纸', 'steps': '把纸平放，再折起比较。', 'adultAction': '成人帮忙折纸。', 'why': '围绕刚才的问题观察。'}))
        self.assertEqual(result['lastRequest']['status'], 'completed')
        self.assertEqual(self.child_state(), before)
        self.assertEqual([p for p, _ in contexts], ['parent_select', 'parent_answer'])
        self.assertEqual(self.s.parent.snapshot(self.b)['messages'], [])
        self.assertEqual(result['messages'][-1]['sources'][0]['url'], '/memory?conversation=ca&source=uca')

    def test_time_window_filters_messages_even_in_selected_conversation(self):
        result, contexts = self.ask()
        evidence = contexts[-1][1]['evidence']
        self.assertEqual([e['id'] for e in evidence], ['uca'])
        self.assertEqual(result['lastRequest']['status'], 'completed')
        result, contexts = self.ask(self.response(answer='该时段没有可用资料。', sources=[]), self.selector(mode='range', fromDate='2019-01-01', toDate='2019-01-02'))
        self.assertEqual(contexts[-1][1]['evidence'], [])

    def test_foreign_selection_and_fabricated_citation_cannot_escape_profile(self):
        result, contexts = self.ask(self.response(sources=['ucb']), self.selector(conversationIds=['cb']))
        self.assertEqual(contexts[-1][1]['evidence'], [])
        self.assertEqual(result['lastRequest']['status'], 'failed')
        with self.assertRaises(ProductError):
            self.s.parent.begin({'childId': self.a, 'requestId': 'parent_foreign', 'text': '依据', 'recordId': 'ucb'})

    def test_current_parent_correction_keeps_dated_provenance_without_old_answer(self):
        with self.s.store.transaction() as db:
            db['memoryItems']['corrected'] = {'id': 'corrected', 'childId': self.a, 'type': 'dialogue_memory',
                'kind': 'confusion', 'status': 'parent_confirmed', 'scope': 'conversation', 'conversationId': 'ca',
                'summary': '只说没明白，并未说明具体难点', 'quote': '我没明白为什么会倒', 'version': 2,
                'sourceMessageIds': ['uca'], 'parentEdited': True, 'history': [{'summary': '旧推断'}], 'updatedAt': stamp()}
        result, contexts = self.ask(self.response(sources=['corrected']), self.selector(memoryIds=['corrected']))
        self.assertEqual(result['lastRequest']['status'], 'completed')
        self.assertEqual(contexts[-1][1]['evidence'][0]['summary'], '只说没明白，并未说明具体难点')
        self.assertNotIn('uca', [e['id'] for e in contexts[-1][1]['evidence']])
        self.assertFalse(result['messages'][-1]['stale'])

    def test_draft_requires_explicit_save_and_duplicate_save_is_idempotent(self):
        before = self.child_state()
        draft = {'action': 'add', 'memoryId': '', 'summary': '用熟悉的小车说明', 'topic': '滚动', 'scope': 'topic'}
        result, _ = self.ask(self.response(draft=draft))
        self.assertEqual(self.child_state(), before)
        message = result['messages'][-1]
        data = {'childId': self.a, 'messageId': message['id'], 'summary': '家长编辑后的提醒', 'topic': '滚动'}
        saved = self.s.parent.save_draft(data)
        after = self.child_state()
        self.assertEqual(self.s.parent.save_draft(data), saved)
        self.assertEqual(self.child_state(), after)
        self.assertEqual(self.s.store.read()['memoryItems'][saved['memoryId']]['summary'], data['summary'])
        with self.assertRaises(ProductError):
            self.s.parent.save_draft({**data, 'childId': self.b})

    def test_edit_conflict_keeps_draft_and_reuses_existing_version_mechanism(self):
        item = self.s.add_reminder({'childId': self.a, 'summary': '原提醒'})
        draft = {'action': 'edit', 'memoryId': item['id'], 'summary': '拟修改', 'topic': '', 'scope': 'general'}
        result, _ = self.ask(self.response(draft=draft), self.selector(memoryIds=[item['id']]))
        self.s.update_memory(item['id'], {'childId': self.a, 'expectedVersion': 1, 'summary': '其他窗口修改'})
        data = {'childId': self.a, 'messageId': result['messages'][-1]['id'], 'summary': '拟修改', 'expectedVersion': 1}
        with self.assertRaises(ProductError) as caught:
            self.s.parent.save_draft(data)
        self.assertEqual(caught.exception.code, 'memory_conflict')
        self.assertEqual(self.s.store.read()['parentMessages'][data['messageId']]['draft']['status'], 'pending')
        self.assertEqual(self.s.parent.snapshot(self.a)['messages'][-1]['draft']['summary'], '拟修改')
        self.s.parent.save_draft({**data, 'expectedVersion': 2})
        self.assertEqual(self.s.store.read()['memoryItems'][item['id']]['version'], 3)

    def test_activity_invalidation_tracks_actual_dependencies_only(self):
        used = self.s.add_reminder({'childId': self.a, 'summary': '轻物，桌边观察'})
        other = self.s.add_reminder({'childId': self.a, 'summary': '另一个无关提醒'})
        activity = {'title': '比较纸的形状', 'materials': '纸', 'steps': '平放与折起比较。', 'adultAction': '成人折纸。', 'why': '围绕提问观察。'}
        result, _ = self.ask(self.response(activity=activity, sources=[used['id']]), self.selector(memoryIds=[used['id']]))
        self.assertIsNotNone(result['activity'])
        self.s.update_memory(other['id'], {'childId': self.a, 'action': 'withdraw'})
        self.assertIsNotNone(self.s.parent.snapshot(self.a)['activity'])
        self.s.update_memory(used['id'], {'childId': self.a, 'action': 'withdraw'})
        self.assertIsNone(self.s.parent.snapshot(self.a)['activity'])
        self.assertTrue(self.s.parent.snapshot(self.a)['messages'][-1]['stale'])

    def test_network_does_not_hold_store_lock_and_cancellation_discards_late_result(self):
        entered, release = threading.Event(), threading.Event()
        def model(context, request_id, purpose='chat'):
            if purpose == 'parent_select':
                entered.set(); release.wait(2)
                return self.selector(), {}
            self.fail('cancelled request must not call answer')
        before = self.child_state()
        data = {'childId': self.a, 'requestId': 'parent_delayed', 'text': '最近'}
        with patch.object(self.s.model, 'complete', side_effect=model) as mocked:
            self.s.parent.begin(data)
            self.assertTrue(entered.wait(1))
            self.s.parent.begin(data)  # same request, no second model call
            read_done = threading.Event()
            threading.Thread(target=lambda: (self.s.store.read(), read_done.set())).start()
            self.assertTrue(read_done.wait(.5))
            self.s.parent.cancel(data)
            release.set()
            time.sleep(.05)
            self.assertEqual(mocked.call_count, 1)
        self.assertEqual(self.child_state(), before)
        self.assertEqual(len(self.s.parent.snapshot(self.a)['messages']), 1)


if __name__ == '__main__':
    unittest.main()
