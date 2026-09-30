#!/usr/bin/env python3
"""Synthetic spoken note through real WebRTC, delegation, and Alicia's tool loop."""
import asyncio,json,time,wave
from fractions import Fraction
from av import AudioFrame
from aiortc import AudioStreamTrack
from pathlib import Path
import httpx
from aiortc import RTCPeerConnection
from aiortc.contrib.media import MediaPlayer

async def main():
    pc=RTCPeerConnection();events=pc.createDataChannel('oai-events')
    received=[];transcripts=[];replies=[];delegations=set();audio_frames=0
    ready=asyncio.Event()
    async with httpx.AsyncClient(timeout=180) as client:
        session=(await client.post('http://127.0.0.1:8768/api/session/open',json={'title':'Studio synthetic voice qualification'})).json()['session_id']
        @pc.on('track')
        def track(track):
            async def consume():
                nonlocal audio_frames
                try:
                    while True:
                        frame=await track.recv()
                        # Count audible frames, not just an open media track.
                        if abs(frame.to_ndarray().astype('int32')).max()>40:audio_frames+=1
                except Exception:pass
            asyncio.create_task(consume())
        async def delegate(event):
            text=''.join(transcripts).strip();transcripts.clear()
            r=await client.post(f'http://127.0.0.1:8768/api/session/{session}/live-delegation',json={'id':event['delegation']['id'],'message':text})
            data=r.json();replies.append(data)
            events.send(json.dumps({'type':'session.commentary.append','delegation_id':event['delegation']['id'],'content':data.get('reply','Tool request failed')}))
        @events.on('message')
        def message(raw):
            event=json.loads(raw);kind=event.get('type');received.append(kind)
            if kind=='session.started':ready.set()
            if kind=='session.input_transcript.delta':transcripts.append(event.get('delta',''))
            if kind=='session.delegation.created' and event['delegation']['id'] not in delegations:
                delegations.add(event['delegation']['id']);asyncio.create_task(delegate(event))
            if kind=='session.output_transcript.delta':print('spoken:',event.get('delta',''),flush=True)
            if kind=='error':print('provider error:',json.dumps(event),flush=True)
        class ContinuousInput(AudioStreamTrack):
            def __init__(self):
                super().__init__()
                self.audio=wave.open(str(Path.home()/'.alicia/voice-proof.wav'))
                self.position=0;self.started=None
            async def recv(self):
                if self.started is None:self.started=time.monotonic()
                await asyncio.sleep(max(0,self.started+self.position/24000-time.monotonic()))
                data=self.audio.readframes(480)
                frame=AudioFrame(format='s16',layout='mono',samples=480)
                frame.planes[0].update(data.ljust(960,b'\x00'))
                frame.sample_rate=24000;frame.pts=self.position;frame.time_base=Fraction(1,24000)
                self.position+=480
                return frame
        pc.addTrack(ContinuousInput())
        await pc.setLocalDescription(await pc.createOffer())
        r=await client.post(f'http://127.0.0.1:8768/api/session/{session}/live',json={'sdp':pc.localDescription.sdp})
        if r.status_code!=200:raise RuntimeError(r.text)
        from aiortc import RTCSessionDescription
        await pc.setRemoteDescription(RTCSessionDescription(sdp=r.json()['transport']['sdp'],type='answer'))
        await asyncio.wait_for(ready.wait(),30)
        deadline=time.monotonic()+140
        while time.monotonic()<deadline and not (replies and audio_frames>5):await asyncio.sleep(1)
        await asyncio.sleep(5)
        events.send(json.dumps({'type':'session.close'}));await asyncio.sleep(1)
        notes=(await client.get('http://127.0.0.1:8768/api/todos')).json()
        note_verified='studio voice qualification passed' in json.dumps(notes).lower()
        receipt={'note_verified':note_verified,'session':session,'started':ready.is_set(),'audible_frames':audio_frames,'delegations':len(delegations),'replies':replies,'event_types':sorted(set(received))}
        (Path.home()/'.alicia/voice-proof.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
        await pc.close()
        assert ready.is_set() and audio_frames>5 and replies and note_verified and any('saved' in r.get('reply','').lower() for r in replies),'Voice proof incomplete'

asyncio.run(main())
