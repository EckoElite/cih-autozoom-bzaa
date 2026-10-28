"""Timeline schema v1 (spec Phase 4), as validated models.

A timeline says, for one file, which part of the 16:9 frame holds the picture at each moment. Times are
seconds on the player's clock (0 is the first frame; the analyzer subtracts the stream's start time). Hard
cuts are `segments`; gradual matte moves are `ramps` with one fitted entry per frame. Validation rejects a
timeline that could not be played back correctly (overlapping segments, a ramp whose frame list does not
match its range).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
TIME_EPSILON = 1e-6


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Params(_Model):
    min_segment_s: float = Field(3.0, gt=0)
    dark_yavg: float = Field(0.0, ge=0)
    cropdetect_limit: float = Field(0.0, ge=0)


class FileInfo(_Model):
    path: str
    size: int = Field(ge=0)
    mtime: float = Field(ge=0)
    quick_hash: str = Field(pattern=r"^[0-9a-f]{40}$")


class PlexIds(_Model):
    part_id: int | None = None
    rating_key: int | None = None


class Video(_Model):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sar: str = Field(pattern=r"^\d+:\d+$")
    dar: float = Field(gt=0)
    fps: float = Field(gt=0)
    start_time: float
    duration: float = Field(ge=0)
    hdr: str = Field(pattern=r"^(SDR|HDR10|HDR10\+|HLG|DV-P\d+)$")


class Active(_Model):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class Boundary(_Model):
    kind: Literal["cut", "ramp"]
    frame: int = Field(ge=0)
    scene_cut: bool | None = None


class Segment(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    active: Active
    aspect: float = Field(gt=0)
    preset: float | None = None
    confidence: float = Field(ge=0, le=1)
    boundary: Boundary

    @model_validator(mode="after")
    def _ordered(self) -> Segment:
        if self.end <= self.start:
            raise ValueError("segment end must be after its start")
        return self


class RampFrame(_Model):
    f: int = Field(ge=0)
    t: float = Field(ge=0)
    top: float
    bottom: float
    left: float
    right: float
    readable: bool


class Ramp(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    frames: list[RampFrame]

    @model_validator(mode="after")
    def _consistent(self) -> Ramp:
        if self.end_frame < self.start_frame or self.end < self.start:
            raise ValueError("ramp must end at or after its start")
        if len(self.frames) != self.end_frame - self.start_frame + 1:
            raise ValueError("a ramp stores one entry for every frame from start_frame to end_frame")
        for offset, frame in enumerate(self.frames):
            if frame.f != self.start_frame + offset:
                raise ValueError("ramp frames must be consecutive from start_frame")
            if offset and frame.t <= self.frames[offset - 1].t:
                raise ValueError("ramp frame times must increase")
        return self


class Timeline(_Model):
    schema_version: Literal[1] = Field(SCHEMA_VERSION, alias="schema")
    analyzer_version: str
    params: Params
    file: FileInfo
    plex: PlexIds = Field(default_factory=PlexIds)
    video: Video
    segments: list[Segment]
    ramps: list[Ramp] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @field_validator("segments")
    @classmethod
    def _segments_in_order(cls, segments: list[Segment]) -> list[Segment]:
        for before, after in zip(segments, segments[1:], strict=False):
            if after.start < before.end - TIME_EPSILON:
                raise ValueError("segments must be in time order and must not overlap")
        return segments

    def to_json(self, indent: int | None = None) -> str:
        """JSON with the spec's key names (`schema`, not `schema_version`)."""
        return self.model_dump_json(by_alias=True, indent=indent)


def load_timeline(data: str | bytes | dict) -> Timeline:
    """Parse and validate a stored timeline; raises pydantic.ValidationError when it is not schema v1."""
    if isinstance(data, dict):
        return Timeline.model_validate(data)
    return Timeline.model_validate_json(data)
