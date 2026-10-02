/* Explicit, user-triggered audio for the existing chat. No automatic microphone. */
class CuriosityVoice {
 constructor({api, context, changed, transcript, notice}) {
  Object.assign(this,{api,context,changed,transcript,notice});
  this.phase='idle';this.epoch=0;this.messageId='';this.error='';this.job=null;this.raw=null;this.audio=null;this.events=[];
 }
 event(name){this.events.push({name,at:performance.now()});if(this.events.length>30)this.events.shift();}
 inputBusy(){return ['permission','recording','converting','recognizing'].includes(this.phase);}
 update(phase,error=''){this.phase=phase;this.error=error;this.changed();}
 valid(epoch,ctx){return this.epoch===epoch&&this.context().childId===ctx.childId;}
 validJob(epoch,ctx,id){return this.valid(epoch,ctx)&&this.job?.id===id;}
 taskKey(){return 'curiosity-voice-task-'+this.context().childId;}
 stop(cancelPending=true){
  ++this.epoch;clearInterval(this.ticker);clearTimeout(this.deadline);
  if(this.recorder&&this.recorder.state!=='inactive')this.recorder.stop();
  this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;this.recorder=null;
  if(this.audio){this.audio.pause();this.audio.removeAttribute('src');this.audio.load();this.audio=null;}
  if(cancelPending&&this.job?.status==='pending'){
   fetch('/api/speech/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({childId:this.job.childId,requestId:this.job.id}),keepalive:true}).catch(()=>{});
  }
  if(cancelPending&&this.job?.kind==='asr')localStorage.removeItem('curiosity-voice-task-'+this.job.childId);
  this.job=null;this.raw=null;this.messageId='';this.unknown=false;this.update('idle');
 }
 async record(){
  if(this.phase==='recording'){if(this.recorder?.state==='recording'){this.update('converting');this.recorder.stop();}return;}
  if(this.inputBusy())return;
  this.stop(); const epoch=this.epoch,ctx=this.context();this.update('permission');
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){this.update('asr_error','这个浏览器暂不支持录音，可以选择音频文件或直接打字。');return;}
  try{
   const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
   if(!this.valid(epoch,ctx)){stream.getTracks().forEach(t=>t.stop());return;}
   this.stream=stream;const chunks=[];
   const mime=['audio/webm;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
   const recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);this.recorder=recorder;
   recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
   recorder.onerror=()=>{if(this.valid(epoch,ctx)){this.stop();this.update('asr_error','录音中断了，请重新录音或打字。');}};
   recorder.onstop=async()=>{
    stream.getTracks().forEach(t=>t.stop());
    if(!this.valid(epoch,ctx)||this.recorder!==recorder)return;
    clearInterval(this.ticker);clearTimeout(this.deadline);
    this.stream=null;this.recorder=null;
    await this.convertAndRecognize(new Blob(chunks,{type:recorder.mimeType}),epoch,ctx,'microphone');
   };
   recorder.start();this.started=performance.now();this.event('recording_started');this.update('recording');
   this.ticker=setInterval(()=>{if(this.valid(epoch,ctx)&&this.recorder===recorder)this.changed();},500);
   this.deadline=setTimeout(()=>{if(this.valid(epoch,ctx)&&this.recorder===recorder&&recorder.state==='recording')recorder.stop();},ctx.maxSeconds*1000);
  }catch(err){
   if(!this.valid(epoch,ctx))return;
   this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;
   const errors={NotAllowedError:'麦克风权限没有开启。可以在浏览器中允许后重录，或直接打字。',NotFoundError:'没有找到麦克风。可以接入设备、选择音频文件或打字。',NotReadableError:'麦克风暂时被占用。关闭其他录音后重试，或直接打字。'};
   this.update('asr_error',errors[err.name]||'录音没有开始。可以重录、选择音频文件或打字。');
  }
 }
 async file(file){
  if(!file||this.inputBusy())return;
  this.stop();const epoch=this.epoch,ctx=this.context();
  if(file.size>12*1024*1024){this.update('asr_error','音频文件太大，请选择60秒以内的音频。');return;}
  await this.convertAndRecognize(file,epoch,ctx,'audio_file');
 }
 static encodeWav(samples,rate=16000){
  const result=new ArrayBuffer(44+samples.length*2),v=new DataView(result);
  const label=(offset,text)=>{for(let i=0;i<text.length;i++)v.setUint8(offset+i,text.charCodeAt(i));};
  label(0,'RIFF');v.setUint32(4,36+samples.length*2,true);label(8,'WAVE');label(12,'fmt ');v.setUint32(16,16,true);
  v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);label(36,'data');v.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++){const s=Math.max(-1,Math.min(1,samples[i]));v.setInt16(44+i*2,s<0?s*32768:s*32767,true);}
  return new Uint8Array(result);
 }
 async convertAndRecognize(blob,epoch,ctx,inputKind){
  if(!this.valid(epoch,ctx))return;
  this.update('converting');let decoder;
  try{
   if(!blob.size)throw new Error('没有录到声音，请重录或打字。');
   const AudioCtx=window.AudioContext||window.webkitAudioContext;
   if(!AudioCtx||!window.OfflineAudioContext)throw new Error('这个浏览器暂时不能处理音频，可以换一个浏览器或直接打字。');
   decoder=new AudioCtx();const decoded=await decoder.decodeAudioData(await blob.arrayBuffer());
   if(decoded.duration>ctx.maxSeconds+0.1)throw new Error('一次最多60秒，请选择更短的音频。');
   if(decoded.duration<0.15)throw new Error('录音太短，请说完问题再结束。');
   const output=new OfflineAudioContext(1,Math.ceil(decoded.duration*16000),16000);
   const source=output.createBufferSource();source.buffer=decoded;source.connect(output.destination);source.start();
   const pcm=(await output.startRendering()).getChannelData(0);
   if(!this.valid(epoch,ctx))return;
   this.raw=CuriosityVoice.encodeWav(pcm);this.inputKind=inputKind;
   await this.recognize(epoch,ctx);
  }catch(err){if(this.valid(epoch,ctx))this.update('asr_error',err.name==='EncodingError'||err.name==='NotSupportedError'?'无法读取这段音频，请选择可播放的文件、重录或打字。':err.message||'无法读取这段音频，请重录或打字。');}
  finally{await decoder?.close().catch(()=>{});}
 }
 async recognize(epoch=this.epoch,ctx=this.context()){
  if(!this.valid(epoch,ctx)||!this.raw||this.phase==='recognizing')return;
  this.update('recognizing');const id='speech_'+crypto.randomUUID().replaceAll('-','');
  this.job={id,childId:ctx.childId,kind:'asr',status:'pending'};localStorage.setItem(this.taskKey(),JSON.stringify(this.job));
  let binary='';for(let i=0;i<this.raw.length;i+=8192)binary+=String.fromCharCode(...this.raw.subarray(i,i+8192));
  try{
   const job=await this.api('/api/speech/transcribe',{requestId:id,childId:ctx.childId,conversationId:ctx.conversationId,audio:btoa(binary),inputKind:this.inputKind});
   if(!this.validJob(epoch,ctx,id))return;
   await this.poll(job,epoch,ctx);
  }catch(err){if(this.validJob(epoch,ctx,id)){this.unknown=!err.status;if(!this.unknown){localStorage.removeItem(this.taskKey());this.job=null;if(['audio_empty','audio_length','audio_format'].includes(err.code))this.raw=null;}this.update('asr_error',(err.name==='AbortError'?'识别请求的完成状态暂时不明确。':err.message)+(this.unknown?' 还没有发送问题，可以先查看识别状态。':''));}}
 }
 async poll(job,epoch,ctx){
  if(!this.valid(epoch,ctx)||(this.job&&this.job.id!==job.id))return;
  const id=job.id;
  this.job=job;
  while(job.status==='pending'&&this.validJob(epoch,ctx,id)){
   await new Promise(resolve=>setTimeout(resolve,450));
   if(!this.validJob(epoch,ctx,id))return;
   const result=await this.api('/api/speech/request?'+new URLSearchParams({childId:ctx.childId,requestId:id}));
   if(!this.validJob(epoch,ctx,id))return;
   this.job=job=result;
  }
  if(!this.validJob(epoch,ctx,id))return;
  this.unknown=false;
  if(job.kind==='asr'){
   localStorage.removeItem(this.taskKey());
   if(job.status==='completed'){
    this.event('transcript_ready');this.raw=null;this.job=null;this.update('idle');this.transcript(job.text,job.id,job.inputKind);
   }else this.update('asr_error',job.error||'识别没有完成，请重录或打字。');
  }else if(job.status==='completed'){
   this.audio=new Audio('/api/speech/audio?'+new URLSearchParams({childId:ctx.childId,requestId:job.id}));
   this.audio.onplaying=()=>{if(this.valid(epoch,ctx)){this.event('audio_playing');this.update('playing');}};
   this.audio.onended=()=>{if(this.valid(epoch,ctx))this.update('play_ready');};
   this.audio.onerror=()=>{if(this.valid(epoch,ctx))this.update('tts_error','音频没有播放成功。可以重试朗读，屏幕回答仍保留。');};
   await this.playAudio(epoch,ctx);
  }else this.update('tts_error',job.error||'朗读没有完成，文字回答仍保留。');
 }
 async recover(){
  if(this.phase==='recognizing'||this.phase==='tts_loading')return;
  const job=this.job||JSON.parse(localStorage.getItem(this.taskKey())||'null');if(!job)return;
  const ctx=this.context();if(job.childId&&job.childId!==ctx.childId)return;
  const epoch=++this.epoch;this.job={...job,childId:ctx.childId};this.update(job.kind==='asr'?'recognizing':'tts_loading');
  try{const result=await this.api('/api/speech/request?'+new URLSearchParams({childId:ctx.childId,requestId:job.id}));if(this.validJob(epoch,ctx,job.id))await this.poll(result,epoch,ctx);}
  catch(err){if(this.validJob(epoch,ctx,job.id)){this.unknown=!err.status;if(!this.unknown){if(job.kind==='asr')localStorage.removeItem(this.taskKey());this.job=null;}this.update(job.kind==='asr'?'asr_error':'tts_error',err.name==='AbortError'?'暂时没有查到完成状态，可以稍后再查看。':err.message);}}
 }
 async playAudio(epoch,ctx){
  if(!this.valid(epoch,ctx)||!this.audio)return;
  const audio=this.audio;
  this.update('play_ready');
  try{await audio.play();}
  catch(err){if(this.valid(epoch,ctx)&&this.audio===audio)this.update('play_ready',err.name==='NotAllowedError'?'浏览器需要你再点一次播放。':'播放没有开始，可以再次点击或继续阅读。');}
 }
 async speak(messageId){
  if(this.inputBusy())return;
  if(this.messageId===messageId&&this.phase==='playing'){this.audio?.pause();this.update('play_ready');return;}
  if(this.messageId===messageId&&this.phase==='play_ready'&&this.audio){this.audio.currentTime=0;await this.playAudio(this.epoch,this.context());return;}
  if(this.messageId===messageId&&this.phase==='tts_loading')return;
  this.stop();const epoch=this.epoch,ctx=this.context();this.messageId=messageId;this.update('tts_loading');
  const id='speech_'+crypto.randomUUID().replaceAll('-','');this.job={id,childId:ctx.childId,kind:'tts',status:'pending'};
  try{const job=await this.api('/api/speech/synthesize',{childId:ctx.childId,requestId:id,messageId});if(this.validJob(epoch,ctx,id))await this.poll(job,epoch,ctx);}
  catch(err){if(this.validJob(epoch,ctx,id)){this.unknown=!err.status;if(!this.unknown)this.job=null;this.update('tts_error',(err.name==='AbortError'?'朗读请求的完成状态暂时不明确。':err.message)+' 文字回答没有丢失。');}}
 }
}
if(typeof module!=='undefined')module.exports=CuriosityVoice;
