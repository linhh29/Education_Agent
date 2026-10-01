"""Regressions for the live product. No real provider calls or child data."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import app
from companion import CompanionService, ModelClient, ProductError, safe_suggestion

ROOT = Path(__file__).resolve().parents[1]


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = CompanionService(ROOT, Path(self.temp.name) / 'db.json', app.seed_db)
        self.child = self.service.save_profile({'nickname':'合成甲', 'age':5})['id']
        self.other = self.service.save_profile({'nickname':'合成乙', 'age':6})['id']
        self.counter = 0

    def request(self, text='一个自由问题', **extra):
        self.counter += 1
        return {'childId':self.child, 'requestId':'test_request_%08d'%self.counter, 'userText':text, **extra}

    @staticmethod
    def answer(memory=None, used=None):
        memory = [{**m, 'scope':m.get('scope','topic'), 'evidenceType':{'confusion':'explicit_confusion','understanding':'own_explanation','preference':'explicit_preference'}[m['kind']],
                   'relation':m.get('relation','new'), 'relatedMemoryIds':m.get('relatedMemoryIds',[])} for m in memory or []]
        return ({'answer':'一个自然回答，可以长也可以短。', 'topic':'自由话题', 'memory':memory, 'usedMemoryIds':used or []},
                {'model':'qwen3.8-max-0902','durationMs':15,'callId':'fake_offline'})

    def chat(self, req, answer=None):
        def complete(context, request_id, purpose='chat'):
            if purpose=='recall':
                return {'intent':'ordinary','memoryIds':[],'conversationIds':[]},{'durationMs':1}
            return answer or self.answer()
        with patch.object(self.service.model, 'complete', side_effect=complete) as model:
            result = self.service.chat(req)
            self.assertEqual(sum(c.kwargs.get('purpose','chat')=='chat' for c in model.call_args_list),1)
            return result

    def test_ordinary_answer_does_not_imply_understanding(self):
        result = self.chat(self.request())
        self.assertEqual(len(result['snapshot']['messages']),2)
        self.assertEqual(result['snapshot']['memories'],[])

    def test_activity_guard_checks_each_action_not_the_whole_negative_sentence(self):
        allowed = ['由家长拿叶子，只看，不品尝。', '不要尝或舔叶子，只观察。', '家长拿手电，注意不要照眼睛。', '只看形状，不用刀，也不要点火。']
        denied = ['先品尝叶子。', '不要品尝叶子，然后尝一下。', '不要舔叶子，但可以品尝。', '不要看而是尝一下。', '不能不品尝。', '由家长点火，孩子不要品尝。', '不要照眼睛，然后把手电照向孩子眼睛。', '不怕开水，倒一点。', '不要品尝，独自在马路边观察。']
        for steps in allowed + denied:
            with self.subTest(steps=steps):
                self.assertEqual(safe_suggestion({'title': '一起观察', 'steps': steps, 'why': '观察'}) is not None, steps in allowed)

    def test_child_activity_uses_the_shared_guard_without_changing_the_answer(self):
        for steps, allowed in [('由家长拿叶子，只看，不品尝。', True), ('不要品尝，接着尝一下叶子。', False)]:
            response, meta = self.answer()
            response['suggestion'] = {'title': '观察叶子', 'steps': steps, 'why': '观察颜色'}
            result = self.chat(self.request(), (response, meta))
            answer = result['snapshot']['messages'][-1]
            self.assertEqual(answer['text'], response['answer'])
            self.assertEqual(bool(answer.get('suggestion')), allowed)

    def test_exact_quote_grounds_memory_and_invalid_quote_is_discarded(self):
        req = self.request('我没明白其中的关系')
        valid = {'kind':'confusion','quote':'没明白其中的关系','summary':'这次关系还没有讲明白'}
        invalid = {'kind':'understanding','quote':'我已经会了','summary':'无原话依据'}
        data = self.chat(req,self.answer([valid,invalid]))['snapshot']
        self.assertEqual(len(data['memories']),1)
        self.assertEqual(data['memories'][0]['quote'],valid['quote'])
        self.assertEqual(len(data['memories'][0]['sourceMessageIds']),2)

    def test_duplicate_request_does_not_call_model_or_append_messages(self):
        req = self.request()
        self.chat(req)
        with patch.object(self.service.model,'complete') as model:
            duplicate = self.service.chat(req)
            model.assert_not_called()
        self.assertEqual(len(duplicate['snapshot']['messages']),2)

    def test_retry_reuses_original_user_message(self):
        req = self.request()
        with patch.object(self.service.model,'complete',side_effect=ProductError('受控超时',504,'model_timeout')):
            with self.assertRaises(ProductError): self.service.chat(req)
        snapshot = self.service.snapshot(self.child)
        self.assertEqual(snapshot['messages'][0]['status'],'failed')
        retried = self.chat(self.request(retryOf=req['requestId']))['snapshot']
        self.assertEqual(len(retried['messages']),2)
        self.assertEqual(retried['lastRequest']['attempt'],2)

    def test_foreign_conversation_and_record_are_rejected(self):
        data = self.chat(self.request())['snapshot']
        conv = data['activeConversation']['id']
        with self.assertRaises(ProductError): self.service.begin_chat(self.request(childId=self.other, conversationId=conv))
        reminder = self.service.add_reminder({'childId':self.child,'summary':'保持自然的讲法','topic':'相关话题'})
        with self.assertRaises(ProductError): self.service.update_memory(reminder['id'],{'childId':self.other,'summary':'改写'})
        self.assertEqual(self.service.snapshot(self.other)['messages'],[])

    def test_cancel_discards_late_answer_and_preserves_input(self):
        req = self.request()
        started, release = threading.Event(), threading.Event()
        def delayed(*args):
            started.set();release.wait(2);return self.answer()
        result = []
        with patch.object(self.service.model,'complete',side_effect=delayed):
            thread = threading.Thread(target=lambda:result.append(self.service.chat(req)))
            thread.start();self.assertTrue(started.wait(1))
            self.service.cancel({'childId':self.child,'requestId':req['requestId']})
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['cancelled'])
        self.assertEqual(len(self.service.snapshot(self.child)['messages']),1)

    def test_parent_update_invalidates_inflight_context(self):
        req = self.request()
        context,pending = self.service.begin_chat(req)
        self.service.add_reminder({'childId':self.child,'summary':'对相关关系再慢一点解释'})
        with patch.object(self.service.model,'complete') as model:
            result = self.service.chat(req)
            model.assert_not_called()
        self.assertEqual(result['request']['status'],'cancelled')
        self.assertEqual(len(result['snapshot']['messages']),1)

    def test_edit_withdraw_delete_restore_are_consistent(self):
        item = self.service.add_reminder({'childId':self.child,'summary':'原提醒','topic':'话题甲'})
        def update(action,**fields):return self.service.update_memory(item['id'],{'childId':self.child,'action':action,**fields})
        update('edit',summary='新提醒',topic='话题甲')
        db = self.service.store.read()
        self.assertEqual(self.service.candidates(db,self.child,'新问题','')[0]['summary'],'新提醒')
        update('withdraw')
        self.assertEqual(self.service.candidates(self.service.store.read(),self.child,'新问题',''),[])
        update('restore')
        self.assertEqual(self.service.snapshot(self.child)['memories'][0]['summary'],'新提醒')
        update('delete')
        self.assertEqual(self.service.snapshot(self.child)['memories'],[])
        update('restore')
        self.assertEqual(self.service.snapshot(self.child)['memories'][0]['status'],'parent_confirmed')

    def test_deleting_source_removes_dependent_memory_until_conversation_restored(self):
        req = self.request('我可以说说这个关系')
        memory = {'kind':'understanding','quote':req['userText'],'summary':'记录这一次表达'}
        data = self.chat(req,self.answer([memory]))['snapshot']
        conv,mem = data['activeConversation']['id'],data['memories'][0]['id']
        self.service.conversation_action({'childId':self.child,'conversationId':conv})
        self.service.conversation_action({'childId':self.child,'conversationId':conv})
        self.assertEqual(self.service.snapshot(self.child)['memories'],[])
        self.assertEqual(self.service.snapshot(self.child)['messages'],[])
        with self.assertRaises(ProductError): self.service.update_memory(mem,{'childId':self.child,'action':'restore'})
        self.service.conversation_action({'childId':self.child,'conversationId':conv,'restore':True})
        self.assertEqual(len(self.service.snapshot(self.child)['memories']),1)
        self.assertEqual(len(self.service.snapshot(self.child)['messages']),2)

    def test_deleted_pending_conversation_restores_stopped_input(self):
        req = self.request()
        _,pending = self.service.begin_chat(req)
        self.service.conversation_action({'childId':self.child,'conversationId':pending['conversationId']})
        self.service.conversation_action({'childId':self.child,'conversationId':pending['conversationId'],'restore':True})
        data = self.service.snapshot(self.child)
        self.assertIsNone(data['pending'])
        self.assertEqual(data['messages'][0]['status'],'cancelled')

    def test_archive_restore_and_restart_preserve_records(self):
        self.chat(self.request())
        self.service.archive_profile({'childId':self.child})
        with self.assertRaises(ProductError): self.service.snapshot(self.child)
        self.service.archive_profile({'childId':self.child,'restore':True})
        restored = CompanionService(ROOT,self.service.store.path,app.seed_db)
        self.assertEqual(len(restored.snapshot(self.child)['messages']),2)

    def test_restart_marks_pending_input_retryable(self):
        req = self.request()
        self.service.begin_chat(req)
        restarted = CompanionService(ROOT,self.service.store.path,app.seed_db)
        data = restarted.snapshot(self.child)
        self.assertEqual(data['lastRequest']['code'],'server_restarted')
        self.assertEqual(data['messages'][0]['status'],'failed')


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name);(root/'config').mkdir()
        config = json.loads((ROOT/'config/runtime.json').read_text())
        self.fake_key = root/'fake_test_key';self.fake_key.write_text('offline-auth-placeholder')
        config['key_file']=str(self.fake_key)
        (root/'config/runtime.json').write_text(json.dumps(config))
        self.client = ModelClient(root)

    def test_missing_key_blocks_without_zero_cost_network_attempt(self):
        self.fake_key.unlink()
        with patch('companion.build_opener') as opener:
            with self.assertRaises(ProductError):self.client.complete({'currentText':'合成问题'},'test_request')
            opener.assert_not_called()
        self.assertEqual(self.client.occupied(),2.735356)

    def test_other_confirmed_spending_reduces_headroom_and_cannot_be_reset(self):
        config_path=self.client.root/'config/runtime.json'
        config=json.loads(config_path.read_text())
        config['budget']['prior_cny']+=1
        config_path.write_text(json.dumps(config))
        updated=ModelClient(self.client.root)
        self.assertAlmostEqual(updated.occupied(),3.735356)
        self.assertAlmostEqual(updated.limit(),49.735356)
        config['budget']['prior_cny']=2.735356
        config_path.write_text(json.dumps(config))
        self.assertAlmostEqual(ModelClient(self.client.root).occupied(),3.735356)

    def test_unknown_timeout_keeps_positive_reservation_and_no_automatic_retry(self):
        with patch('companion.build_opener') as opener:
            opener.return_value.open.side_effect=URLError('controlled offline timeout')
            with self.assertRaises(ProductError):self.client.complete({'currentText':'合成问题'},'test_request')
            self.assertEqual(opener.return_value.open.call_count,1)
        call = list(self.client.ledger.read()['calls'].values())[0]
        self.assertGreater(call['occupiedCny'],0)
        self.assertEqual(call['costBasis'],'unknown_usage_keep_full_reservation')

    def test_budget_exhaustion_blocks_before_network(self):
        for occupancy,code in [(47,'budget_exhausted'),(float('nan'),'budget_ledger')]:
            with self.subTest(code=code):
                with self.client.ledger.transaction() as ledger:
                    ledger['calls']['previous']={'occupiedCny':occupancy}
                with patch('companion.build_opener') as opener:
                    with self.assertRaises(ProductError) as exc:self.client.complete({'currentText':'合成问题'},'test_request')
                    self.assertEqual(exc.exception.code,code);opener.assert_not_called()

    def test_usage_is_settled_and_configuration_is_sent(self):
        result={'choices':[{'message':{'content':json.dumps({'answer':'自然回答','topic':'合成话题','memory':[], 'usedMemoryIds':[], 'needsParent':False,'suggestion':None})},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':700,'completion_tokens':500}}
        with patch('companion.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps(result).encode()
            answer,meta=self.client.complete({'currentText':'合成问题'},'test_request')
            payload=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(payload['model'],'qwen3.8-max-0902')
        self.assertEqual(payload['enable_thinking'],self.client.config['enable_thinking'])
        self.assertEqual(payload['max_completion_tokens'],self.client.config['max_completion_tokens'])
        self.assertNotIn('tools',payload)
        call=list(self.client.ledger.read()['calls'].values())[0]
        self.assertAlmostEqual(call['occupiedCny'],.0264)
        self.assertEqual(call['attempt'],1)

    def test_incomplete_response_is_not_shown_or_automatically_repaired(self):
        response={'choices':[{'message':{'content':json.dumps({'answer':'不完整的片段'})},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':700,'completion_tokens':50}}
        with patch('companion.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            with self.assertRaises(ProductError) as error:
                self.client.complete({'currentText':'合成问题'},'test_request')
            self.assertEqual(error.exception.code,'model_format')
            self.assertEqual(opener.return_value.open.call_count,1)
        record=list(self.client.ledger.read()['calls'].values())[0]
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['error'],'model_format')
        self.assertGreater(record['occupiedCny'],0)

    def test_summary_schema_constrains_ids_without_constraining_text_and_shares_budget(self):
        summary={'topic':'一次探索','focus':{'text':'讨论了一个问题','sourceMessageIds':['msg_source']},'difficulties':[],'attempts':[],'openQuestions':[]}
        response={'choices':[{'message':{'content':json.dumps(summary)},'finish_reason':'stop'}],'usage':{'prompt_tokens':500,'completion_tokens':100}}
        with patch('companion.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            self.client.complete({'messages':[{'id':'msg_source','role':'user','text':'合成问题'}]},'summary_offline',purpose='exploration')
            payload=json.loads(opener.return_value.open.call_args.args[0].data)
        props=payload['response_format']['json_schema']['schema']['properties']
        self.assertNotIn('enum',props['topic'])
        self.assertNotIn('enum',props['focus']['properties']['text'])
        self.assertEqual(props['focus']['properties']['sourceMessageIds']['items']['enum'],['msg_source'])
        self.assertEqual(payload['model'],'qwen3.8-max-0902')
        self.assertEqual(payload['max_completion_tokens'],1100)
        record=list(self.client.ledger.read()['calls'].values())[0]
        self.assertEqual(record['purpose'],'exploration')
        self.assertGreater(record['occupiedCny'],0)


if __name__=='__main__':unittest.main()
