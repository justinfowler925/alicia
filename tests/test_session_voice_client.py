"""Contract checks for the browser voice state machine.

These deliberately inspect the shipped client entry point: a helper-only unit test can
stay green when the page never wires the helper.
"""

import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).parents[1] / "brutus/static/session.js").read_text()


def test_direct_session_link_opens_the_named_proposal_session():
    assert 'new URLSearchParams(window.location.search).get("session")' in SOURCE
    assert 'sessionStorage.setItem("brutus.session", requested)' in SOURCE
    assert "return hydrate(requested)" in SOURCE


def test_an_approved_proposal_lands_its_returned_state_without_waiting_for_sse():
    start = SOURCE.index("async function settleProposal")
    end = SOURCE.index("\n}\n\n/* --- saying things", start)
    body = SOURCE[start:end]
    assert "const result = await response.json()" in body
    assert "if (artifact) renderProposal(artifact)" in body


def test_voice_instructions_live_in_help_and_supervisor_names_providers():
    html = (Path(__file__).parents[1] / "brutus/static/session.html").read_text()
    assert 'popovertarget="voice-help"' in html
    assert 'id="voice-help"' in html
    assert "Talk naturally. Pause when you are done." not in SOURCE
    _client_contract("providers")


def test_owner_voice_enrollment_is_a_visible_record_and_consent_flow():
    html = (Path(__file__).parents[1] / "brutus/static/session.html").read_text()
    assert 'id="voice-enroll"' in html
    assert 'id="voice-enrollment"' in html
    assert 'id="enrollment-consent"' in html
    assert 'fetch("/api/voice-enrollment", { method: "POST", body })' in SOURCE
    assert "encodeWav" in SOURCE


def test_shipped_session_client_is_valid_javascript():
    source_path = Path(__file__).parents[1] / "brutus/static/session.js"
    result = subprocess.run(["node", "--check", str(source_path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_workspace_disclosure_releases_the_fixed_conversation_layout():
    assert '$("#work-tray")?.addEventListener("toggle"' in SOURCE
    assert 'classList.toggle("workspace-open", open)' in SOURCE
    css = (Path(__file__).parents[1] / "brutus/static/session.css").read_text()
    assert "body.workspace-open" in css
    assert ".voice-shell.workspace-open" in css


def test_supervisor_hides_generic_lifecycle_lectures():
    _client_contract("lifecycle")


def test_livekit_is_the_preferred_transport_and_attaches_agent_audio():
    token = SOURCE.index("/voice-token")
    fallback = SOURCE.index("startLegacyVoice", token)
    assert token < fallback
    assert "https://esm.sh/livekit-client@2.15.13" in SOURCE


def test_livekit_claims_spoken_output_before_connecting():
    start = SOURCE.index("async function startVoice()")
    end = SOURCE.index("\nfunction startLegacyVoice", start)
    body = SOURCE[start:end]
    assert body.index("state.voiceTransport = \"livekit\"") < body.index("await room.connect")
    _client_contract("playback")


def test_livekit_capture_suppresses_echo_and_background_noise_before_stt():
    assert "audioCaptureDefaults" in SOURCE
    assert "echoCancellation: true" in SOURCE
    assert "noiseSuppression: true" in SOURCE
    assert "autoGainControl: false" in SOURCE
    assert "setMicrophoneEnabled(true)" in SOURCE
    assert "RoomEvent.TrackSubscribed" in SOURCE
    assert "track.attach()" in SOURCE
    assert "RoomEvent.ActiveSpeakersChanged" in SOURCE


def test_disabled_or_failed_livekit_falls_back_to_browser_speech():
    assert "if (!grant.enabled || !grant.url || !grant.token)" in SOURCE
    assert "return startLegacyVoice();" in SOURCE
    assert 'startLegacyVoice("Live voice unavailable' in SOURCE
    assert "const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition" in SOURCE


def test_owner_enrollment_never_falls_back_to_unverified_browser_voice():
    start = SOURCE.index("async function startVoice()")
    end = SOURCE.index("\nfunction startLegacyVoice", start)
    body = SOURCE[start:end]
    assert "grant.owner_enrollment_required" in body
    assert "browser fallback is disabled for safety" in body


def test_route_teardown_aborts_every_request_and_disconnects_room():
    start = SOURCE.index("function teardownVoice()")
    end = SOURCE.index("\n}", start) + 2
    body = SOURCE[start:end]
    assert body.count(".abort()") >= 3
    assert "teardownLiveKit()" in body
    assert 'window.addEventListener("pagehide", teardownVoice)' in SOURCE
    assert "sessionStorage.removeItem" in SOURCE
    assert "teardownVoice();" in SOURCE[SOURCE.index("sessionStorage.removeItem") :]
    assert "room.disconnect()" in SOURCE


def test_all_six_user_visible_voice_states_exist():
    for phase in ("idle", "listening", "thinking", "buffering", "speaking", "error"):
        assert f'"{phase}"' in SOURCE
    assert 'btn.dataset.voiceState = phase' in SOURCE
    assert '"Preparing reply…"' in SOURCE
    assert '"Cancel reply and listen"' in SOURCE


def test_barge_in_cancels_work_without_inventing_a_user_request():
    assert 'say("what needs me", "voice")' not in SOURCE
    start = SOURCE.index("function bargeIn()")
    end = SOURCE.index("\n}", start) + 2
    body = SOURCE[start:end]
    assert "state.sayAbort?.abort()" in body
    assert "stopSpeaking()" in body
    assert 'setVoicePhase("listening"' in body


def _client_contract(case, *, mutant=False):
    harness = Path(__file__).with_name("session_client_contracts.cjs")
    result = subprocess.run(
        ["node", str(harness), case, *(["mutant"] if mutant else [])],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if mutant:
        assert result.returncode != 0, "seeded regression escaped the behavioral check"
        assert "AssertionError" in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert f"PASS {case}" in result.stdout


@pytest.mark.parametrize("case", ["providers", "lifecycle", "playback"])
def test_client_contract_rejects_seeded_regression(case):
    _client_contract(case, mutant=True)
