from dataclasses import dataclass, field
import logging
from pathlib import Path
import os
import re
import shlex
import socket

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    studio_api_url: str
    token: str = field(repr=False)
    cli_dir: Path
    worker_id: str = "worker"
    gflow_cmd: str = "uv run gflow"
    profiles: tuple[str, ...] = ()
    max_concurrent: int = 1
    mem_reserve_mb: int = 1536
    chrome_peak_mb: int = 1200
    work_dir: Path = field(default_factory=lambda: Path("~/.gflow-worker").expanduser())
    poll_seconds: float = 60
    clip_qc_poll_seconds: float = 20
    credits_refresh_minutes: float = 1440
    credits_refresh_jitter_minutes: float = 240
    clip_timeout_seconds: float = 1200
    seed_offset_ms: int = 100
    keep_done_days: float = 3
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    probe_python_cmd: str = "uv run python"
    probe_prefix: str = ""
    probe_projects: dict[str, str] = field(default_factory=dict)
    probe_timeout_seconds: float = 300
    lock_file: Path | None = None
    lock_timeout_seconds: float = 1800
    proxy: str | None = None
    cover_image_model: str = "nano2-lite"
    cover_gap_seconds: tuple = (5, 15)

    @classmethod
    def from_env(cls):
        required = ("STUDIO_API_URL", "GFLOW_WORKER_TOKEN", "GFLOW_CLI_DIR")
        for name in required:
            if not os.environ.get(name, "").strip():
                raise ValueError(f"{name} is required")
        hostname = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())[:64] or "worker"
        probe_projects = {}
        for item in os.getenv("GFLOW_PROBE_PROJECTS", "").split(","):
            if item.strip():
                profile, separator, project = item.partition("=")
                if not separator or not profile.strip() or not project.strip():
                    raise ValueError("GFLOW_PROBE_PROJECTS must contain profile=project_id entries")
                probe_projects[profile.strip()] = project.strip()
        values = dict(studio_api_url=os.environ[required[0]].rstrip("/"),
                      token=os.environ[required[1]], cli_dir=Path(os.environ[required[2]]).expanduser().resolve(),
                      worker_id=os.getenv("GFLOW_WORKER_ID", hostname),
                      gflow_cmd=os.getenv("GFLOW_CMD", "uv run gflow"),
                      profiles=tuple(p.strip() for p in os.getenv("GFLOW_PROFILES", "").split(",") if p.strip()),
                      probe_python_cmd=os.getenv("GFLOW_PYTHON_CMD", "uv run python"),
                      probe_prefix=os.getenv("GFLOW_PROBE_PREFIX", ""),
                      probe_projects=probe_projects,
                      probe_timeout_seconds=float(os.getenv("GFLOW_PROBE_TIMEOUT_SECONDS", "300")),
                      lock_file=(Path(os.environ["GFLOW_LOCK_FILE"]).expanduser() if os.getenv("GFLOW_LOCK_FILE", "").strip() else None),
                      lock_timeout_seconds=float(os.getenv("GFLOW_LOCK_TIMEOUT_SECONDS", "1800")),
                      proxy=(os.getenv("GFLOW_PROXY") or os.getenv("GFLOW_SOCKS5_PROXY") or "").strip() or None,
                      work_dir=Path(os.getenv("GFLOW_WORK_DIR", "~/.gflow-worker")).expanduser().resolve(),
                      ffmpeg=os.getenv("FFMPEG", "ffmpeg"), ffprobe=os.getenv("FFPROBE", "ffprobe"))
        for name in ("max_concurrent", "mem_reserve_mb", "chrome_peak_mb", "seed_offset_ms"):
            values[name] = int(os.getenv("GFLOW_" + name.upper(), str(cls.__dataclass_fields__[name].default)))
        if values["max_concurrent"] > 2:
            log.warning("GFLOW_MAX_CONCURRENT=%d clamped to 2", values["max_concurrent"])
            values["max_concurrent"] = 2
        for name in ("poll_seconds", "clip_qc_poll_seconds", "credits_refresh_minutes", "credits_refresh_jitter_minutes", "clip_timeout_seconds", "keep_done_days"):
            values[name] = float(os.getenv("GFLOW_" + name.upper(), str(cls.__dataclass_fields__[name].default)))
        values["cover_image_model"] = os.getenv("GFLOW_COVER_IMAGE_MODEL", str(cls.__dataclass_fields__["cover_image_model"].default))
        gap_raw = os.getenv("GFLOW_COVER_GAP_SECONDS", "").strip()
        if gap_raw:
            parts = [float(p.strip()) for p in gap_raw.split(",") if p.strip()]
            if len(parts) != 2 or parts[0] < 0 or parts[0] > parts[1]:
                raise ValueError("invalid cover gap limits")
            values["cover_gap_seconds"] = (parts[0], parts[1])
        else:
            cover_min = float(os.getenv("GFLOW_COVER_GAP_MIN_SECONDS", "5"))
            cover_max = float(os.getenv("GFLOW_COVER_GAP_MAX_SECONDS", "15"))
            if cover_min < 0 or cover_max < cover_min:
                raise ValueError("invalid cover gap limits")
            values["cover_gap_seconds"] = (cover_min, cover_max)
        cfg = cls(**values)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cfg.worker_id):
            raise ValueError("invalid GFLOW_WORKER_ID")
        if not cfg.cli_dir.is_dir():
            raise ValueError("GFLOW_CLI_DIR must be a directory")
        if cfg.max_concurrent < 1 or any(getattr(cfg, n) < 0 for n in ("mem_reserve_mb", "chrome_peak_mb", "seed_offset_ms", "keep_done_days", "credits_refresh_jitter_minutes")):
            raise ValueError("invalid worker limits")
        if min(cfg.poll_seconds, cfg.clip_qc_poll_seconds, cfg.credits_refresh_minutes, cfg.clip_timeout_seconds, cfg.probe_timeout_seconds,
               cfg.lock_timeout_seconds) <= 0:
            raise ValueError("worker intervals must be positive")
        if cfg.credits_refresh_jitter_minutes >= cfg.credits_refresh_minutes:
            raise ValueError("credits_refresh_jitter_minutes must be less than credits_refresh_minutes")
        if len(cfg.cover_gap_seconds) != 2 or cfg.cover_gap_seconds[0] < 0 or cfg.cover_gap_seconds[0] > cfg.cover_gap_seconds[1]:
            raise ValueError("invalid cover gap limits")
        if not shlex.split(cfg.probe_python_cmd) or shlex.split(cfg.probe_prefix) is None:
            raise ValueError("invalid probe command")
        return cfg
