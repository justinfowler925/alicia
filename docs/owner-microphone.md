# Experimental owner microphone for macOS Voice Control

Blue Snowball → local CPU speaker verification → BlackHole 2ch → Voice Control.
The model reads Alicia's existing owner profile; it does not enroll, upload, or
retain microphone recordings. Raw audio is held in bounded memory and discarded.
Cursor is not involved in audio filtering.

Install BlackHole 2ch from Homebrew (`brew install --cask blackhole-2ch`). Create a
separate Python 3.12 environment and install
`scripts/owner-microphone-requirements.txt`. From this checkout run:

```
python -m alicia.owner_microphone --devices
python -m alicia.owner_microphone --observe --seconds 60
python -m alicia.owner_microphone
```

Grant microphone access to the launching application when macOS requests it.
The default physical input is Blue Snowball. The exact device must exist, and
output must be BlackHole 2ch. The system output device is never changed.
After qualification, choose System Settings → Accessibility → Voice Control →
Microphone → BlackHole 2ch. Do not select Automatic: it may use a physical mic
and bypass the filter. To undo, stop the process and restore the previous input.
Stopping the process while BlackHole stays selected leaves Voice Control silent.

Each phrase waits for 360 ms of quiet, then its complete audio and all overlapping
one-second windows must meet a conservative 0.65 similarity threshold. There is
no open-mic period after a match. Short phrases under 750 ms and phrases over
eight seconds are discarded. Audio discontinuities, overload, missing enrollment,
model errors and ambiguous device names fail closed. A busy output drops a new
phrase instead of queueing delayed commands. A quiet speech signal can be missed
by the RMS segmenter; a noisy room can prevent segmentation.

Latency from the beginning of a phrase is its duration + 360 ms + model checks
+ audio I/O. The approved phrase is replayed at normal speed, so completion of a
command can be delayed by roughly its duration again. This is a conservative
whole-phrase prototype, not a low-latency streaming product.

Qualification must include actual owner speech through the chosen microphone,
other speakers, a speaker change within a phrase, overlapping speakers, TV,
recordings and process failure. Unit tests establish fail-closed mechanics, not
speaker accuracy. ECAPA verification does not reliably detect overlapping speech
or replayed/AI-cloned owner audio. Do not use this as security authentication or
claim it can guarantee that only the owner issues commands. Keep observe mode
until measured owner acceptance and non-owner rejection are acceptable.

No auto-start is installed: enabling an unqualified filter at login could silently
disable the user's accessibility input. Physical input qualification and an
independent Voice Control command demonstration are required before that step.
