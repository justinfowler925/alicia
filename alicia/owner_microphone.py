"""Experimental local speaker-filtered microphone. Never an authentication boundary.

python -m alicia.owner_microphone --help
Audio stays in bounded memory. Each complete utterance and its overlapping windows
must match the enrolled speaker; rejection, overload, or an error outputs silence.
Overlapping speech and replay attacks are NOT reliably detected by this model.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import queue
import threading
import time

import numpy as np

from .voice_identity import VoiceIdentity, MODEL_ID

RATE = 16000
FRAME = 320


class Gate:
    def __init__(self, identity, threshold=0.65):
        if not 0.45 <= threshold <= 1:
            raise ValueError('threshold must be between 0.45 and 1')
        self.identity, self.threshold = identity, threshold

    def check(self, audio):
        """No PCM is released unless every check succeeds, including the final tail."""
        started = time.monotonic()
        if not RATE * .75 <= len(audio) <= RATE * 8:
            return {'accepted': False, 'reason': 'phrase_length', 'compute_ms': 0}
        windows = [audio]
        size, step = RATE, RATE // 2
        if len(audio) > size:
            starts = list(range(0, len(audio) - size + 1, step))
            starts.append(len(audio) - size)
            windows.extend(audio[i:i + size] for i in sorted(set(starts)))
        try:
            scores = [float(self.identity.verify_pcm(x.astype('<i2').tobytes(), RATE)['score']) for x in windows]
            accepted = all(np.isfinite(s) and s >= self.threshold for s in scores)
            return {'accepted': accepted, 'score': round(min(scores), 4), 'windows': len(scores),
                    'compute_ms': round((time.monotonic() - started) * 1000, 1)}
        except Exception as exc:
            return {'accepted': False, 'reason': type(exc).__name__,
                    'compute_ms': round((time.monotonic() - started) * 1000, 1)}


class Segmenter:
    def __init__(self, noise_floor=250):
        self.noise_floor = noise_floor
        self.pre = deque(maxlen=5)
        self.frames = []
        self.quiet = 0
        self.discard = False

    def feed(self, audio):
        loud = np.sqrt(np.mean(audio.astype(np.float32) ** 2)) >= self.noise_floor
        if self.discard:
            self.quiet = 0 if loud else self.quiet + 1
            if self.quiet >= 18:
                self.discard = False
                self.quiet = 0
            return None
        if not self.frames:
            self.pre.append(audio.copy())
            if loud:
                self.frames = list(self.pre)
                self.pre.clear()
            return None
        self.frames.append(audio.copy())
        self.quiet = 0 if loud else self.quiet + 1
        if len(self.frames) > 400:
            self.frames.clear()
            self.discard = True
            self.quiet = 0
            return None
        if self.quiet >= 18:
            # Preserve 100 ms of trailing silence; don't count the end pause as speech.
            result = np.concatenate(self.frames[:-13])
            self.frames.clear()
            self.quiet = 0
            return result
        return None


class Playout:
    def __init__(self):
        self.frames = deque()
        self.lock = threading.Lock()

    def clear(self):
        with self.lock:
            self.frames.clear()

    def enqueue(self, audio):
        with self.lock:
            if self.frames:
                return False  # No accumulation of delayed commands.
            for i in range(0, len(audio), FRAME):
                self.frames.append(np.pad(audio[i:i + FRAME], (0, max(0, FRAME - len(audio[i:i + FRAME])))))
            return True

    def next(self):
        with self.lock:
            return self.frames.popleft() if self.frames else np.zeros(FRAME, dtype=np.int16)


def device(sd, name, direction):
    found = [(i, d) for i, d in enumerate(sd.query_devices())
             if d['name'] == name and d['max_' + direction + '_channels'] > 0]
    if len(found) != 1:
        raise RuntimeError(f'Expected exactly one {direction} device named {name!r}; found {len(found)}')
    return found[0][0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='Blue Snowball')
    parser.add_argument('--output', default='BlackHole 2ch')
    parser.add_argument('--profile', type=Path, default=Path.home() / '.alicia/state/voice-owner.json')
    parser.add_argument('--model-cache', type=Path, default=Path.home() / '.alicia-owner-mic/model')
    parser.add_argument('--threshold', type=float, default=.65)
    parser.add_argument('--noise-floor', type=int, default=250)
    parser.add_argument('--devices', action='store_true')
    parser.add_argument('--observe', action='store_true', help='Measure matches but never forward microphone audio')
    parser.add_argument('--seconds', type=float, default=0, help='Stop after this duration; 0 runs until interrupted')
    args = parser.parse_args()
    import sounddevice as sd
    if args.devices:
        print(sd.query_devices())
        return
    if args.input == args.output or args.output != 'BlackHole 2ch':
        raise RuntimeError('Output must be BlackHole 2ch and must differ from input')
    import torch
    torch.set_num_threads(4)
    from speechbrain.inference.speaker import EncoderClassifier
    identity = VoiceIdentity(args.profile)
    if not identity.status()['enrolled']:
        raise RuntimeError('A valid enrolled owner profile is required')
    identity._classifier = EncoderClassifier.from_hparams(source=MODEL_ID, savedir=str(args.model_cache),
                                                          run_opts={'device': 'cpu'})
    gate = Gate(identity, args.threshold)
    # Warm the CPU model before opening a microphone.
    gate.check(np.zeros(RATE, dtype=np.int16))
    input_id, output_id = device(sd, args.input, 'input'), device(sd, args.output, 'output')
    incoming = queue.Queue(maxsize=100)
    playout = Playout()
    failed = threading.Event()

    def capture(data, frames, timing, status):
        if status or frames != FRAME:
            failed.set()
            return
        try:
            incoming.put_nowait(data[:, 0].copy())
        except queue.Full:
            failed.set()

    def render(out, frames, timing, status):
        out.fill(0)
        if status or frames != FRAME:
            failed.set()
        if not failed.is_set():
            pcm = playout.next()
            out[:, 0] = pcm
            out[:, 1] = pcm

    segmenter = Segmenter(args.noise_floor)
    print(json.dumps({'event': 'ready', 'input': args.input, 'output': args.output,
                      'observe': args.observe, 'threshold': args.threshold}), flush=True)
    with sd.OutputStream(device=output_id, samplerate=RATE, blocksize=FRAME, channels=2,
                         dtype='int16', callback=render), sd.InputStream(
            device=input_id, samplerate=RATE, blocksize=FRAME, channels=1, dtype='int16', callback=capture):
        start = time.monotonic()
        while not args.seconds or time.monotonic() - start < args.seconds:
            if failed.is_set():
                playout.clear()
                raise RuntimeError('Audio discontinuity or overload: output closed')
            try:
                block = incoming.get(timeout=.2)
            except queue.Empty:
                continue
            phrase = segmenter.feed(block)
            if phrase is not None:
                result = gate.check(phrase)
                result.update(event='decision', speech_seconds=round(len(phrase) / RATE, 2))
                result['forwarded'] = bool(result['accepted'] and not args.observe and playout.enqueue(phrase))
                print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
