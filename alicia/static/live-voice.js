/* Native full-duplex voice; API credentials and work stay on Studio. */
export class LiveVoice {
  constructor(sessionId, onPhase, onTranscript) {
    this.sessionId = sessionId;
    this.onPhase = onPhase;
    this.onTranscript = onTranscript;
    this.input = [];
    this.logQueue = Promise.resolve();
    this.caption = null;
    this.seen = new Set();
    this.closed = false;
    this.audio = new Audio();
    this.audio.autoplay = true;
  }
  send(event) {
    if (this.events?.readyState === "open") this.events.send(JSON.stringify(event));
  }
  async start(signal) {
    signal.addEventListener("abort", () => this.stop(), {once:true});
    this.peer = new RTCPeerConnection();
    this.peer.ontrack = ({track}) => {
      this.audio.srcObject = new MediaStream([track]);
      this.audio.play().catch(() => this.onPhase("error", "Audio playback is blocked. Tap Talk to reconnect."));
    };
    this.peer.onconnectionstatechange = () => {
      if (["failed", "disconnected"].includes(this.peer.connectionState) && !this.closed) {
        this.stop(); this.onPhase("error", "Connection lost. Tap Talk to reconnect; your work stays on Studio.");
      }
    };
    this.onPhase("buffering", "Allow microphone access if your browser asks.");
    let micTimeout;
    const permission = navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}}).then(stream => {
      if(this.closed || signal.aborted) stream.getTracks().forEach(track=>track.stop());
      return stream;
    });
    try {
      this.microphone = await Promise.race([permission,new Promise((_,reject)=>{
        micTimeout=setTimeout(()=>{this.stop();reject(new Error("Microphone permission is still pending. Allow it in your browser, then retry."));},25000);
      })]);
    } finally {clearTimeout(micTimeout);}
    if (signal.aborted || this.closed) { this.stop(); return; }
    for (const track of this.microphone.getAudioTracks()) this.peer.addTrack(track,this.microphone);
    this.events = this.peer.createDataChannel("oai-events");
    this.events.onmessage = ({data}) => { try { this.receive(JSON.parse(data)); } catch { this.onPhase("error", "Voice sent an unreadable event."); } };
    this.events.onclose = () => { if (!this.closed) {this.stop(); this.onPhase("error", "Voice disconnected. Tap Talk to reconnect.");} };
    await this.peer.setLocalDescription(await this.peer.createOffer());
    if (this.peer.iceGatheringState !== "complete") await new Promise((resolve,reject) => {
      const timeout=setTimeout(()=>{this.peer.removeEventListener("icegatheringstatechange",check);reject(new Error("Voice connection timed out"));},10000);
      const check=()=>{if(this.peer.iceGatheringState==="complete"){clearTimeout(timeout);this.peer.removeEventListener("icegatheringstatechange",check);resolve();}};
      this.peer.addEventListener("icegatheringstatechange",check);check();
    });
    const response=await fetch(`/api/session/${this.sessionId}/live`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sdp:this.peer.localDescription.sdp}),signal});
    const result=await response.json();
    if(!response.ok) throw new Error(result.detail||"Voice could not connect");
    if(signal.aborted || this.closed) return;
    await this.peer.setRemoteDescription({type:"answer",sdp:result.transport.sdp});
    this.readyTimeout=setTimeout(()=>{if(!this.ready){this.stop();this.onPhase("error","Voice did not start. Tap Talk to retry.");}},15000);
  }
  receive(event) {
    if(this.closed) return;
    if(event.type==="session.started") {this.ready=true;clearTimeout(this.readyTimeout);this.onPhase("listening","Listening — work runs on Studio.");}
    if(event.type==="session.input_transcript.delta") {
      this.input.push({text:event.delta||"",end:event.end_ms||0});
      this.record("user",event.delta||"");
    }
    if(event.type==="session.output_transcript.delta") this.record("assistant",event.delta||"");
    if(event.type==="session.delegation.created" && event.delegation?.target==="client") {
      if(!this.seen.has(event.delegation.id)) {this.seen.add(event.delegation.id);void this.delegate(event);}
    }
    if(event.type==="session.closed") {this.stop();this.onPhase("idle");}
    if(event.type==="error") {this.stop();this.onPhase("error","The voice service reported an error. Your recorded work remains on Studio.");}
  }
  async delegate(event) {
    const selected=this.input.splice(0);
    this.flushCaption();
    await this.logQueue;
    const id=event.delegation.id;
    const message=selected.map(x=>x.text).join("").trim();
    if(!message) {this.send({type:"session.commentary.append",delegation_id:id,content:"I did not receive a complete request. Please repeat what you want me to do."});return;}
    try {
      const response=await fetch(`/api/session/${this.sessionId}/live-delegation`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,message})});
      const result=await response.json();
      this.send({type:"session.commentary.append",delegation_id:id,content:response.ok ? result.reply : "That request is recorded but I cannot confirm completion. Check the conversation before repeating it."});
    } catch {this.send({type:"session.commentary.append",delegation_id:id,content:"The work connection dropped. Studio may still be working; check the conversation before repeating the action."});}
  }
  record(role,text) {
    this.onPhase(role === "assistant" ? "speaking" : "listening");
    if (this.caption?.role !== role) this.flushCaption();
    if (!this.caption) this.caption={id:crypto.randomUUID(),role,text:""};
    this.caption.text+=text;
    this.onTranscript?.(role,this.caption.text);
    clearTimeout(this.captionTimer);
    this.captionTimer=setTimeout(()=>this.flushCaption(),1500);
  }
  flushCaption() {
    clearTimeout(this.captionTimer);
    const part=this.caption;this.caption=null;
    if(!part?.text.trim()) return;
    this.logQueue=this.logQueue.then(async()=>{
      for(let attempt=0;attempt<2;attempt++) {
        try {const r=await fetch(`/api/session/${this.sessionId}/live-transcript`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(part),keepalive:true});if(r.ok)return;}catch{}
      }
      this.onPhase("error","Conversation could not be saved. Check your Studio connection.");
    });
  }
  mute(value) {this.audio.muted=value;}
  stop() {
    this.flushCaption();
    this.closed=true;clearTimeout(this.readyTimeout);
    this.microphone?.getTracks().forEach(t=>t.stop());
    this.audio.pause();this.audio.srcObject=null;
    this.send({type:"session.close"});
    const peer=this.peer,events=this.events;
    setTimeout(()=>{events?.close();peer?.close();},500);
  }
}
