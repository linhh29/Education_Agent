"""Lifecycle checks use a fake provider; semantic quality is checked through UI."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import app
from companion import CompanionService, ProductError
from memory_support import validate_summary, summary_input


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'config').mkdir()
        config = json.loads((Path(app.ROOT)/'config/runtime.json').read_text())
        config['key_file'] = str(root/'never-created-offline-key')
        (root/'config/runtime.json').write_text(json.dumps(config))
        self.root = root
        self.service = CompanionService(root, root/'db.json', app.seed_db)
        self.addCleanup(self.service.close)
        self.child = self.service.save_profile({'nickname':'合成记忆验证','age':6,'kind':'test'})['id']
        self.other = self.service.save_profile({'nickname':'合成隔离验证','age':5,'kind':'test'})['id']
        self.seq = 0
        self.select = []
        self.memories = []
        self.answer_text = '旧讲法留下的特殊描述'
        self.contexts = []
        self.service.model.complete = Mock(side_effect=self.complete)

    def complete(self, context, request_id, purpose='chat'):
        self.contexts.append((purpose,context))
        meta = {'model':'qwen3.8-max-0902','durationMs':2,'callId':'offline'}
        if purpose=='recall':
            return {'intent':'ordinary','memoryIds':list(self.select),'conversationIds':[]},meta
        if purpose=='exploration':
            ids = [m['id'] for m in context['messages']]
            return {'topic':'合成探索','focus':{'text':'讨论了一个关系','sourceMessageIds':ids[:1]},'difficulties':[],
                    'attempts':[{'text':'尝试解释关系','sourceMessageIds':ids[-1:]}],'openQuestions':[]},meta
        return {'answer':self.answer_text,'topic':'合成话题','memory':list(self.memories),'usedMemoryIds':list(self.select),
                'suggestion':{'title':'看看纸片','steps':'请家长一起看看两张纸片。','why':'比较形状。'},'needsParent':False},meta

    def req(self,text='一个合成问题',**extra):
        self.seq+=1
        return {'childId':self.child,'requestId':'offline_memory_%04d'%self.seq,'userText':text,**extra}

    def reminder(self,topic='合成话题',summary='先解释实际关系',child=None):
        return self.service.add_reminder({'childId':child or self.child,'topic':topic,'summary':summary})

    def update(self,item,action='edit',**fields):
        return self.service.update_memory(item['id'],{'childId':self.child,'action':action,**fields})

    def wait_summary(self,cid):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            exp=self.service.store.read()['conversations'][cid].get('exploration',{})
            if exp.get('status')!='pending':return exp
            time.sleep(.01)
        self.fail('bounded offline summary did not finish')

    def test_catalog_reaches_beyond_old_twelve_then_only_selected_versions_enter_answer(self):
        target=self.reminder('目标语义','早先的真实提醒')
        for n in range(20):self.reminder('无关话题%d'%n,'规模测试提醒')
        foreign=self.reminder(child=self.other)
        self.select=[target['id'],foreign['id'],'invented']
        reply=self.service.chat(self.req('换一种说法问问题'))
        recall=self.contexts[0][1]
        answer=self.contexts[1][1]
        self.assertEqual(len(recall['topicRecords']),21)
        self.assertEqual([x['id'] for x in answer['candidateMemories']],[target['id']])
        saved=reply['snapshot']['messages'][-1]
        self.assertEqual(saved['providedMemoryVersions'],[{'id':target['id'],'version':1}])
        self.assertEqual(saved['usedMemoryIds'],[target['id']])

    def test_parent_edit_invalidates_suggestion_and_removes_old_body_from_future_context(self):
        self.answer_text = '更早的独立解释'
        self.service.chat(self.req('更早的不依赖记忆的建议'))
        self.answer_text = '旧讲法留下的特殊描述'
        reminder=self.reminder();self.select=[reminder['id']]
        first=self.service.chat(self.req())['snapshot']
        self.assertIsNotNone(first['suggestion'])
        revised=self.update(reminder,summary='修改后的讲法')
        self.assertEqual(revised['version'],2)
        self.assertIsNone(self.service.snapshot(self.child)['suggestion'])
        self.assertTrue(self.service.snapshot(self.child)['suggestionStale'])
        self.service.chat(self.req('接着聊'))
        context=self.contexts[-1][1]
        self.assertNotIn('旧讲法留下的特殊描述',json.dumps(context,ensure_ascii=False))
        self.assertEqual(context['candidateMemories'][0]['summary'],'修改后的讲法')
        restored=self.update(reminder,'restore')
        self.assertEqual(restored['version'],3)
        self.assertEqual(restored['summary'],reminder['summary'])

    def test_withdrawn_source_is_not_sent_as_history_and_unrelated_record_survives(self):
        self.memories=[{'kind':'confusion','quote':'还不明白','summary':'这次未明白','scope':'topic','evidenceType':'explicit_confusion','relation':'new','relatedMemoryIds':[]}]
        data=self.service.chat(self.req('还不明白'))['snapshot']
        item=data['memories'][0]
        unrelated=self.reminder('别的主题','保留这条提醒')
        self.update(item,'withdraw')
        self.memories=[]
        self.service.chat(self.req('新的问题'))
        context=self.contexts[-1][1]
        self.assertEqual(context['history'],[])
        self.assertEqual(self.service.store.read()['memoryItems'][unrelated['id']]['status'],'parent_confirmed')
        self.assertEqual(self.service.store.read()['memoryItems'][item['id']]['status'],'withdrawn')

    def test_edit_during_selection_cancels_without_sending_old_info_to_answer(self):
        item=self.reminder()
        def changed(context,rid,purpose='chat'):
            self.assertEqual(purpose,'recall')
            self.update(item,summary='在途修改')
            return {'intent':'ordinary','memoryIds':[item['id']],'conversationIds':[]},{'durationMs':1}
        self.service.model.complete=Mock(side_effect=changed)
        result=self.service.chat(self.req())
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.service.model.complete.call_count,1)

    def test_recall_failure_does_not_block_ordinary_answer_or_claim_personal_memory(self):
        self.reminder()
        def fail_recall(context,rid,purpose='chat'):
            if purpose=='recall':raise ProductError('受控检索失败',504,'model_timeout')
            return self.complete(context,rid,purpose)
        self.service.model.complete=Mock(side_effect=fail_recall)
        data=self.service.chat(self.req())['snapshot']
        self.assertEqual(data['messages'][-1]['retrieval']['status'],'unavailable')
        context=self.contexts[-1][1]
        self.assertEqual(context['memoryStatus'],'unavailable')
        self.assertEqual(context['candidateMemories'],[])

    def test_permission_failure_does_not_attempt_a_second_provider_call(self):
        self.reminder()
        self.service.model.complete=Mock(side_effect=ProductError('权限不可用',503,'model_permission'))
        with self.assertRaises(ProductError):self.service.chat(self.req())
        self.assertEqual(self.service.model.complete.call_count,1)

    def test_quote_recall_excludes_withdrawn_source_and_keeps_original_on_disk(self):
        self.memories=[{'kind':'confusion','quote':'原来的困惑','summary':'当时没明白','scope':'topic','evidenceType':'explicit_confusion','relation':'new','relatedMemoryIds':[]}]
        first=self.service.chat(self.req('原来的困惑'))['snapshot']
        item=first['memories'][0];cid=first['activeConversation']['id']
        self.update(item,'withdraw')
        self.memories=[]
        # A fresh conversation can never see the withdrawn quote through recall.
        with self.service.store.transaction() as db:db['conversations'][cid]['endedAt']='offline'
        original=self.complete
        def quotes(context,rid,purpose='chat'):
            if purpose=='recall':return {'intent':'quotes','memoryIds':[],'conversationIds':[cid]},{'durationMs':1}
            return original(context,rid,purpose)
        self.service.model.complete=Mock(side_effect=quotes)
        self.service.chat(self.req('原来我说过什么？'))
        answer=self.contexts[-1][1]
        self.assertNotIn('原来的困惑',json.dumps(answer,ensure_ascii=False))
        self.assertEqual(self.service.store.read()['messages'][item['sourceMessageIds'][0]]['text'],'原来的困惑')

    def test_local_preference_does_not_delete_general_and_expires_in_new_conversation(self):
        general=self.reminder('', '一般可以用故事')
        self.select=[general['id']]
        self.memories=[{'kind':'preference','quote':'这次想简短些','summary':'本次希望简短','scope':'general','evidenceType':'explicit_preference','relation':'local_change','relatedMemoryIds':[general['id']]}]
        data=self.service.chat(self.req('这次想简短些'))['snapshot']
        item=next(x for x in data['memories'] if x['kind']=='preference')
        self.assertEqual(item['scope'],'conversation')
        self.assertEqual(self.service.store.read()['memoryItems'][general['id']]['status'],'parent_confirmed')
        self.assertNotIn(item['id'],[x['id'] for x in self.service.candidates(self.service.store.read(),self.child,'','new-conversation')])

    def test_summary_is_async_reused_and_invalidated_after_dependency_edit(self):
        item=self.reminder();self.select=[item['id']]
        data=self.service.chat(self.req())['snapshot'];cid=data['activeConversation']['id']
        self.service.end_conversation({'childId':self.child,'conversationId':cid})
        exp=self.wait_summary(cid)
        self.assertEqual(exp['status'],'ready')
        self.assertEqual(exp['memoryVersions'],[{'id':item['id'],'version':1}])
        before=self.service.model.complete.call_count
        result=self.service.request_summary({'childId':self.child,'conversationId':cid})
        self.assertTrue(result['reused'])
        self.assertEqual(before,self.service.model.complete.call_count)
        self.update(item,summary='新的家长要求')
        self.assertEqual(self.service.snapshot(self.child)['conversations'][0]['exploration']['status'],'stale')

    def test_late_summary_cannot_overwrite_parent_edit_or_restart(self):
        item=self.reminder();self.select=[item['id']]
        cid=self.service.chat(self.req())['snapshot']['activeConversation']['id']
        started,release,finished=threading.Event(),threading.Event(),threading.Event()
        original=self.complete
        def delayed(context,rid,purpose='chat'):
            if purpose=='exploration':started.set();release.wait(2)
            value=original(context,rid,purpose)
            finished.set()
            return value
        self.service.model.complete=Mock(side_effect=delayed)
        self.service.request_summary({'childId':self.child,'conversationId':cid})
        self.assertTrue(started.wait(1))
        self.update(item,summary='较新的修改')
        release.set();self.assertTrue(finished.wait(1))
        self.assertEqual(self.wait_summary(cid)['status'],'stale')
        restarted=CompanionService(self.root,self.service.store.path,app.seed_db)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.snapshot(self.child)['conversations'][0]['exploration']['status'],'stale')

    def test_summary_rejects_invented_or_foreign_source_ids(self):
        with self.assertRaises(ValueError):
            validate_summary({'topic':'主题','focus':{'text':'无依据','sourceMessageIds':['foreign']},'difficulties':[],'attempts':[],'openQuestions':[]},[{'id':'local'}])

    def test_exploration_recall_supplies_and_records_current_summary_version(self):
        cid=self.service.chat(self.req())['snapshot']['activeConversation']['id']
        self.service.end_conversation({'childId':self.child,'conversationId':cid})
        exp=self.wait_summary(cid)
        original=self.complete
        def recall(context,rid,purpose='chat'):
            if purpose=='recall':return {'intent':'exploration','memoryIds':[],'conversationIds':[cid,'foreign']},{'durationMs':1}
            return original(context,rid,purpose)
        self.service.model.complete=Mock(side_effect=recall)
        result=self.service.chat(self.req('上次聊到哪里'))['snapshot']['messages'][-1]
        context=self.contexts[-1][1]
        self.assertEqual([e['conversationId'] for e in context['explorations']],[cid])
        self.assertEqual(context['sourceQuotes'],[])
        self.assertEqual(result['providedExplorationVersions'],[{'conversationId':cid,'version':exp['version'],'fingerprint':exp['fingerprint']}])

    def test_new_understanding_keeps_old_confusion_and_duplicate_does_not_grow_cards(self):
        self.memories=[{'kind':'confusion','quote':'我还不明白','summary':'这次困惑','scope':'topic','evidenceType':'explicit_confusion','relation':'new','relatedMemoryIds':[]}]
        first=self.service.chat(self.req('我还不明白'))['snapshot']
        old=first['memories'][0]
        self.select=[old['id']]
        self.memories=[{'kind':'understanding','quote':'我来说说关系','summary':'这次用自己的话表达关系','scope':'topic','evidenceType':'own_explanation','relation':'supplement','relatedMemoryIds':[old['id']]}]
        second=self.service.chat(self.req('我来说说关系'))['snapshot']
        new=next(m for m in second['memories'] if m['kind']=='understanding')
        self.assertEqual(self.service.store.read()['memoryItems'][old['id']]['status'],'observed')
        self.select=[new['id']]
        self.memories=[{**self.memories[0],'relation':'duplicate','relatedMemoryIds':[new['id']]}]
        third=self.service.chat(self.req('我来说说关系'))['snapshot']
        self.assertEqual(len(third['memories']),2)
        self.update(old,'withdraw')
        self.assertEqual(self.service.store.read()['memoryItems'][old['id']]['status'],'withdrawn')


if __name__=='__main__':unittest.main()
