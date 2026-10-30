"""A provisional timeline from a survey that is still running (hybrid analysis: a title nobody has analysed yet).

When a player asks for a title, the survey starts at the playhead and works outward. After a few seconds it knows the
picture for the stretch around the playhead, long before it has seen the whole file. This turns what the survey has
so far into a timeline the player can use at once:

* the stretch the survey has covered and found steady gets that picture's rectangle (a little larger, to absorb the
  coarse pass's error), and everything else gets the whole frame, which never cuts picture;
* the stretch ends where the survey meets something that is not the same picture, so a change the survey has found
  is never played at the wrong zoom;
* nothing is claimed from dark or doubtful samples, nor from fewer than two bright ones.

It is a guess about a stretch, not a verdict about the file: the final timeline (refined at full resolution) replaces
it. The two ways to be wrong are not alike: too small a rectangle would leave the picture too big for the screen, so
the rectangle only ever errs large.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .coarse import Sample
from .geometry import EQUIVALENT_ZOOM, picture_zoom
from .probe import VideoInfo
from .survey import BRIGHT_YAVG, MIN_PICTURE_AREA, _lopsided

MIN_BRIGHT = 2  # bright samples needed before anything is claimed
MIN_VOUCHING_SLOTS = 2  # and they must come from at least this many places, so one odd shot claims nothing
BLACK_YAVG = (
    22.0  # a sample darker than this is a black frame (a fade, a gap): it shows nothing that could overhang
)
Rect = tuple[int, int, int, int]


def _bright(s: Sample, info: VideoInfo) -> bool:
    return s.yavg >= BRIGHT_YAVG and s.w * s.h >= MIN_PICTURE_AREA * info.width * info.height


def _rect(s: Sample) -> Rect:
    return s.x, s.y, s.w, s.h


@dataclass
class _Run:
    """A stretch of the survey that showed one picture: the slots it spans, the slots that vouched for it, and the
    bright samples it was judged by."""

    slots: list[int] = field(default_factory=list)
    vouched: list[int] = field(default_factory=list)
    bright: list[Sample] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)  # the rectangles of `bright`, counted as they come

    @property
    def rect(self) -> Rect:
        return self.counts.most_common(1)[0][0]

    def add(self, slot: int, here: list[Sample]) -> None:
        self.slots.append(slot)
        if here:
            self.vouched.append(slot)
            self.bright.extend(here)
            self.counts.update(_rect(s) for s in here)

    def valid(self) -> bool:
        return len(self.vouched) >= MIN_VOUCHING_SLOTS and len(self.bright) >= MIN_BRIGHT

    def trimmed(self) -> list[int]:
        """The slots up to the last one a bright sample vouched for."""
        return self.slots[: self.slots.index(self.vouched[-1]) + 1] if self.vouched else []


def _runs(info: VideoInfo, by_slot: dict[int, list[Sample]], order: list[int]) -> list[_Run]:
    """The stretches, in survey order, over which the picture was steady, each with the picture it showed.

    A run starts at a slot with a bright sample and goes on until a bright sample shows a clearly bigger picture (that
    would be cut off if it were played at the zoom found so far) or two slots in a row show a clearly smaller one (a
    different picture, not a dark crop); the slot that ends a run starts the next. Dark and doubtful slots neither
    extend nor end a run. A run is only worth anything if at least two slots vouch for it."""
    finished: list[_Run] = []
    run: _Run | None = None
    smaller: list[int] = []  # the slots in a row that showed a smaller picture, with their samples
    smaller_samples: list[list[Sample]] = []

    def close() -> None:
        if run is not None and run.valid():
            finished.append(run)

    for slot in order:
        here = [s for s in by_slot.get(slot, []) if _bright(s, info)]
        if run is None:
            if here:
                run = _Run()
                run.add(slot, here)
            continue
        usual = picture_zoom(run.rect, info)
        zooms = [picture_zoom(_rect(s), info) for s in here]
        if any(
            z < usual / (1 + EQUIVALENT_ZOOM) and not _lopsided(s, run.rect, info)
            for s, z in zip(here, zooms, strict=True)
        ):
            close()  # a bigger picture: it ends this run and begins the next
            run = _Run()
            run.add(slot, here)
            smaller, smaller_samples = [], []
            continue
        if any(z > usual * (1 + EQUIVALENT_ZOOM) for z in zooms):
            smaller.append(slot)
            smaller_samples.append(here)
            if len(smaller) >= 2:
                close()  # two in a row: a different, smaller picture begins
                run = _Run()
                for s, h in zip(smaller, smaller_samples, strict=True):
                    run.add(s, h)
                smaller, smaller_samples = [], []
            else:
                run.add(slot, [])  # one smaller slot: bars at worst, so it neither vouches nor ends the run
            continue
        smaller, smaller_samples = [], []
        run.add(slot, here)
    close()
    return finished


def _safe(samples: list[Sample], rect: Rect, info: VideoInfo) -> bool:
    """True if nothing a slot's samples show can overhang the screen when `rect` is zoomed to fit it: every sample is a
    black frame, or its crop lies inside `rect` (the visible picture is within the matte, whatever its brightness; a dark
    scene's crop is smaller than the real picture, never bigger). Such a slot proves nothing about the picture, but it
    cannot be hurt by the zoom, so a stretch may run on through it: a fade in, a dim opening, the credits."""
    if not samples:
        return False  # the seek failed: nothing known
    tol_x, tol_y = 0.01 * info.width, 0.01 * info.height
    x, y, w, h = rect
    return all(
        s.w <= 0
        or s.h <= 0
        or s.yavg < BLACK_YAVG
        or (
            s.x >= x - tol_x
            and s.y >= y - tol_y
            and s.x + s.w <= x + w + tol_x
            and s.y + s.h <= y + h + tol_y
        )
        for s in samples
    )


def _extended(
    core: list[int],
    rect: Rect,
    by_slot: dict[int, list[Sample]],
    order: list[int],
    taken: set[int],
    info: VideoInfo,
) -> list[int]:
    """`core` (a run's vouched slots) plus the safe slots just before it and just after it in survey order."""
    where = {slot: i for i, slot in enumerate(order)}
    slots = list(core)
    i = where[core[0]] - 1
    while i >= 0 and order[i] not in taken and _safe(by_slot.get(order[i], []), rect, info):
        slots.append(order[i])
        i -= 1
    i = where[core[-1]] + 1
    while i < len(order) and order[i] not in taken and _safe(by_slot.get(order[i], []), rect, info):
        slots.append(order[i])
        i += 1
    return slots


def _intervals(slots: list[int], step: float, duration: float) -> list[tuple[float, float]]:
    """The time spans of a set of slots, neighbouring slots joined."""
    spans: list[list[int]] = []
    for slot in sorted(set(slots)):
        if spans and slot == spans[-1][1] + 1:
            spans[-1][1] = slot
        else:
            spans.append([slot, slot])
    return [(a * step, min(duration, (b + 1) * step)) for a, b in spans]


def provisional_timeline(info: VideoInfo, by_slot: dict[int, list[Sample]], where: dict) -> dict | None:
    """A timeline-shaped dict (`provisional: true`, `coverage`, `video`, `segments`) for what the survey has seen, or
    None while it has seen too little. `where` is the survey's own report of how far it has got. Each steady stretch
    gets its own picture; everything else, the odd shot between stretches included, gets the whole frame."""
    slots, first = int(where["slots"]), int(where["first"])
    visited = [*range(first, first + int(where["forward"])), *range(0, int(where["wrapped"]))]
    runs = _runs(info, by_slot, [s for s in visited if s < slots])
    if not runs:
        return None
    step = float(where["step"])
    margin = 2 * -(-info.width // 960)  # the coarse pass is good to a couple of pixels of its 960-wide frame
    placed: list[tuple[float, float, Rect]] = []
    order = [s for s in visited if s < slots]
    taken = {slot for run in runs for slot in run.trimmed()}  # no run takes another's slots when it runs on
    for run in runs:
        x, y, w, h = run.rect
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(info.width, x + w + margin), min(info.height, y + h + margin)
        core = [s for s in run.trimmed() if s in run.vouched or _safe(by_slot.get(s, []), run.rect, info)]
        covered = _extended(core, run.rect, by_slot, order, taken, info)
        taken.update(covered)
        for a, b in _intervals(covered, step, info.duration):
            placed.append((a, b, (x0, y0, x1 - x0, y1 - y0)))
    placed.sort()
    whole: Rect = (0, 0, info.width, info.height)
    pieces: list[tuple[float, float, Rect]] = []
    cursor = 0.0
    for a, b, rect in placed:
        a = max(a, cursor)  # runs never overlap in time, but a rounding error must not make a negative gap
        if a > cursor + 1e-6:
            pieces.append((cursor, a, whole))
        if b > a:
            if pieces and pieces[-1][2] == rect and abs(pieces[-1][1] - a) < 1e-6:
                pieces[-1] = (pieces[-1][0], b, rect)  # neighbouring stretches of the same picture are one
            else:
                pieces.append((a, b, rect))
        cursor = max(cursor, b)
    if cursor < info.duration - 1e-6:
        pieces.append((cursor, info.duration, whole))
    sar_n, _, sar_d = info.sar.partition(":")
    sar = int(sar_n) / int(sar_d or 1)
    measured = {rect for _, _, rect in placed}
    segments = [
        {
            "start": a,
            "end": b,
            "active": {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]},
            "aspect": round(rect[2] * sar / rect[3], 3),
            "boundary": {"kind": "cut", "frame": 0 if i == 0 else round(a * info.fps), "scene_cut": None},
            "measured": rect in measured,
        }
        for i, (a, b, rect) in enumerate(pieces)
    ]
    covered = sorted({(a, b) for a, b, _ in placed})
    return {
        "provisional": True,
        "coverage": [[a, b] for a, b in covered],
        "video": {
            "width": info.width,
            "height": info.height,
            "sar": info.sar,
            "dar": info.dar,
            "fps": info.fps,
            "start_time": info.start_time,
            "duration": info.duration,
            "hdr": info.hdr,
        },
        "segments": segments,
        "ramps": [],
        "flags": ["provisional"],
    }
