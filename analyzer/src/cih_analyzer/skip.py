"""What the analyzer leaves alone, and the single entry point that decides it.

Skip rules (spec section 2 and Phase 4, plus the user's Phase 4 decision to skip extras and trailers):
  * disc images (ISO), non-video files, 3D files (filename tags or an MVC stream), audio-only files;
  * extras and trailers, by Plex's local-extras conventions (folder names and `-trailer`-style suffixes);
  * broken files: logged by the caller and passed over, never fatal.
Every skip carries a short machine-readable reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

from .probe import NoVideoStream, ProbeError, VideoInfo, probe_video

VIDEO_EXTENSIONS = frozenset(
    {
        ".mkv",
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".ts",
        ".m2ts",
        ".mts",
        ".wmv",
        ".webm",
        ".mpg",
        ".mpeg",
        ".vob",
        ".flv",
    }
)
DISC_IMAGE_EXTENSIONS = frozenset({".iso", ".img"})
THREE_D_TAGS = frozenset({"3d", "hsbs", "fsbs", "hou", "fou"})
EXTRAS_DIRECTORIES = frozenset(
    {
        "extras",
        "featurettes",
        "trailers",
        "behind the scenes",
        "deleted scenes",
        "interviews",
        "scenes",
        "shorts",
        "other",
    }
)
EXTRAS_SUFFIXES = (
    "-trailer",
    "-featurette",
    "-behindthescenes",
    "-deleted",
    "-interview",
    "-scene",
    "-short",
    "-other",
)
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class SkipConfig:
    skip_extras: bool = True
    three_d_tags: frozenset[str] = field(default=THREE_D_TAGS)
    extras_directories: frozenset[str] = field(default=EXTRAS_DIRECTORIES)
    extras_suffixes: tuple[str, ...] = EXTRAS_SUFFIXES


@dataclass(frozen=True)
class InspectResult:
    info: VideoInfo | None
    skip: str | None


def path_skip_reason(path: str, config: SkipConfig = SkipConfig()) -> str | None:  # noqa: B008 - frozen, shared
    """A reason to skip decided from the path alone (no file access), or None."""
    p = PurePath(path)
    ext = p.suffix.lower()
    if ext in DISC_IMAGE_EXTENSIONS:
        return "iso"
    if ext not in VIDEO_EXTENSIONS:
        return "not-video"
    stem = p.stem.lower()
    if config.three_d_tags & {t for t in _TOKEN_SPLIT.split(stem) if t}:
        return "3d-filename"
    if config.skip_extras:
        if any(part.lower() in config.extras_directories for part in p.parts[:-1]):
            return "extra"
        if stem.endswith(config.extras_suffixes):
            return "extra"
    return None


def info_skip_reason(info: VideoInfo) -> str | None:
    """A reason to skip decided from what ffprobe found, or None."""
    return "3d-mvc" if info.has_mvc else None


def inspect_file(path: str, config: SkipConfig = SkipConfig()) -> InspectResult:  # noqa: B008
    """Apply the skip rules, probing the file only when the path did not already rule it out.

    Never raises for a bad file: an unreadable or corrupt file comes back as `broken: <ffprobe's message>`.
    """
    reason = path_skip_reason(path, config)
    if reason:
        return InspectResult(None, reason)
    try:
        info = probe_video(path)
    except NoVideoStream:
        return InspectResult(None, "audio-only")
    except ProbeError as exc:
        return InspectResult(None, f"broken: {exc}")
    reason = info_skip_reason(info)
    return InspectResult(None if reason else info, reason)
