import numpy as np
import pytest
from alicia.owner_microphone import Gate, Segmenter, Playout, RATE, FRAME

class Identity:
    def __init__(self, scores):
        self.scores = iter(scores)
        self.calls = 0
    def verify_pcm(self, pcm, rate):
        self.calls += 1
        score = next(self.scores)
        if isinstance(score, Exception):
            raise score
        return {'score': score}

@pytest.mark.parametrize('score', [0.64, float('nan'), ValueError('bad profile')])
def test_unknown_or_failed_verification_is_silent(score):
    assert not Gate(Identity([score])).check(np.ones(RATE, dtype=np.int16))['accepted']

def test_every_window_including_tail_must_match():
    identity = Identity([.9, .9, .9, .2])
    assert not Gate(identity).check(np.ones(RATE * 2, dtype=np.int16))['accepted']
    assert identity.calls == 4

def test_owner_positive_reaches_verifier():
    identity = Identity([.9] * 4)
    assert Gate(identity).check(np.ones(RATE * 2, dtype=np.int16))['accepted']
    assert identity.calls == 4

@pytest.mark.parametrize('length', [0, RATE // 2, RATE * 9])
def test_invalid_lengths_never_call_verifier(length):
    identity = Identity([])
    assert not Gate(identity).check(np.ones(length, dtype=np.int16))['accepted']
    assert identity.calls == 0

def test_segmenter_preserves_start_and_separates_speakers():
    segmenter = Segmenter()
    loud = np.full(FRAME, 1000, dtype=np.int16)
    quiet = np.zeros(FRAME, dtype=np.int16)
    assert all(segmenter.feed(loud) is None for _ in range(50))
    results = [segmenter.feed(quiet) for _ in range(18)]
    phrase = results[-1]
    assert np.array_equal(phrase[:RATE], np.full(RATE, 1000))
    assert all(r is None for r in results[:-1])
    assert segmenter.frames == []

def test_long_phrase_discarded_until_pause():
    segmenter = Segmenter()
    for _ in range(500):
        assert segmenter.feed(np.full(FRAME, 1000, dtype=np.int16)) is None
    for _ in range(18):
        assert segmenter.feed(np.zeros(FRAME, dtype=np.int16)) is None
    assert not segmenter.discard

def test_playout_empty_and_cleared_are_silent():
    p = Playout()
    assert not p.next().any()
    assert p.enqueue(np.full(FRAME, 1000, dtype=np.int16))
    assert not p.enqueue(np.full(FRAME, 2000, dtype=np.int16))
    assert (p.next() == 1000).all()
    assert not p.next().any()
    p.enqueue(np.full(FRAME, 1000, dtype=np.int16));p.clear()
    assert not p.next().any()
