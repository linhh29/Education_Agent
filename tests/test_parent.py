"""Parent isolation/ownership/lifecycle regressions; providers are controlled."""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

import app
from companion import CompanionService, ProductError, stamp
from parent_support import ParentService

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

    def ask(self, answer=None, selection=None, child=None, text='最近怎么样'):
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
            self.s.parent.begin({'childId': child or self.a, 'requestId': rid, 'text': text})
            for _ in range(200):
                snapshot = self.s.parent.snapshot(child or self.a)
                if not snapshot['pending']:
                    return snapshot, contexts
                time.sleep(.005)
        self.fail('parent query did not finish')

    def test_read_only_query_and_activity_leave_all_child_state_unchanged(self):
        before = self.child_state()
        result, contexts = self.ask(self.response(activity={'title': '纸的形状', 'materials': '一张纸', 'steps': '把纸平放，再折起比较。', 'adultAction': '成人帮忙折纸。', 'why': '围绕刚才的问题观察。'}), self.selector(activityIntent='new'))
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
        result, contexts = self.ask(self.response(answer='该时段没有可用资料。', sources=[]), self.selector(mode='range', fromDate='2019-01-01', toDate='2019-01-02', timeQuote='2019年1月1日至1月2日'), text='2019年1月1日至1月2日')
        self.assertEqual(contexts[-1][1]['evidence'], [])

    def test_parent_context_keeps_speakers_and_editors_separate(self):
        reminder = self.s.add_reminder({'childId': self.a, 'summary': '以后用家里的物品举例'})
        now = stamp()
        with self.s.store.transaction() as db:
            db['messages']['aca'] = {**db['messages']['uca'], 'id': 'aca', 'role': 'assistant', 'text': 'AI的解释'}
            db['memoryItems']['edited'] = {'id': 'edited', 'childId': self.a, 'type': 'dialogue_memory', 'kind': 'confusion',
                'status': 'parent_confirmed', 'summary': '家长修订的有限判断', 'quote': '我没明白为什么会倒', 'scope': 'conversation',
                'conversationId': 'ca', 'sourceMessageIds': ['uca'], 'sourceActor': 'child', 'parentEdited': True, 'version': 1, 'updatedAt': now}
            for role, text in [('user', '以后用家里的物品举例'), ('assistant', '旧AI误称孩子提问是家长要求')]:
                db['parentMessages'][role] = {'id': role, 'childId': self.a, 'role': role, 'text': text, 'createdAt': now}
        before = self.child_state()
        result, contexts = self.ask(selection=self.selector(memoryIds=[reminder['id'], 'edited']))
        self.assertEqual(self.child_state(), before)
        self.assertIsNone(result['messages'][-1]['draft'])
        for _, context in contexts:
            self.assertEqual(context['questionSpeaker'], 'parent')
            self.assertEqual([m['speaker'] for m in context['parentHistory']], ['parent', 'parent_assistant'])
        self.assertEqual(contexts[0][1]['directory']['conversations'][0]['questionSpeaker'], 'child')
        evidence = {m['id']: m for m in contexts[-1][1]['evidence']}
        self.assertEqual(evidence['uca']['speaker'], 'child')
        self.assertEqual(evidence['aca']['speaker'], 'child_assistant')
        self.assertEqual(evidence[reminder['id']]['sourceActor'], 'parent')
        self.assertEqual(evidence['edited']['sourceActor'], 'child')
        self.assertEqual(evidence['edited']['summaryActor'], 'parent')

    def test_empty_advice_is_omitted_on_write_and_old_reads_without_recalling_model(self):
        for value in ['', '  ', ',，。…！—\n\u200b', '看看。', '好。', '1分钟', '👀']:
            result, contexts = self.ask(self.response(advice=value))
            expected = '' if value in ['', '  ', ',，。…！—\n\u200b'] else value
            self.assertEqual(result['messages'][-1]['advice'], expected)
            self.assertEqual([p for p, _ in contexts], ['parent_select', 'parent_answer'])
        mid = result['messages'][-1]['id']
        with self.s.store.transaction() as db:
            db['parentMessages'][mid]['advice'] = ',，'
        with patch.object(self.s.model, 'complete', side_effect=AssertionError('read called model')):
            self.assertEqual(self.s.parent.snapshot(self.a)['messages'][-1]['advice'], '')
        self.assertEqual(self.s.store.read()['parentMessages'][mid]['advice'], ',，')

    def test_calendar_ranges_use_server_local_date_not_model_year(self):
        now = datetime.fromisoformat('2026-10-01T16:01:00+00:00')
        for mode, offset, start, end in [('day', 0, '2026-10-02', '2026-10-02'), ('day', -1, '2026-10-01', '2026-10-01'), ('week', -1, '2026-09-21', '2026-09-27')]:
            with self.subTest(mode=mode, offset=offset):
                first, last, _ = ParentService.window({'mode': mode, 'offset': offset, 'fromDate': '2025-01-01'}, now)
                self.assertEqual((str(first), str(last)), (start, end))
        first, last, _ = ParentService.window({'mode': 'week', 'offset': -1}, datetime.fromisoformat('2027-01-04T00:01:00+08:00'))
        self.assertEqual((str(first), str(last)), ('2026-12-28', '2027-01-03'))
        for selected in [{'mode': 'clarify'}, {'mode': 'all'}, {'mode': 'range', 'fromDate': '2025-09-21', 'toDate': '2025-09-27', 'timeQuote': '9月21日至27日'}]:
            with self.assertRaises(ValueError):
                ParentService.window(selected, now, '9月21日至27日')
        first, last, _ = ParentService.window({'mode': 'range', 'fromDate': '09-21', 'toDate': '09-27', 'timeQuote': '9月21日至27日'}, now, '9月21日至27日')
        self.assertEqual((str(first), str(last)), ('2026-09-21', '2026-09-27'))

    def test_midnight_evidence_and_revision_dates_are_separate_local_times(self):
        with self.s.store.transaction() as db:
            db['messages']['uca']['createdAt'] = '2026-10-01T15:59:00+00:00'
            db['messages']['after'] = {**db['messages']['uca'], 'id': 'after', 'text': '跨日后的提问', 'createdAt': '2026-10-01T16:01:00+00:00'}
            db['messages']['assistant_next'] = {**db['messages']['after'], 'id': 'assistant_next', 'role': 'assistant', 'text': '跨日后才回答'}
            db['memoryItems']['revised'] = {'id': 'revised', 'childId': self.a, 'type': 'dialogue_memory', 'kind': 'confusion',
                'status': 'parent_confirmed', 'summary': '家长更新', 'quote': '我没明白为什么会倒', 'scope': 'conversation', 'conversationId': 'ca',
                'sourceMessageIds': ['uca', 'assistant_next'], 'version': 2, 'parentEdited': True, 'updatedAt': '2026-10-01T17:00:00+00:00'}
        request = {'id': 'date_projection', 'childId': self.a, 'text': '今天', 'createdAt': '2026-10-01T16:02:00+00:00'}
        with patch.object(self.s.model, 'complete', return_value=(self.selector(mode='day', offset=0, memoryIds=['revised']), {})):
            context, _, _ = self.s.parent.context(request)
        self.assertEqual([e['id'] for e in context['evidence']], ['after', 'assistant_next'])
        self.assertEqual(context['evidence'][0]['createdAt'], '2026-10-02T00:01:00+08:00')
        with patch.object(self.s.model, 'complete', return_value=(self.selector(mode='day', offset=-1, memoryIds=['revised']), {})):
            context, _, _ = self.s.parent.context(request)
        record = next(e for e in context['evidence'] if e['id'] == 'revised')
        self.assertEqual(record['sourceOccurredAt'], ['2026-10-01T23:59:00+08:00'])
        self.assertEqual(record['updatedAt'], '2026-10-02T01:00:00+08:00')

    def test_activity_survives_dialogue_tail_but_not_replacement_invalidation_or_clear(self):
        reminder = self.s.add_reminder({'childId': self.a, 'summary': '桌子中间，不用玻璃'})
        activity = {'title': '纸的形状', 'materials': '纸', 'steps': '在桌子中间折纸。', 'adultAction': '家长折，孩子观察。', 'why': '看形状', 'constraints': ['不用玻璃', '桌子中间', '家长操作']}
        self.ask(self.response(activity=activity, sources=[reminder['id']]), self.selector(memoryIds=[reminder['id']], activityIntent='new'))
        for _ in range(4):
            snapshot, _ = self.ask(self.response(activity={**activity, 'title': '无关回答误带的活动'}), self.selector(activityIntent='none'))
            self.assertIsNone(snapshot['messages'][-1]['activity'])
        _, contexts = self.ask(self.response(answer='纸和桌子', sources=[]), self.selector(activityIntent='continue'))
        self.assertEqual(contexts[0][1]['currentActivity'], activity)
        self.assertEqual(contexts[-1][1]['previousActivity'], activity)
        self.assertLessEqual(len(contexts[-1][1]['parentHistory']), 6)
        self.assertEqual(self.s.parent.snapshot(self.b)['activity'], None)
        self.s.update_memory(reminder['id'], {'childId': self.a, 'action': 'withdraw'})
        _, contexts = self.ask(self.response(sources=[]), self.selector(activityIntent='continue'))
        self.assertIsNone(contexts[-1][1]['previousActivity'])
        replacement = {**activity, 'title': '新的活动', 'constraints': ['地面观察']}
        _, contexts = self.ask(self.response(activity=replacement), self.selector(activityIntent='new'))
        self.assertIsNone(contexts[-1][1]['previousActivity'])
        self.assertEqual(self.s.parent.snapshot(self.a)['activity']['constraints'], ['地面观察'])
        self.ask(self.response(answer='好，结束这项活动。'), self.selector(activityIntent='clear'))
        self.assertIsNone(self.s.parent.snapshot(self.a)['activity'])

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

    def test_request_retry_identity_includes_child_text_and_target_record(self):
        first = self.s.add_reminder({'childId': self.a, 'summary': '记录A'})
        second = self.s.add_reminder({'childId': self.a, 'summary': '记录B'})
        data = {'childId': self.a, 'requestId': 'parent_identity_001', 'text': '这条记录的依据', 'recordId': first['id']}
        with patch('parent_support.threading.Thread') as worker:
            original = self.s.parent.begin(data)
            self.assertEqual(self.s.parent.begin(data), original)
            for changed in ({'recordId': second['id']}, {'recordId': ''}, {'text': '另一个问题'}, {'childId': self.b}):
                with self.subTest(changed=changed), self.assertRaises(ProductError) as caught:
                    self.s.parent.begin({**data, **changed})
                self.assertEqual((caught.exception.status, caught.exception.code), (409, 'parent_request_conflict'))
            self.assertEqual(worker.call_count, 1)
            with self.s.store.transaction() as db:
                db['parentRequests'][data['requestId']]['status'] = 'completed'
            before = self.s.store.read()
            self.assertEqual(self.s.parent.begin(data)['status'], 'completed')
            self.assertEqual(self.s.store.read(), before)
            next_request = self.s.parent.begin({**data, 'requestId': 'parent_identity_002', 'recordId': second['id']})
            self.assertEqual(next_request['recordId'], second['id'])
            self.assertEqual(worker.call_count, 2)

    def test_conversation_draft_needs_explicit_owned_conversation_and_keeps_scope(self):
        draft = {'action': 'add', 'memoryId': '', 'summary': '只在这段聊天里举例', 'topic': '', 'scope': 'conversation'}
        result, _ = self.ask(self.response(draft=draft))
        mid = result['messages'][-1]['id']
        data = {'childId': self.a, 'messageId': mid, 'summary': draft['summary']}
        before = self.child_state()
        for conversation_id in (None, '', 'cb', 'missing'):
            with self.subTest(conversation_id=conversation_id), self.assertRaises(ProductError):
                self.s.parent.save_draft({**data, **({'conversationId': conversation_id} if conversation_id is not None else {})})
            self.assertEqual(self.child_state(), before)
            self.assertEqual(self.s.parent.snapshot(self.a)['messages'][-1]['draft']['status'], 'pending')
        with self.s.store.transaction() as db:
            db['conversations']['removed'] = {**db['conversations']['ca'], 'id': 'removed', 'deleted': True}
        with self.assertRaises(ProductError):
            self.s.parent.save_draft({**data, 'conversationId': 'removed'})
        saved = self.s.parent.save_draft({**data, 'conversationId': 'ca'})
        db = self.s.store.read()
        item = db['memoryItems'][saved['memoryId']]
        self.assertEqual((item['scope'], item['conversationId']), ('conversation', 'ca'))
        self.assertEqual(db['parentMessages'][mid]['draft']['scope'], 'conversation')
        self.assertEqual(db['parentMessages'][mid]['draft']['conversationId'], 'ca')
        self.assertIn(item['id'], [m['id'] for m in self.s.candidates(db, self.a, '举例', 'ca')])
        self.assertNotIn(item['id'], [m['id'] for m in self.s.candidates(db, self.a, '举例', 'new_conversation')])
        self.assertEqual(self.s.parent.save_draft({**data, 'conversationId': 'ca'}), saved)
        self.assertEqual(self.s.store.read(), db)

    def test_parent_draft_preserves_confirmed_topic_and_general_scope(self):
        for scope, topic in [('topic', '滚动'), ('general', ''), ('general', '只是标签')]:
            with self.subTest(scope=scope, topic=topic):
                draft = {'action': 'add', 'memoryId': '', 'summary': '家长确认的讲法', 'topic': topic, 'scope': scope}
                result, _ = self.ask(self.response(draft=draft))
                saved = self.s.parent.save_draft({'childId': self.a, 'messageId': result['messages'][-1]['id'], 'summary': draft['summary']})
                item = self.s.store.read()['memoryItems'][saved['memoryId']]
                self.assertEqual((item['scope'], item['topic']), (scope, topic))

    def test_editing_conversation_reminder_does_not_expand_or_rebind_it(self):
        item = self.s.add_reminder({'childId': self.a, 'summary': '原提醒', 'scope': 'conversation', 'conversationId': 'ca'})
        draft = {'action': 'edit', 'memoryId': item['id'], 'summary': '修改后的提醒', 'topic': '', 'scope': 'conversation'}
        result, _ = self.ask(self.response(draft=draft), self.selector(memoryIds=[item['id']]))
        data = {'childId': self.a, 'messageId': result['messages'][-1]['id'], 'summary': draft['summary']}
        with self.assertRaises(ProductError):
            self.s.parent.save_draft({**data, 'conversationId': ''})
        saved = self.s.parent.save_draft(data)
        item = self.s.store.read()['memoryItems'][saved['memoryId']]
        self.assertEqual((item['scope'], item['conversationId']), ('conversation', 'ca'))

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

    def test_refreshed_edit_draft_cannot_revive_withdrawn_or_deleted_target(self):
        for action in ('withdraw', 'delete'):
            with self.subTest(action=action):
                item = self.s.add_reminder({'childId': self.a, 'summary': '原提醒'})
                draft = {'action': 'edit', 'memoryId': item['id'], 'summary': '拟修改', 'topic': '', 'scope': 'general'}
                result, _ = self.ask(self.response(draft=draft), self.selector(memoryIds=[item['id']]))
                changed = self.s.update_memory(item['id'], {'childId': self.a, 'action': action})
                before = self.child_state()
                data = {'childId': self.a, 'messageId': result['messages'][-1]['id'], 'summary': '拟修改', 'expectedVersion': changed['version']}
                with self.assertRaises(ProductError) as caught:
                    self.s.parent.save_draft(data)
                self.assertEqual(caught.exception.code, 'parent_target_inactive')
                self.assertEqual(self.child_state(), before)
                self.assertEqual(self.s.parent.snapshot(self.a)['messages'][-1]['draft']['status'], 'pending')

    def test_activity_invalidation_tracks_actual_dependencies_only(self):
        used = self.s.add_reminder({'childId': self.a, 'summary': '轻物，桌边观察'})
        other = self.s.add_reminder({'childId': self.a, 'summary': '另一个无关提醒'})
        activity = {'title': '比较纸的形状', 'materials': '纸', 'steps': '平放与折起比较。', 'adultAction': '成人折纸。', 'why': '围绕提问观察。'}
        result, _ = self.ask(self.response(activity=activity, sources=[used['id']]), self.selector(memoryIds=[used['id']], activityIntent='new'))
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
