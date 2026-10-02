// Controlled browser-device branches. No real microphone, network or speaker.
const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const Voice=require('../static/voice.js');
const instances=[];
global.window={};
global.crypto=require('node:crypto').webcrypto;
global.localStorage={data:new Map(),getItem(k){return this.data.get(k)||null;},setItem(k,v){this.data.set(k,v);},removeItem(k){this.data.delete(k);}};
global.fetch=async()=>({ok:true});
function make(api=async()=>{}){let ctx={childId:'synthetic',conversationId:'conv',maxSeconds:60};const texts=[];const v=new Voice({api,context:()=>ctx,changed(){},transcript(...args){texts.push(args);},notice(){}});instances.push(v);return {v,texts,change:()=>ctx={...ctx,childId:'another'}};}
function media(getUserMedia){Object.defineProperty(global,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia}}});window.MediaRecorder=global.MediaRecorder=class {static isTypeSupported(){return true;}constructor(stream){this.state='inactive';this.mimeType='audio/webm';}start(){this.state='recording';}stop(){if(this.state==='inactive')throw Error('double stop');this.state='inactive';this.onstop?.();}};}
afterEach(()=>{instances.splice(0).forEach(v=>v.stop());localStorage.data.clear();});
test('microphone only opens after record; denied permission is recoverable',async()=>{let opened=0;media(async()=>{opened++;throw Object.assign(Error(),{name:'NotAllowedError'});});const {v}=make();assert.equal(opened,0);await v.record();assert.equal(opened,1);assert.equal(v.phase,'asr_error');assert.match(v.error,/权限/);});
test('cancelling permission or changing profile releases a late stream',async()=>{let resolve,stopped=0;media(()=>new Promise(r=>resolve=r));const {v,change}=make();const pending=v.record();change();v.stop();resolve({getTracks:()=>[{stop(){stopped++;}}]});await pending;assert.equal(stopped,1);assert.equal(v.phase,'idle');});
test('repeated end-click uploads once and releases recorder tracks',async()=>{let stopped=0,converted=0;media(async()=>({getTracks:()=>[{stop(){stopped++;}}]}));const {v}=make();v.convertAndRecognize=async()=>{converted++;v.update('recognizing');};await v.record();await v.record();await v.record();assert.equal(converted,1);assert.equal(stopped,1);assert.equal(v.phase,'recognizing');});
test('stopping recording never launches ASR from late onstop',async()=>{let converted=0,stopped=0;media(async()=>({getTracks:()=>[{stop(){stopped++;}}]}));const {v}=make();v.convertAndRecognize=async()=>{converted++;};await v.record();v.stop();assert.equal(converted,0);assert.ok(stopped>=1);assert.equal(v.stream,null);assert.equal(v.recorder,null);});
test('blocked playback remains ready; replay reuses audio without API',async()=>{let plays=0,calls=0;const {v}=make(async()=>{calls++;});v.messageId='saved';v.audio={play:async()=>{plays++;throw Object.assign(Error(),{name:'NotAllowedError'});},pause(){},removeAttribute(){},load(){}};await v.playAudio(v.epoch,v.context());assert.equal(v.phase,'play_ready');assert.match(v.error,/再点一次/);await v.speak('saved');assert.equal(calls,0);assert.equal(plays,2);});
test('playing starts only with actual playing event; navigation releases audio',async()=>{let lastAudio;global.Audio=class {constructor(){lastAudio=this;this.paused=false;}async play(){}pause(){this.paused=true;}removeAttribute(){this.cleared=true;}load(){}};const {v}=make();v.messageId='saved';await v.poll({id:'cached',kind:'tts',status:'completed'},v.epoch,v.context());assert.equal(v.phase,'play_ready');lastAudio.onplaying();assert.equal(v.phase,'playing');v.stop();assert.ok(lastAudio.paused&&lastAudio.cleared);lastAudio.onplaying();assert.equal(v.phase,'idle');});
test('ASR recovery delivers editable text once and never generates chat',async()=>{let calls=[];const {v,texts}=make(async path=>{calls.push(path);return {id:'saved',kind:'asr',status:'completed',text:'合成问题',inputKind:'audio_file'};});localStorage.setItem(v.taskKey(),JSON.stringify({id:'saved',kind:'asr'}));await v.recover();assert.equal(texts.length,1);assert.equal(localStorage.getItem(v.taskKey()),null);assert.ok(calls.every(p=>p.startsWith('/api/speech/request?')));});
test('PCM encoding writes actual mono 16 kHz WAV sizes',()=>{const bytes=Voice.encodeWav(new Float32Array([0,1,-1]));const view=new DataView(bytes.buffer);assert.equal(view.getUint32(24,true),16000);assert.equal(view.getUint16(22,true),1);assert.equal(view.getUint32(40,true),6);assert.equal(view.getInt16(46,true),32767);assert.equal(view.getInt16(48,true),-32768);});
test('known invalid audio is not presented as an unknown paid request',async()=>{const {v}=make(async()=>{throw Object.assign(Error('这段音频没有声音'),{status:400,code:'audio_empty'});});v.raw=new Uint8Array([1,2]);await v.recognize();assert.equal(v.phase,'asr_error');assert.equal(v.unknown,false);assert.equal(v.raw,null);assert.equal(v.job,null);assert.equal(localStorage.getItem(v.taskKey()),null);});
test('network uncertainty recovers the original ASR request before retrying',async()=>{const {v}=make(async()=>{throw Error('连接中断');});v.raw=new Uint8Array([1,2]);await v.recognize();assert.equal(v.phase,'asr_error');assert.equal(v.unknown,true);assert.equal(v.job.status,'pending');assert.ok(localStorage.getItem(v.taskKey()));});

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function until(check){for(let i=0;i<100&&!check();i++)await new Promise(resolve=>setTimeout(resolve,10));assert.ok(check(),'controlled request reached its wait point');}
for(const kind of ['asr','tts'])for(const switchProfile of [false,true]){
 test(`${kind}: late old poll cannot replace or cancel the current task (${switchProfile?'new profile':'same profile'})`,async()=>{
  const requests=[],waits=new Map(),cancelled=[];const originalFetch=global.fetch;
  global.fetch=async(path,options)=>{cancelled.push(JSON.parse(options.body));return {ok:true};};
  const {v,texts,change}=make(async(path,data)=>{
   if(data){const job={id:data.requestId,childId:data.childId,kind,status:'pending'};requests.push(job);return job;}
   const id=new URL(path,'http://synthetic.local').searchParams.get('requestId');const wait=deferred();waits.set(id,wait);return wait.promise;
  });
  const start=()=>{if(kind==='asr'){v.raw=new Uint8Array([1,2]);return v.recognize();}return v.speak('answer_'+requests.length);};
  let first,second;
  try{
   first=start();await until(()=>requests[0]&&waits.has(requests[0].id));const old=requests[0];
   v.stop();if(switchProfile)change();second=start();await until(()=>requests[1]&&waits.has(requests[1].id));const current=requests[1];
   waits.get(old.id).resolve(old);await first;
   assert.equal(v.job.id,current.id);assert.equal(v.job.childId,current.childId);assert.equal(v.phase,kind==='asr'?'recognizing':'tts_loading');assert.equal(texts.length,0);
   v.stop();assert.equal(cancelled.at(-1).requestId,current.id);assert.equal(cancelled.at(-1).childId,current.childId);
   if(kind==='asr')assert.equal(localStorage.getItem('curiosity-voice-task-'+current.childId),null);
   waits.get(current.id).resolve({...current,status:'completed',text:'late text'});await second;assert.equal(v.phase,'idle');assert.equal(texts.length,0);
  }finally{v.stop();for(const [id,wait]of waits)wait.resolve({...requests.find(job=>job.id===id),status:'cancelled'});await Promise.allSettled([first,second]);global.fetch=originalFetch;}
 });
}
test('late recovery response cannot overwrite a newly started task',async()=>{
 const old=deferred(),current=deferred();let newId;
 const {v}=make(async(path,data)=>{if(data){newId=data.requestId;return current.promise;}return old.promise;});
 localStorage.setItem(v.taskKey(),JSON.stringify({id:'old',childId:'synthetic',kind:'asr',status:'pending'}));
 const recovering=v.recover();v.stop();v.raw=new Uint8Array([1]);const recognizing=v.recognize();
 try{old.resolve({id:'old',childId:'synthetic',kind:'asr',status:'completed',text:'old'});await recovering;assert.equal(v.job.id,newId);assert.equal(v.phase,'recognizing');}
 finally{v.stop();current.resolve({id:newId,kind:'asr',status:'cancelled'});await recognizing;}
});
test('late failed poll leaves the new task state and recovery item intact',async()=>{
 const old=deferred(),current=deferred();let oldId,newId,requested=false;
 const {v}=make(async(path,data)=>{if(data){if(!oldId){oldId=data.requestId;return {id:oldId,childId:data.childId,kind:'asr',status:'pending'};}newId=data.requestId;return current.promise;}requested=true;return old.promise;});
 v.raw=new Uint8Array([1]);const first=v.recognize();await until(()=>requested);v.stop();v.raw=new Uint8Array([2]);const second=v.recognize();
 try{old.reject(Object.assign(Error('old failure'),{status:404}));await first;assert.equal(v.job.id,newId);assert.equal(v.phase,'recognizing');assert.equal(JSON.parse(localStorage.getItem(v.taskKey())).id,newId);}
 finally{v.stop();current.resolve({id:newId,kind:'asr',status:'cancelled'});await second;}
});
for(const kind of ['asr','tts'])test(`${kind}: a normal pending task completes once with its own result`,async()=>{
 let job,lastAudio;const calls=[];
 global.Audio=class {constructor(src){lastAudio=this;this.src=src;}async play(){}pause(){}removeAttribute(){}load(){}};
 const {v,texts}=make(async(path,data)=>{calls.push(path);if(data){job={id:data.requestId,childId:data.childId,kind,status:'pending'};return job;}return {...job,status:'completed',text:'正常合成问题',inputKind:'audio_file'};});
 if(kind==='asr'){
  v.raw=new Uint8Array([1]);await v.recognize();assert.deepEqual(texts,[['正常合成问题',job.id,'audio_file']]);assert.equal(v.phase,'idle');assert.equal(v.job,null);assert.equal(localStorage.getItem(v.taskKey()),null);
 }else{
  await v.speak('saved_answer');assert.equal(v.phase,'play_ready');assert.equal(v.messageId,'saved_answer');assert.equal(new URL(lastAudio.src,'http://synthetic.local').searchParams.get('requestId'),job.id);lastAudio.onplaying();assert.equal(v.phase,'playing');lastAudio.onended();assert.equal(v.phase,'play_ready');
 }
 assert.equal(calls.length,2);
});
test('late recorder stop does not clear a new recording timer',async()=>{
 const callbacks=[],cleared=[];const clearI=global.clearInterval,clearT=global.clearTimeout;
 media(async()=>({getTracks:()=>[{stop(){}}]}));MediaRecorder.prototype.stop=function(){this.state='inactive';callbacks.push(this.onstop);};
 const {v}=make();await v.record();v.stop();await v.record();const ticker=v.ticker,deadline=v.deadline;
 global.clearInterval=id=>{cleared.push(id);clearI(id);};global.clearTimeout=id=>{cleared.push(id);clearT(id);};
 try{await callbacks[0]();assert.equal(v.phase,'recording');assert.ok(!cleared.includes(ticker));assert.ok(!cleared.includes(deadline));}
 finally{global.clearInterval=clearI;global.clearTimeout=clearT;v.stop();}
});
