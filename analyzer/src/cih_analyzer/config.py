"""Settings from environment variables (spec R12): everything that changes between installs is set in the container
template, never in code. Secrets (the Plex token, the API key) are kept out of `repr()` so they cannot reach a log.

| Variable | Meaning |
|---|---|
| `CONFIG_DIR` | appdata: the database lives here (default `/config`) |
| `MEDIA_ROOTS` | container paths to scan, separated by `;` (read-only mounts) |
| `PATH_MAP` | equivalent roots, one rule per `;`, aliases separated by `\\|`; the first alias of a rule is the container path |
| `PLEX_URL`, `PLEX_TOKEN` | Plex server; the token only comes from here |
| `PLEX_SKIP_SECTIONS` | library titles to ignore, separated by `;` |
| `API_KEY` | if set, `/api/v1/*` needs it as `X-Api-Key` (or `key=`) |
| `PORT` | HTTP port (default 8765) |
| `CONCURRENCY` | files analysed at start-up (default 1); a count saved from the status page takes its place until cleared there |
| `START_PAUSED` | `1`: start with analysis paused, until Resume on the status page (default off). The queue still fills, and files can be put first meanwhile |
| `QUIET_HOURS` | `HH:MM-HH:MM`, local time: analysis pauses inside this window |
| `PAUSE_ON_TRANSCODE` | pause while Plex is transcoding (default on) |
| `SCAN_INTERVAL` | seconds between scans of the media roots (default 3600) |
| `PLEX_INTERVAL` | seconds between Plex syncs (default 900) |
| `RECENT_DAYS` | titles added to Plex this recently are analysed first (default 14) |
| `SAMPLE_INTERVAL` | seconds between the keyframes the first pass looks at (default 1; larger is faster, and can miss a short change) |
| `SURVEY_INTERVAL` | seconds between the places a first, cheap survey looks at (default 8; 0 turns it off). A file that shows one picture throughout is finished by the survey; any other gets the detailed pass over every keyframe. A change shorter than this that falls between two places, in a file that never changes otherwise, is missed |
| `ON_DEMAND_ROOTS` | container paths (among `MEDIA_ROOTS`, separated by `;`, for example `/media/tv`) that are catalogued but analysed only when a player asks for a title in them, instead of being queued in the background. Use it for big libraries such as TV |
| `EXPRESS_WORKERS` | extra workers (default 1; 0 = none) that take only titles a player asked for, so one does not wait behind the background queue. They run even while analysis is paused by hand, but obey quiet hours and the Plex-transcode pause |
| `KNOWN_VARIABLE_FILE` | a text file (one `Title (Year)` per line) of extra titles that change ratio; they skip the survey and get the detailed pass, like the titles bundled with the analyzer |
| `SAMPLE_INTERVALS` | per-folder overrides, `prefix=seconds;prefix=seconds` (for example `/media/tv=8`), longest prefix wins |
| `LOG_LEVEL` | default `INFO` |
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

_FALSE = {"0", "false", "no", "off", ""}
MIN_SAMPLE_INTERVAL = 1.0  # the first pass never samples more often than the coarse pass's own minimum
MIN_SURVEY_INTERVAL = 2.0


class ConfigError(ValueError):
    """A setting is present but not usable."""


def _split(value: str | None, sep: str = ";") -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(sep) if part.strip())


def parse_quiet_hours(value: str | None) -> tuple[time, time] | None:
    if not value or not value.strip():
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        raise ConfigError(f"QUIET_HOURS must look like 18:00-23:30, got {value!r}")
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise ConfigError(f"QUIET_HOURS has an impossible time: {value!r}")
    return time(h1, m1), time(h2, m2)


def in_quiet_hours(window: tuple[time, time] | None, now: time) -> bool:
    """True if `now` is inside the window; a window may cross midnight (22:00-06:00)."""
    if window is None:
        return False
    start, end = window
    if start == end:
        return False
    return start <= now < end if start < end else now >= start or now < end


def _number(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum:g}, got {raw!r}")
    return value


def _survey_interval(env: Mapping[str, str]) -> float:
    """Seconds between the places the survey looks at; 0 turns the survey off (every file gets the detailed pass)."""
    value = _number(env, "SURVEY_INTERVAL", 8.0, 0)
    if 0 < value < MIN_SURVEY_INTERVAL:
        raise ConfigError(
            f"SURVEY_INTERVAL must be 0 (off) or at least {MIN_SURVEY_INTERVAL:g}, got {value:g}"
        )
    return value


@dataclass(frozen=True)
class Settings:
    config_dir: Path = Path("/config")
    media_roots: tuple[str, ...] = ()
    path_map: tuple[tuple[str, ...], ...] = ()
    plex_url: str = ""
    plex_token: str = field(default="", repr=False)
    plex_skip_sections: tuple[str, ...] = ()
    api_key: str = field(default="", repr=False)
    port: int = 8765
    concurrency: int = 1
    quiet_hours: tuple[time, time] | None = None
    pause_on_transcode: bool = True
    start_paused: bool = False
    scan_interval_s: float = 3600.0
    plex_interval_s: float = 900.0
    recent_days: float = 14.0
    log_level: str = "INFO"
    analysis_timeout_s: float = 6 * 3600.0
    sample_interval_s: float = 1.0
    sample_intervals: tuple[tuple[str, float], ...] = ()
    survey_interval_s: float = 8.0
    on_demand_roots: tuple[str, ...] = ()
    express_workers: int = 1

    def interval_for(self, path: str) -> float:
        """Seconds between sampled keyframes for a file: the longest folder override that contains it, else the
        global setting."""
        best, best_len = self.sample_interval_s, -1
        for prefix, seconds in self.sample_intervals:
            root = prefix.rstrip("/")
            if (path == root or path.startswith(root + "/")) and len(root) > best_len:
                best, best_len = seconds, len(root)
        return best

    @property
    def db_path(self) -> Path:
        return self.config_dir / "cih-analyzer.sqlite"

    @property
    def plex_enabled(self) -> bool:
        return bool(self.plex_url and self.plex_token)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        rules = tuple(
            tuple(alias.strip() for alias in rule.split("|") if alias.strip())
            for rule in _split(env.get("PATH_MAP"))
        )
        for rule in rules:
            if len(rule) < 2:
                raise ConfigError(f"each PATH_MAP rule needs at least two paths separated by |, got {rule!r}")
        intervals = []
        for item in _split(env.get("SAMPLE_INTERVALS")):
            prefix, _, seconds = item.rpartition("=")
            try:
                value = float(seconds)
            except ValueError:
                value = 0.0
            if not prefix.strip() or value < MIN_SAMPLE_INTERVAL:
                raise ConfigError(
                    f"SAMPLE_INTERVALS entries look like /media/tv=8 (at least 1 s), got {item!r}"
                )
            intervals.append((prefix.strip(), value))
        port = int(_number(env, "PORT", 8765, 1))
        if port > 65535:
            raise ConfigError(f"PORT must be at most 65535, got {port}")
        return cls(
            config_dir=Path(env.get("CONFIG_DIR") or "/config"),
            media_roots=_split(env.get("MEDIA_ROOTS")),
            path_map=rules,
            plex_url=(env.get("PLEX_URL") or "").strip().rstrip("/"),
            plex_token=(env.get("PLEX_TOKEN") or "").strip(),
            plex_skip_sections=_split(env.get("PLEX_SKIP_SECTIONS")),
            api_key=(env.get("API_KEY") or "").strip(),
            port=port,
            concurrency=int(_number(env, "CONCURRENCY", 1, 1)),
            quiet_hours=parse_quiet_hours(env.get("QUIET_HOURS")),
            pause_on_transcode=(env.get("PAUSE_ON_TRANSCODE", "1").strip().lower() not in _FALSE),
            start_paused=(env.get("START_PAUSED", "0").strip().lower() not in _FALSE),
            scan_interval_s=_number(env, "SCAN_INTERVAL", 3600, 10),
            plex_interval_s=_number(env, "PLEX_INTERVAL", 900, 10),
            recent_days=_number(env, "RECENT_DAYS", 14, 0),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
            sample_interval_s=_number(env, "SAMPLE_INTERVAL", 1.0, MIN_SAMPLE_INTERVAL),
            sample_intervals=tuple(intervals),
            survey_interval_s=_survey_interval(env),
            on_demand_roots=_split(env.get("ON_DEMAND_ROOTS")),
            express_workers=int(_number(env, "EXPRESS_WORKERS", 1, 0)),
        )
