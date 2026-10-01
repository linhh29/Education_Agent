"""Bounded ASR/TTS adapters for the existing product, ledger and database.

Microphone bytes exist only during an ASR request. TTS caches contain only
already-delivered public answers. No provider URL or credential is persisted.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import socket
import threading
import time
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler
from companion import ProductError, NoRedirect, identifier, stamp


def wav_info(raw, recording=False):
    try:
        with wave.open(io.BytesIO(raw), 'rb') as f:
            channels, width, rate, frames = f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()
            pcm = f.readframes(frames)
            if f.getcomptype() != 'NONE' or width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise ValueError()
            declared_bytes = frames * width * channels
            if len(pcm) != declared_bytes:
                # DashScope's completed downloads can retain a streaming WAV
                # length sentinel. Count the received, aligned PCM and write a
                # normal WAV below. Uploaded recordings still require exact sizes.
                if recording or declared_bytes < 0x7fff0000 or len(pcm) % (width * channels):
                    raise ValueError()
                frames = len(pcm) // (width * channels)
            if recording and (channels != 1 or rate != 16000):
                raise ValueError()
            if not frames:
                raise ProductError('没有收到音频。可以重新录音，或直接打字。', 400, 'audio_empty')
            return channels, width, rate, pcm, frames / rate
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise ProductError('音频格式未能读取。请重新录音或选择可播放的音频文件。', 400, 'audio_format')


def split_text(text, limit=500):
    """Preserve every character; divide transport chunks, never rewrite answers."""
    chunks = []
    while len(text) > limit:
        end = max(text.rfind(p, 0, limit) for p in ('。', '！', '？', '\n', '；')) + 1
        if end < limit // 2:
            end = limit
        chunks.append(text[:end]); text = text[end:]
    if text:
        chunks.append(text)
    return chunks


class SpeechClient:
    def __init__(self, model):
        self.model, self.config = model, model.config['speech']
        c = self.config
        expected = {'asr_model':'qwen3-asr-flash-2026-02-10', 'tts_model':'qwen3-tts-flash-2025-11-27',
                    'asr_endpoint':'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                    'tts_endpoint':'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation',
                    'voice':'Serena', 'language':'Chinese'}
        if any(c.get(k) != v for k, v in expected.items()):
            raise ProductError('云语音配置与本轮授权不一致。', 503, 'speech_config')
        for k in ('timeout_seconds', 'max_recording_seconds', 'cache_seconds', 'asr_cny_per_second', 'tts_cny_per_10000_characters', 'limit_cny'):
            if isinstance(c.get(k), bool) or not isinstance(c.get(k), (int, float)) or not math.isfinite(c[k]):
                raise ProductError('语音限制和计费参数必须有效。', 503, 'speech_config')
        if not (1 <= c['timeout_seconds'] <= 30 and 1 <= c['max_recording_seconds'] <= 60 and 60 <= c['cache_seconds'] <= 86400 and
                0 < c['limit_cny'] <= 5 and c['asr_cny_per_second'] >= .00022 and c['tts_cny_per_10000_characters'] >= .8 and
                model.config['budget'].get('manual_reserve_cny', 0) >= 5):
            raise ProductError('语音或人工试用预算超出授权边界。', 503, 'speech_config')
        self.downloads = {}  # signed media locations remain in RAM only, never logs
        self.download_lock = threading.Lock()

    def call(self, purpose, payload, units, request_id):
        c = self.config; key = self.model.read_key()
        rate = c['asr_cny_per_second'] if purpose == 'asr' else c['tts_cny_per_10000_characters'] / 10000
        reserve = math.ceil(units) * rate
        call_id = identifier('call')
        with self.model.ledger.transaction() as ledger:
            self.model.check_budget(ledger, reserve, purpose)
            ledger['calls'][call_id] = {'requestId':request_id, 'purpose':purpose, 'model':c[purpose+'_model'], 'startedAt':stamp(),
                'status':'reserved', 'occupiedCny':round(reserve, 6), 'reservedCny':round(reserve, 6), 'attempt':1,
                'reservedUnits':math.ceil(units), 'timeoutSeconds':c['timeout_seconds']}
        started = time.monotonic(); usage = None; status = 'unknown'; error = ''
        req = Request(c[purpose+'_endpoint'], data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type':'application/json', 'Authorization':'Bearer '+key}, method='POST')
        try:
            with build_opener(ProxyHandler({}), NoRedirect, HTTPSHandler).open(req, timeout=c['timeout_seconds']) as response:
                raw = response.read(512001)
            if len(raw) > 512000:
                raise ProductError('语音服务返回过大，请改用文字。', 502, 'speech_format')
            result = json.loads(raw); usage = result.get('usage')
            if result.get('code') or result.get('status_code', 200) != 200:
                raise ProductError('指定语音服务未完成请求，请稍后重试或使用文字。', 502, 'speech_provider')
            status = 'completed'
            return result
        except HTTPError as exc:
            status, error = 'failed', 'http_'+str(exc.code)
            if exc.code in (401, 403, 404):
                raise ProductError('指定云语音模型的权限或服务不可用。请家长检查本机配置；文字交流仍可使用。', 503, 'speech_permission')
            if exc.code == 429:
                raise ProductError('云语音服务现在有点忙，可以稍后再试或打字。', 429, 'speech_busy')
            raise ProductError('云语音服务没有接通，可以稍后重试或打字。', 502, 'speech_provider')
        except (TimeoutError, socket.timeout, URLError):
            error = 'network_or_timeout'
            raise ProductError('云语音等待超时或网络中断。可以重试这一段语音，或继续使用文字。', 504, 'speech_timeout')
        except (ValueError, TypeError, IndexError):
            error = 'invalid_response'
            raise ProductError('语音服务的返回格式不完整，可以重试或改用文字。', 502, 'speech_format')
        finally:
            with self.model.ledger.transaction() as ledger:
                rec = ledger['calls'][call_id]
                rec.update(finishedAt=stamp(), durationMs=round((time.monotonic()-started)*1000), status=status, error=error)
                value = usage.get('seconds' if purpose == 'asr' else 'characters') if isinstance(usage, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    rec.update(usage=usage, occupiedCny=round(math.ceil(value)*rate, 6), costBasis='usage_at_list_price_no_free_quota')
                else:
                    rec['costBasis'] = 'unknown_usage_keep_full_reservation'

    def transcribe(self, raw, request_id):
        duration = wav_info(raw, recording=True)[4]
        payload = {'model':self.config['asr_model'], 'messages':[{'role':'user', 'content':[{'type':'input_audio',
            'input_audio':{'data':'data:audio/wav;base64,'+base64.b64encode(raw).decode()}}]}],
            'stream':False, 'asr_options':{'language':'zh', 'enable_itn':True}}
        result = self.call('asr', payload, math.ceil(duration)+1, request_id)
        try:
            text = result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProductError('识别结果不完整，请重录或打字。', 502, 'speech_format')
        if not text:
            raise ProductError('没有识别到清楚的话。可以靠近一些重录，或直接打字。', 422, 'asr_empty')
        if len(text) > 800:
            raise ProductError('这段话较长，请分成两次说；还没有发送问题。', 422, 'asr_length')
        return text

    @staticmethod
    def audio_url(raw):
        p = urlparse(str(raw or ''))
        if p.hostname != 'dashscope-result-bj.oss-cn-beijing.aliyuncs.com' or p.username or p.password or p.port not in (None, 443) or p.scheme not in ('https','http') or not p.path.endswith('.wav'):
            raise ProductError('语音文件地址不符合已批准的服务范围。文字回答仍然保留。', 502, 'speech_audio_url')
        return p._replace(scheme='https').geturl()

    def synthesize(self, text, request_id, cancelled=lambda:False):
        parts = []
        for index, chunk in enumerate(split_text(text)):
            if cancelled():
                raise ProductError('朗读已停止，文字回答仍然保留。', 409, 'speech_cancelled')
            digest = hashlib.sha256((self.config['tts_model']+self.config['voice']+chunk).encode()).hexdigest()
            with self.download_lock:
                cached = self.downloads.get(digest)
            if not cached or cached[1] <= time.time():
                payload = {'model':self.config['tts_model'], 'input':{'text':chunk, 'voice':self.config['voice'], 'language_type':self.config['language']}}
                # The service's character usage can exceed Python/UTF-16 text
                # length for Chinese. UTF-8 bytes plus overhead conservatively
                # reserve before calling; returned usage settles actual cost.
                result = self.call('tts', payload, len(chunk.encode('utf-8'))+16, request_id+':'+str(index))
                try:
                    audio = result['output']['audio']; url = self.audio_url(audio['url'])
                    cached = (url, min(float(audio['expires_at']), time.time()+self.config['cache_seconds']))
                except (KeyError, TypeError, ValueError):
                    raise ProductError('朗读文件没有完整返回。文字回答仍然保留，可以重试朗读。', 502, 'speech_format')
                with self.download_lock:
                    self.downloads[digest] = cached
            try:
                # Credentials are not forwarded to the signed audio endpoint.
                with build_opener(ProxyHandler({}), NoRedirect, HTTPSHandler).open(Request(cached[0]), timeout=self.config['timeout_seconds']) as response:
                    raw = response.read(16_000_001)
            except (HTTPError, URLError, TimeoutError, socket.timeout):
                raise ProductError('朗读文件暂时没有下载完成。可以重试朗读，文字不用重新发送。', 504, 'speech_download')
            if len(raw) > 16_000_000:
                raise ProductError('朗读文件过大，文字回答仍然保留。', 502, 'speech_audio_size')
            parts.append(wav_info(raw))
        if not parts or any(p[:3] != parts[0][:3] for p in parts):
            raise ProductError('朗读文件格式不一致，文字回答仍然保留。', 502, 'speech_format')
        out = io.BytesIO()
        with wave.open(out, 'wb') as f:
            f.setnchannels(parts[0][0]); f.setsampwidth(parts[0][1]); f.setframerate(parts[0][2])
            f.writeframes(b''.join(p[3] for p in parts))
        return out.getvalue()


class SpeechService:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.client = SpeechClient(service.model)
        self.config = self.client.config
        self.cache_dir = self.store.path.parent / (self.store.path.stem+'-speech-cache')
        self.slots = threading.BoundedSemaphore(2)
        with self.store.transaction() as db:
            jobs = db.setdefault('speechRequests', {})
            for job in jobs.values():
                if job.get('status') == 'pending':
                    job.update(status='failed', error='服务已重启，请重新识别或朗读；已有文字仍保留。', code='server_restarted')
            self.prune(db)

    def public_status(self):
        return {'enabled':True, 'maxRecordingSeconds':self.config['max_recording_seconds'],
                'asrModel':self.config['asr_model'], 'ttsModel':self.config['tts_model'], 'voice':self.config['voice']}

    def prune(self, db):
        jobs = db.setdefault('speechRequests', {})
        for jid, job in list(jobs.items()):
            if job.get('expiresAt', 0) < time.time() and job['status'] != 'pending':
                (self.cache_dir / (jid+'.wav')).unlink(missing_ok=True)
                del jobs[jid]

    def message(self, db, child_id, mid):
        self.service.profile(db, child_id)
        msg = db['messages'].get(mid, {})
        if msg.get('childId') != child_id or msg.get('role') != 'assistant' or msg.get('status') != 'completed' or msg.get('deleted') or db['conversations'].get(msg.get('conversationId'), {}).get('deleted'):
            raise ProductError('这段回答已不可用，请回到当前档案选择回答。', 404, 'speech_source')
        return msg

    @staticmethod
    def view(job):
        return {k:copy.deepcopy(job[k]) for k in ('id','childId','kind','status','text','messageId','error','code','expiresAt','inputKind') if k in job}

    def get(self, child_id, jid):
        with self.store.transaction() as db:
            self.service.profile(db, child_id); self.prune(db)
            job = db['speechRequests'].get(jid, {})
            if job.get('childId') != child_id:
                raise ProductError('这段语音已过期或不属于当前档案，可以重新录音或朗读。', 404, 'speech_expired')
            if job['kind'] == 'tts': self.message(db, child_id, job['messageId'])
            return self.view(job)

    def begin(self, kind, data):
        jid, child_id = str(data.get('requestId','')), str(data.get('childId',''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', jid):
            raise ProductError('语音请求标识无效，请刷新。')
        raw = None
        if kind == 'asr':
            try:
                encoded = data.get('audio','')
                if not isinstance(encoded,str) or len(encoded) > 2_600_000: raise ValueError()
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise ProductError('音频未完整上传，请重新录音或选择文件。', 400, 'audio_format')
            _, _, _, pcm, duration = wav_info(raw, recording=True)
            if duration > self.config['max_recording_seconds'] or len(raw) > 1_925_000:
                raise ProductError('一次最多说60秒，请分成两次提问。', 400, 'audio_length')
            if not any(pcm):
                raise ProductError('这段音频没有声音，请重录或打字。', 400, 'audio_empty')
            digest = hashlib.sha256(raw).hexdigest()
        with self.store.transaction() as db:
            self.service.profile(db, child_id); self.prune(db)
            if kind == 'tts':
                msg = self.message(db, child_id, str(data.get('messageId','')))
                digest = hashlib.sha256((msg['text']+self.config['tts_model']+self.config['voice']).encode()).hexdigest()
            old = db['speechRequests'].get(jid)
            if old:
                if old.get('childId') != child_id or old.get('digest') != digest or old['kind'] != kind:
                    raise ProductError('语音请求已属于其他内容。', 409, 'speech_conflict')
                return self.view(old)
            if kind == 'tts':
                cached = next((x for x in db['speechRequests'].values() if x['kind']=='tts' and x['childId']==child_id and x.get('messageId')==msg['id'] and x['digest']==digest and x['status'] in ('pending','completed') and (x['status']=='pending' or (self.cache_dir/(x['id']+'.wav')).is_file())), None)
                if cached: return self.view(cached)
            if self.service.closed.is_set() or not self.slots.acquire(blocking=False):
                raise ProductError('正在处理其他语音，请稍后再试；文字可以继续使用。', 409, 'speech_busy')
            job = {'id':jid, 'childId':child_id, 'kind':kind, 'status':'pending', 'digest':digest, 'createdAt':stamp(),
                   'expiresAt':time.time()+(1800 if kind=='asr' else self.config['cache_seconds'])}
            if kind == 'asr':
                conv = next((x['id'] for x in db['conversations'].values() if x['childId']==child_id and not x.get('deleted') and not x.get('endedAt')), '')
                if str(data.get('conversationId') or '') != conv:
                    self.slots.release()
                    raise ProductError('聊天已经切换，请重新录音或打字。', 409, 'speech_context')
                job.update(conversationId=conv, inputKind='audio_file' if data.get('inputKind')=='audio_file' else 'microphone')
            else:
                job.update(messageId=msg['id']); text = msg['text']
            db['speechRequests'][jid] = job
        def cancelled():
            return self.service.closed.is_set() or self.store.read().get('speechRequests',{}).get(jid,{}).get('status') != 'pending'
        def work():
            try:
                if cancelled(): return
                output = self.client.transcribe(raw, jid) if kind == 'asr' else self.client.synthesize(text, jid, cancelled)
                with self.store.transaction() as db:
                    current = db['speechRequests'].get(jid, {})
                    if current.get('status') != 'pending' or self.service.closed.is_set(): return
                    self.service.profile(db, child_id)
                    if kind == 'asr':
                        conv = next((x['id'] for x in db['conversations'].values() if x['childId']==child_id and not x.get('deleted') and not x.get('endedAt')), '')
                        if conv != job['conversationId']:
                            raise ProductError('聊天已切换，这次识别没有放入新对话。', 409, 'speech_context')
                        current['text'] = output
                    else:
                        self.message(db, child_id, job['messageId'])
                        self.cache_dir.mkdir(parents=True, exist_ok=True)
                        target = self.cache_dir/(jid+'.wav'); target.write_bytes(output); target.chmod(0o600)
                    current.update(status='completed', finishedAt=stamp())
            except Exception as exc:
                with self.store.transaction() as db:
                    current = db['speechRequests'].get(jid, {})
                    if current.get('status') == 'pending':
                        current.update(status='failed', error=str(exc) if isinstance(exc, ProductError) else '本机语音处理未完成，可以重试或使用文字。', code=exc.code if isinstance(exc, ProductError) else 'speech_local')
            finally:
                self.slots.release()
        threading.Thread(target=work, name='speech-'+kind, daemon=True).start()
        return self.view(job)

    def input_source(self, db, data, child_id, text):
        jid = data.get('transcriptId')
        if not jid: return {'type':'text'}
        job = db.get('speechRequests',{}).get(jid,{})
        if job.get('childId') != child_id or job.get('kind') != 'asr' or job.get('status') != 'completed' or job.get('expiresAt',0) < time.time():
            raise ProductError('识别来源已过期。文字仍保留，请按文字发送或重新录音。', 409, 'speech_expired')
        return {'type':'asr', 'model':self.config['asr_model'], 'inputKind':job['inputKind'], 'transcript':job['text'], 'edited':text != job['text'], 'review':'submitted_by_user'}

    def audio(self, child_id, jid):
        job = self.get(child_id, jid)
        if job['kind'] != 'tts' or job['status'] != 'completed':
            raise ProductError('朗读还没有准备好，可以稍后重试。', 409, 'speech_pending')
        try: return (self.cache_dir/(jid+'.wav')).read_bytes()
        except OSError:
            raise ProductError('朗读缓存已失效，请重新点击朗读。文字回答仍保留。', 404, 'speech_expired')

    def cancel(self, data):
        child_id, jid = data.get('childId'), data.get('requestId')
        with self.store.transaction() as db:
            self.service.profile(db, child_id)
            job = db.get('speechRequests',{}).get(jid, {})
            if job.get('childId') != child_id: raise ProductError('没有这段语音。', 404)
            if job.get('status') == 'pending': job.update(status='cancelled', error='语音已停止，文字内容保留。')
            return self.view(job)

    def close(self):
        with self.store.transaction() as db:
            for job in db.get('speechRequests',{}).values():
                if job.get('status') == 'pending': job.update(status='cancelled', error='服务已停止，请重新录音或朗读。')
