"""Speech regressions use synthetic PCM and controlled providers, never a microphone."""
import base64
import io
import json
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import app
from companion import CompanionService, ModelClient, ProductError
from speech_support import SpeechClient, split_text, wav_info

ROOT=Path(__file__).resolve().parents[1]

def wav(seconds=.2, rate=16000, silent=False):
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate)
        f.writeframes((b'\0\0' if silent else b'\0\x10')*round(seconds*rate))
    return out.getvalue()

class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir()
        c=json.loads((ROOT/'config/runtime.json').read_text());c['key_file']=str(self.root/'dummy-key')
        (self.root/'config/runtime.json').write_text(json.dumps(c));(self.root/'dummy-key').write_text('controlled-test-key')
        self.s=CompanionService(self.root,self.root/'db.json',app.seed_db);self.addCleanup(self.s.close)
        self.child=self.s.save_profile({'nickname':'合成语音甲'})['id'];self.other=self.s.save_profile({'nickname':'合成语音乙'})['id']
        self.n=0
    def payload(self,**extra):
        self.n+=1
        return {'requestId':'speech_test_%08d'%self.n,'childId':self.child,'audio':base64.b64encode(wav()).decode(),**extra}
    def wait(self,job):
        for _ in range(100):
            job=self.s.speech.get(self.child,job['id'])
            if job['status']!='pending':return job
            time.sleep(.01)
        self.fail('speech worker did not complete')
    def answer(self):
        with patch.object(self.s.model,'complete',return_value=({'answer':'这是一段实际交付的文字。','topic':'合成','memory':[],'usedMemoryIds':[],'suggestion':None},{'model':'controlled','durationMs':0})):
            data=self.s.chat({'childId':self.child,'requestId':'request_test_answer','userText':'合成问题'})['snapshot']
        return data['messages'][-1]
    def test_no_audio_or_invalid_wav_never_calls_provider(self):
        with patch.object(self.s.speech.client,'transcribe') as call:
            for raw in (b'',b'not a wave',wav(silent=True),wav(seconds=61),wav(rate=24000)):
                with self.assertRaises(ProductError): self.s.speech.begin('asr',self.payload(audio=base64.b64encode(raw).decode()))
            call.assert_not_called()
    def test_provider_streaming_length_is_normalized_but_uploads_remain_strict(self):
        raw=bytearray(wav(rate=24000));struct.pack_into('<I',raw,40,0x7fffff9b)
        self.assertAlmostEqual(wav_info(raw)[4],.2)
        with self.assertRaises(ProductError):wav_info(raw,recording=True)
        truncated=wav()[:-10]
        with self.assertRaises(ProductError):wav_info(truncated)
    def test_parent_conflict_keeps_current_version_and_does_not_drop_draft(self):
        memory=self.s.add_reminder({'childId':self.child,'summary':'先讲现在的问题'})
        self.s.update_memory(memory['id'],{'childId':self.child,'summary':'新的家长提醒','expectedVersion':1})
        with self.assertRaises(ProductError) as error:
            self.s.update_memory(memory['id'],{'childId':self.child,'summary':'旧页面里的草稿','expectedVersion':1})
        self.assertEqual(error.exception.code,'memory_conflict')
        self.assertEqual(self.s.store.read()['memoryItems'][memory['id']]['summary'],'新的家长提醒')
    def test_asr_is_not_a_chat_until_user_submits_and_preserves_edit_origin(self):
        with patch.object(self.s.speech.client,'transcribe',return_value='我没听懂，能举例吗？'):
            job=self.wait(self.s.speech.begin('asr',self.payload(inputKind='audio_file')))
        self.assertEqual(job['text'],'我没听懂，能举例吗？')
        self.assertEqual(self.s.snapshot(self.child)['messages'],[])
        self.assertEqual(self.s.snapshot(self.child)['memories'],[])
        request={'childId':self.child,'requestId':'request_voice_edit','userText':'我没听懂，能用纸举例吗？','transcriptId':job['id']}
        context,pending=self.s.begin_chat(request)
        user=self.s.store.read()['messages'][pending['userMessageId']]
        self.assertTrue(user['inputSource']['edited']);self.assertEqual(user['inputSource']['type'],'asr')
        self.assertEqual(user['inputSource']['transcript'],job['text'])
        self.assertEqual(context['currentText'],request['userText'])
    def test_duplicates_and_cancelled_asr_do_not_deliver_late_result(self):
        started,release=threading.Event(),threading.Event()
        def delayed(*args):started.set();release.wait(2);return '迟到的合成原文'
        p=self.payload()
        with patch.object(self.s.speech.client,'transcribe',side_effect=delayed) as call:
            job=self.s.speech.begin('asr',p);self.assertTrue(started.wait(1))
            self.s.speech.begin('asr',p);self.s.speech.cancel({'childId':self.child,'requestId':job['id']})
            release.set();time.sleep(.03)
            self.assertEqual(call.call_count,1)
        result=self.s.speech.get(self.child,job['id']);self.assertEqual(result['status'],'cancelled');self.assertNotIn('text',result)
    def test_tts_only_reads_owned_answer_and_replays_cached_bytes(self):
        message=self.answer()
        with patch.object(self.s.speech.client,'synthesize',return_value=wav()) as tts:
            p=self.payload(messageId=message['id'],text='不应发送的家长备注')
            job=self.wait(self.s.speech.begin('tts',p))
            self.assertEqual(tts.call_args.args[0],message['text'])
            again=self.s.speech.begin('tts',self.payload(messageId=message['id']))
            self.assertEqual(job['id'],again['id']);self.assertEqual(tts.call_count,1)
            self.assertEqual(self.s.speech.audio(self.child,job['id']),wav())
            with self.assertRaises(ProductError): self.s.speech.audio(self.other,job['id'])
        self.s.conversation_action({'childId':self.child,'conversationId':message['conversationId']})
        with self.assertRaises(ProductError):self.s.speech.audio(self.child,job['id'])
    def test_asr_failure_and_tts_failure_do_not_generate_answers(self):
        with patch.object(self.s.speech.client,'transcribe',side_effect=ProductError('没有识别到','422','asr_empty')),patch.object(self.s.model,'complete') as llm:
            result=self.wait(self.s.speech.begin('asr',self.payload()))
            self.assertEqual(result['code'],'asr_empty');llm.assert_not_called()
        message=self.answer()
        with patch.object(self.s.speech.client,'synthesize',side_effect=ProductError('受控失败',504,'speech_timeout')),patch.object(self.s.model,'complete') as llm:
            result=self.wait(self.s.speech.begin('tts',self.payload(messageId=message['id'])))
            self.assertEqual(result['code'],'speech_timeout');llm.assert_not_called()
            self.assertEqual(self.s.store.read()['messages'][message['id']]['text'],message['text'])
    def test_foreign_transcript_and_expired_result_rejected(self):
        with patch.object(self.s.speech.client,'transcribe',return_value='原话'):
            job=self.wait(self.s.speech.begin('asr',self.payload()))
        with self.assertRaises(ProductError):self.s.begin_chat({'childId':self.other,'requestId':'request_foreign_voice','userText':'原话','transcriptId':job['id']})
        with self.s.store.transaction() as db:db['speechRequests'][job['id']]['expiresAt']=0
        with self.assertRaises(ProductError):self.s.speech.get(self.child,job['id'])
    def test_unknown_fee_is_retained_and_voice_limit_blocks_before_network(self):
        with patch('speech_support.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError()
            with self.assertRaises(ProductError):self.s.speech.client.call('asr',{},3,'test_timeout')
        ledger=self.s.model.ledger.read();call=next(iter(ledger['calls'].values()))
        self.assertEqual(call['occupiedCny'],.00066);self.assertEqual(call['costBasis'],'unknown_usage_keep_full_reservation')
        with self.s.model.ledger.transaction() as ledger:ledger['calls']['spent']={'purpose':'tts','occupiedCny':4.9999}
        with patch('speech_support.build_opener') as opener:
            with self.assertRaises(ProductError) as err:self.s.speech.client.call('asr',{},3,'test_budget')
            self.assertEqual(err.exception.code,'speech_budget');opener.assert_not_called()
    def test_global_budget_keeps_five_yuan_manual_reserve(self):
        with self.s.model.ledger.transaction() as ledger:
            ledger['calls']['spent']={'purpose':'chat','occupiedCny':self.s.model.limit()-5-ledger['priorCny']}
        with patch('speech_support.build_opener') as opener:
            with self.assertRaises(ProductError) as err:self.s.speech.client.call('tts',{},20,'test_reserve')
            self.assertEqual(err.exception.code,'budget_exhausted');opener.assert_not_called()
    def test_audio_url_is_exact_provider_https_and_chunks_preserve_text(self):
        valid='http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/example.wav?signature=synthetic'
        self.assertTrue(SpeechClient.audio_url(valid).startswith('https://'))
        for bad in ('https://evil.example/a.wav','https://user:pass@dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav','https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com.evil.example/a.wav'):
            with self.assertRaises(ProductError):SpeechClient.audio_url(bad)
        text=('第一句，有停顿。\n第二句还在这里。'*91)+'最后的尾句'
        pieces=split_text(text);self.assertEqual(''.join(pieces),text);self.assertLessEqual(max(map(len,pieces)),500)
    def test_tts_reserves_chinese_weighted_usage_before_generating(self):
        text='这是一段合成测试文字。'*20
        response={'output':{'audio':{'url':'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.wav','expires_at':time.time()+60}}}
        with patch.object(self.s.speech.client,'call',return_value=response) as call,patch('speech_support.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=wav(rate=24000)
            self.s.speech.client.synthesize(text,'controlled_chinese_usage')
        self.assertEqual(call.call_args.args[2],len(text.encode('utf-8'))+16)
        self.assertGreater(call.call_args.args[2],len(text)*2)
    def test_restart_preserves_completed_asr_and_does_not_resume_pending_requests(self):
        with patch.object(self.s.speech.client,'transcribe',return_value='可恢复的原话'):
            job=self.wait(self.s.speech.begin('asr',self.payload()))
        with self.s.store.transaction() as db:db['speechRequests']['speech_interrupted']={**db['speechRequests'][job['id']],'id':'speech_interrupted','status':'pending'}
        restarted=CompanionService(self.root,self.root/'db.json',app.seed_db);self.addCleanup(restarted.close)
        self.assertEqual(restarted.speech.get(self.child,job['id'])['text'],'可恢复的原话')
        self.assertEqual(restarted.speech.get(self.child,'speech_interrupted')['status'],'failed')

if __name__=='__main__':unittest.main()
