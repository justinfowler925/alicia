"""Alicia laptop configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import home_path


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def _identity_set(value: str) -> frozenset[str]:
    """Parse a comma-separated Canon principal allowlist from the environment."""
    return frozenset(item.strip() for item in value.split(",") if item.strip())


# Canon's authenticated-principal registry is deliberately configured here,
# rather than accepting owner/verifier names from a transition caller. A future
# session/OAuth integration will issue these principals after authenticating a
# real session; the Canon boundary already knows which identities are trusted.
OWNER_IDENTITY = os.environ.get(
    "ALICIA_OWNER_IDENTITY", "justin.fowler@clearspeed.com"
).strip() or "justin.fowler@clearspeed.com"
CANON_WORKER_IDENTITIES = _identity_set(
    os.environ.get("ALICIA_CANON_WORKER_IDENTITIES", "atlas6-worker")
)
CANON_AUTOMATED_VERIFIER_IDENTITIES = _identity_set(
    os.environ.get(
        "ALICIA_CANON_AUTOMATED_VERIFIER_IDENTITIES", "github-evidence-verifier"
    )
)


@dataclass
class LocalLLMCfg:
    enabled: bool = False
    router_url: str = "http://127.0.0.1:7901"
    model: str = "mlx-community/Qwen3-8B-4bit"
    timeout_s: float = 120.0
    # Liveness. GET /v1/models is served without touching the model, so it keeps
    # answering 200 long after generation is dead — on 2026-08-11 the mlx server
    # lost its generate thread to a Metal OOM and stayed "healthy" for 14 hours
    # while every chat request hung. The only honest probe generates a token.
    probe_timeout_s: float = 30.0
    # Two strikes, because a cold model (post-sleep, weights paged out) can miss
    # one deadline legitimately. Restarting on a single slow probe is a loop.
    probe_failures_before_restart: int = 2
    autorestart_enabled: bool = True
    autorestart_label: str = "com.clearspeed.alicia-local-llm"
    autorestart_cooldown_s: float = 600.0


@dataclass
class CursorRunnerCfg:
    enabled: bool = False
    timeout_s: float = 900.0
    model: str = "composer-2.5"
    max_per_tick: int = 1
    reasoning_root: str = "~/.alicia/reasoning"
    allowlist_roots: list[str] = field(
        default_factory=lambda: [
            "~/Projects/alicia",
            "~/atlas-direct",
            "~/fowler-brain",
            "~/local-ai-stack",
            "~/.alicia/reasoning",
        ]
    )


@dataclass
class CursorCloudCfg:
    """Opt-in Cursor Cloud Agents. Default off — never a Forge/Scout fallback."""

    enabled: bool = False
    timeout_s: float = 900.0
    model: str = "composer-2.5"
    default_repo_url: str = "https://github.com/justinfowler925/alicia"
    default_starting_ref: str = "main"
    # Hard ceilings even when opted in. 0 = unlimited for that window.
    max_per_hour: int = 3
    max_per_day: int = 10


@dataclass
class VoiceCfg:
    enabled: bool = False
    # Browser and LiveKit voice do not require macOS Accessibility access.
    # The system-wide push-to-talk listener does, so it must be opted into
    # separately instead of being activated by the broad voice switch.
    ear_enabled: bool = False
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    record_duration_s: float = 5.0
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    # William-style Conversational AI agent. When set, /session prefers ConvAI
    # over LiveKit. Soft-load ALICIA_ELEVENLABS_AGENT_ID or ELEVENLABS_AGENT_ID.
    elevenlabs_agent_id: str = ""
    # Shared secret for optional ElevenLabs Custom LLM webhook (public URL).
    custom_llm_secret: str = ""
    ear_hotkey: str = "alt_r"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""


@dataclass
class ClaudeCfg:
    enabled: bool = False
    model: str = "claude-sonnet-5"
    timeout_s: float = 120.0
    # Sonnet 5 adaptive thinking is on by default; max_tokens covers
    # thinking + text. 2048 was enough when Sonnet 4 did not think.
    max_tokens: int = 8192
    # Reasoning effort for the conversational brain (low|medium|high). Chat
    # turns are dispatch and short answers; low keeps them on a spoken clock.
    effort: str = "low"
    # cli = Claude subscription CLI (default). api = Anthropic Messages (off unless
    # explicitly enabled AND brain.api.off is absent). Never soft-fail into API.
    transport: str = "cli"
    api_enabled: bool = False
    # Prefer ANTHROPIC_API_KEY env only when transport=api. Config field is fallback.
    api_key: str = ""
    # Hosted platform executor under Alicia (manager assign / ask_claude tool).
    # Default off — Cursor remains Alicia's reasoning brain; Claude is opt-in work.
    platform_lane: bool = False


@dataclass
class OpenAICfg:
    """Hosted OpenAI / Codex platform lane — Justin opt-in, default off."""

    enabled: bool = False
    model: str = "gpt-4.1-mini"
    timeout_s: float = 120.0
    base_url: str = "https://api.openai.com/v1"
    # Hard ceilings even when opted in. 0 = unlimited for that window.
    max_per_hour: int = 10
    max_per_day: int = 40


@dataclass
class EarnAutonomyCfg:
    enabled: bool = True
    auto_run: bool = False
    threshold: int = 3


@dataclass
class AliciaCfg:
    # Temporary standalone posture: Alicia does not probe, display, or route
    # through either Atlas daemon while this is false.
    atlas_enabled: bool = False
    atlas6_url: str = "http://127.0.0.1:8767"
    atlas5_url: str = "http://127.0.0.1:8766"
    timeout_s: float = 60.0
    serve_host: str = "127.0.0.1"
    serve_port: int = 8768
    watchdog_enabled: bool = True
    watchdog_interval_s: float = 60.0
    stale_inflight_minutes: int = 120
    linear_workspace: str = "clearspeed"
    max_working_set: int = 5
    # Hard cap on decision cards shown at once. The surface is only useful if it
    # stays answerable in about a minute; overflow is summarised, never dropped.
    max_actions: int = 7
    local_llm: LocalLLMCfg | None = None
    cursor_runner: CursorRunnerCfg | None = None
    cursor_cloud: CursorCloudCfg | None = None
    voice: VoiceCfg | None = None
    claude: ClaudeCfg | None = None
    openai: OpenAICfg | None = None
    earned_autonomy: EarnAutonomyCfg | None = None

    def __post_init__(self) -> None:
        if self.local_llm is None:
            self.local_llm = LocalLLMCfg()
        if self.cursor_runner is None:
            self.cursor_runner = CursorRunnerCfg()
        if self.cursor_cloud is None:
            self.cursor_cloud = CursorCloudCfg()
        if self.voice is None:
            self.voice = VoiceCfg()
        if self.claude is None:
            self.claude = ClaudeCfg()
        if self.openai is None:
            self.openai = OpenAICfg()
        if self.earned_autonomy is None:
            self.earned_autonomy = EarnAutonomyCfg()


def _parse_local_llm(data: dict) -> LocalLLMCfg:
    block = data.get("local_llm") or {}
    if not isinstance(block, dict):
        block = {}
    return LocalLLMCfg(
        enabled=bool(block.get("enabled", False)),
        router_url=str(block.get("router_url") or "http://127.0.0.1:7901"),
        model=str(block.get("model") or "mlx-community/Qwen3-8B-4bit"),
        timeout_s=float(block.get("timeout_s") or 120),
        probe_timeout_s=float(block.get("probe_timeout_s") or 30),
        probe_failures_before_restart=int(block.get("probe_failures_before_restart") or 2),
        autorestart_enabled=bool(block.get("autorestart_enabled", True)),
        autorestart_label=str(
            block.get("autorestart_label") or "com.clearspeed.alicia-local-llm"
        ),
        autorestart_cooldown_s=float(block.get("autorestart_cooldown_s") or 600),
    )


def _parse_cursor_runner(data: dict) -> CursorRunnerCfg:
    block = data.get("cursor_runner") or {}
    if not isinstance(block, dict):
        block = {}
    roots = block.get("allowlist_roots")
    if not isinstance(roots, list) or not roots:
        roots = [
            "~/Projects/alicia",
            "~/atlas-direct",
            "~/fowler-brain",
            "~/local-ai-stack",
            "~/.alicia/reasoning",
        ]
    return CursorRunnerCfg(
        enabled=bool(block.get("enabled", False)),
        timeout_s=float(block.get("timeout_s") or 900),
        model=str(block.get("model") or "composer-2.5"),
        max_per_tick=int(block.get("max_per_tick") or 1),
        reasoning_root=str(block.get("reasoning_root") or home_path("~/.alicia/reasoning")),
        allowlist_roots=[str(r) for r in roots],
    )


def _parse_cursor_cloud(data: dict) -> CursorCloudCfg:
    block = data.get("cursor_cloud") or {}
    if not isinstance(block, dict):
        block = {}
    return CursorCloudCfg(
        enabled=bool(block.get("enabled", False)),
        timeout_s=float(block.get("timeout_s") or 900),
        model=str(block.get("model") or "composer-2.5"),
        default_repo_url=str(
            block.get("default_repo_url") or "https://github.com/justinfowler925/alicia"
        ),
        default_starting_ref=str(block.get("default_starting_ref") or "main"),
        max_per_hour=int(block.get("max_per_hour", 3)),
        max_per_day=int(block.get("max_per_day", 10)),
    )


def _parse_voice(data: dict) -> VoiceCfg:
    block = data.get("voice") or {}
    if not isinstance(block, dict):
        block = {}
    # API keys are stored in 1Password. Fall back to env var if config is empty.
    elevenlabs_key = str(block.get("elevenlabs_api_key") or "")
    if not elevenlabs_key:
        elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    agent_id = str(block.get("elevenlabs_agent_id") or "").strip()
    if not agent_id:
        agent_id = (
            os.environ.get("ALICIA_ELEVENLABS_AGENT_ID")
            or os.environ.get("ELEVENLABS_AGENT_ID")
            or ""
        ).strip()
    return VoiceCfg(
        enabled=bool(block.get("enabled", False)),
        ear_enabled=bool(block.get("ear_enabled", False)),
        whisper_model=str(block.get("whisper_model") or "base"),
        whisper_device=str(block.get("whisper_device") or "cpu"),
        whisper_compute_type=str(block.get("whisper_compute_type") or "int8"),
        record_duration_s=float(block.get("record_duration_s") or 5.0),
        elevenlabs_api_key=elevenlabs_key,
        elevenlabs_voice_id=str(block.get("elevenlabs_voice_id") or ""),
        elevenlabs_agent_id=agent_id,
        ear_hotkey=str(block.get("ear_hotkey") or "alt_r"),
        livekit_url=str(block.get("livekit_url") or os.environ.get("LIVEKIT_URL", "")),
        livekit_api_key=str(block.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY", "")),
        livekit_api_secret=str(
            block.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET", "")
        ),
    )


def _parse_claude(data: dict) -> ClaudeCfg:
    block = data.get("claude") or {}
    if not isinstance(block, dict):
        block = {}
    transport = str(block.get("transport") or "cli").strip().lower()
    if transport not in {"cli", "api"}:
        transport = "cli"
    api_enabled = bool(block.get("api_enabled", False))
    key = str(block.get("api_key") or "")
    if not key and api_enabled and transport == "api":
        key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_PCM3_API_KEY") or ""
    return ClaudeCfg(
        enabled=bool(block.get("enabled", False)),
        model=str(block.get("model") or "claude-sonnet-5"),
        timeout_s=float(block.get("timeout_s") or 120),
        max_tokens=int(block.get("max_tokens") or 8192),
        effort=str(block.get("effort") or "low"),
        transport=transport,
        api_enabled=api_enabled,
        api_key=key,
        platform_lane=bool(block.get("platform_lane", False)),
    )


def _parse_openai(data: dict) -> OpenAICfg:
    block = data.get("openai") or {}
    if not isinstance(block, dict):
        block = {}
    return OpenAICfg(
        enabled=bool(block.get("enabled", False)),
        model=str(block.get("model") or "gpt-4.1-mini"),
        timeout_s=float(block.get("timeout_s") or 120),
        base_url=str(block.get("base_url") or "https://api.openai.com/v1"),
        max_per_hour=int(block.get("max_per_hour", 10)),
        max_per_day=int(block.get("max_per_day", 40)),
    )


def _parse_earned_autonomy(data: dict) -> EarnAutonomyCfg:
    block: dict = {}
    specialist = data.get("specialist_routing")
    if isinstance(specialist, dict) and isinstance(specialist.get("earned_autonomy"), dict):
        block = specialist["earned_autonomy"]
    elif isinstance(data.get("earned_autonomy"), dict):
        block = data["earned_autonomy"]
    return EarnAutonomyCfg(
        enabled=bool(block.get("enabled", True)),
        auto_run=bool(block.get("auto_run", False)),
        threshold=int(block.get("threshold") or 3),
    )


def load_config(path: Path | None = None) -> AliciaCfg:
    # The service runs from a dedicated detached worktree. A hard-coded shared
    # checkout path made the deployed actor silently load somebody else's
    # branch and stale backend allowlist. Default beside the imported package,
    # which is the artifact this process is actually executing.
    runtime_root = Path(__file__).resolve().parents[1]
    configured = os.environ.get("ALICIA_CONFIG", "").strip()
    cfg_path = path or (_expand(configured) if configured else runtime_root / "config.yaml")
    if not cfg_path.exists():
        example = runtime_root / "config.example.yaml"
        if example.exists():
            cfg_path = example
        else:
            return AliciaCfg()
    data = yaml.safe_load(cfg_path.read_text()) or {}
    if not isinstance(data, dict):
        data = {}
    return AliciaCfg(
        atlas_enabled=bool(data.get("atlas_enabled", False)),
        atlas6_url=str(data.get("atlas6_url") or "http://127.0.0.1:8767"),
        atlas5_url=str(data.get("atlas5_url") or "http://127.0.0.1:8766"),
        timeout_s=float(data.get("timeout_s") or 60),
        serve_host=str(data.get("serve_host") or "127.0.0.1"),
        serve_port=int(data.get("serve_port") or 8768),
        watchdog_enabled=bool(data.get("watchdog_enabled", True)),
        watchdog_interval_s=float(data.get("watchdog_interval_s") or 60),
        stale_inflight_minutes=int(data.get("stale_inflight_minutes") or 120),
        linear_workspace=str(data.get("linear_workspace") or "clearspeed"),
        max_working_set=int(data.get("max_working_set") or 5),
        max_actions=int(data.get("max_actions") or 7),
        local_llm=_parse_local_llm(data),
        cursor_runner=_parse_cursor_runner(data),
        cursor_cloud=_parse_cursor_cloud(data),
        voice=_parse_voice(data),
        claude=_parse_claude(data),
        openai=_parse_openai(data),
        earned_autonomy=_parse_earned_autonomy(data),
    )
