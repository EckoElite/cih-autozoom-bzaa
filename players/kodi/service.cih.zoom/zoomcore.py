"""Pure-Python core of the CIH zoom service: timeline to events, player-clock estimate, scheduler.

Nothing here imports xbmc, so it runs (and is tested) on a PC. The Kodi glue is in service.py.

Timeline (JSON, the format written by tools/testpatterns/make_boundary_test.py; the analyzer's output
will be converted to the same shape):
  {"fps": "23.976" | "24",
   "cuts":  [{"frame": int, "zoom_from": float, "zoom_to": float}, ...],
   "ramps": [{"start_frame": int, "zoom": [float, ...]}, ...]}      # one zoom value per frame from start_frame
Every cut frame and every ramp frame is a "set point": from that frame on, the zoom is that value.
"""
import bisect
import json
import statistics
import time
from collections import deque

FPS = {"23.976": (24000, 1001), "24": (24, 1)}
STANDARD = ((24000, 1001), (24, 1), (25, 1), (30000, 1001), (30, 1), (48, 1), (50, 1), (60000, 1001), (60, 1))


def fps_pair(fps):
    """(numerator, denominator) of a frame rate given as a label ("23.976"), or a number snapped to a standard rate."""
    if str(fps) in FPS:
        return FPS[str(fps)]
    value = float(fps)
    num, den = min(STANDARD, key=lambda p: abs(p[0] / p[1] - value))
    if abs(num / den - value) / value < 0.002:
        return num, den
    return int(round(value * 1001)), 1001


def frame_time(frame, fps_label):
    num, den = fps_pair(fps_label)
    return frame * den / float(num)


def frame_at(seconds, fps_label):
    num, den = fps_pair(fps_label)
    return int(seconds * num / den + 1e-9)


def set_points(timeline):
    """Sorted list of (frame, zoom). Later entries for the same frame win."""
    pts = {}
    for cut in timeline.get("cuts", []):
        pts[int(cut["frame"])] = float(cut["zoom_to"])
    for ramp in timeline.get("ramps", []):
        for i, z in enumerate(ramp["zoom"]):
            pts[int(ramp["start_frame"]) + i] = float(z)
    return sorted(pts.items())


def initial_zoom(timeline):
    if "initial" in timeline:
        return float(timeline["initial"])
    cuts = timeline.get("cuts") or []
    return float(cuts[0]["zoom_from"]) if cuts else 1.0


def zoom_at_frame(timeline, frame):
    """The zoom that should be showing at `frame` (used on start, seek and resume)."""
    pts = set_points(timeline)
    frames = [f for f, _ in pts]
    i = bisect.bisect_right(frames, frame) - 1
    return pts[i][1] if i >= 0 else initial_zoom(timeline)


def build_events(timeline, lead_s, from_frame=0):
    """Events [{"target", "frame", "zoom"}] sorted by target time, for set points at or after from_frame.

    target is in seconds of player time: the frame's presentation time minus lead_s.
    """
    fps = timeline["fps"]
    return [{"target": frame_time(f, fps) - lead_s, "frame": f, "zoom": z}
            for f, z in set_points(timeline) if f >= from_frame]


def zoom_request(zoom):
    """JSON-RPC body for Player.SetViewMode. Kodi silently ignores integer-valued numbers, so both are floats."""
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "Player.SetViewMode",
                       "params": {"viewmode": {"zoom": float(zoom), "verticalshift": 0.0}}})


class PlayerClock:
    """Maps a monotonic wall clock onto the player's reported time.

    offset = median over recent samples of (player_time - wall_time); the player's time readout is
    coarse and noisy, the median over many samples is not.
    """

    def __init__(self, get_time, now=time.perf_counter, window=80):
        self.get_time = get_time
        self.now = now
        self.samples = deque(maxlen=window)

    def sample(self):
        w = self.now()
        try:
            t = self.get_time()
        except RuntimeError:  # Kodi raises this when nothing is playing
            return False
        if t is None:
            return False
        self.samples.append(t - (w + self.now()) / 2.0)
        return True

    def ready(self, minimum=8):
        return len(self.samples) >= minimum

    def offset(self):
        return statistics.median(self.samples)

    def player_now(self):
        return self.now() + self.offset()


def run_events(events, clock, send, should_stop, sleep=time.sleep, log=None, frame_s=1001 / 24000.0,
               coarse=0.12, spin=0.002):
    """Send each event when the player clock reaches its target.

    Far from the next target: keep sampling the clock and sleep in short steps. Close to it: sleep to just
    before the target, then spin. Events that are already more than a frame late are dropped, keeping only the
    newest of the overdue ones, so a stall never replays a backlog.
    """
    pending = list(events)
    sent_count = 0
    while pending and not should_stop():
        now = clock.player_now()
        overdue = [e for e in pending if e["target"] < now - frame_s]
        if len(overdue) > 1:
            dropped = overdue[:-1]
            pending = pending[len(dropped):]
            if log:
                log({"dropped": len(dropped), "up_to_frame": dropped[-1]["frame"]})
            continue
        ev = pending[0]
        wait = ev["target"] - now
        if wait > coarse:
            clock.sample()
            sleep(min(wait - coarse, 0.05))
            continue
        if wait > spin:
            sleep(wait - spin)
        while ev["target"] - clock.player_now() > 0 and not should_stop():
            pass
        t_send = clock.player_now()
        wall0 = clock.now()
        result = send(ev["zoom"])
        wall1 = clock.now()
        pending.pop(0)
        sent_count += 1
        if log:
            log({"frame": ev["frame"], "zoom": ev["zoom"], "target": ev["target"], "sent_at": t_send,
                 "late_ms": (t_send - ev["target"]) * 1000.0, "call_ms": (wall1 - wall0) * 1000.0,
                 "ok": bool(result)})
    return sent_count
