"""Spike A driver: play the boundary test video in Kodi and send zoom changes at controlled leads.

For every cut in the timeline the new zoom is sent LEAD seconds before the cut (leads are spread over a
range and shuffled, so the slow-motion capture can fit "frames wrong" against lead). During the ramps a new
zoom is sent for every frame at a fixed lead.

Times are on Kodi's reported player clock; the script maps its own high-resolution clock onto it from
periodic `Player.GetProperties time` polls (median offset), then schedules each command.

Credentials and the Shield address come from .env (SHIELD_IP, KODI_HTTP_PORT, KODI_USER, KODI_PASS).
The PC address Kodi uses to fetch the test file is detected, or given with --pc-ip.

Usage:
  python drive_boundary.py --dry-run
  python drive_boundary.py [--predelay 20] [--ramp-lead 0.20] [--file boundary_hdr10_23976.mkv]
"""
import argparse
import base64
import http.client
import json
import random
import socket
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tools" / "testpatterns" / "out"
LEAD_SET = [0.10, 0.13, 0.16, 0.19, 0.22, 0.25, 0.28]


def load_env():
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Kodi:
    def __init__(self, env):
        self.host, self.port = env["SHIELD_IP"], int(env.get("KODI_HTTP_PORT", "8080"))
        token = base64.b64encode(f"{env['KODI_USER']}:{env['KODI_PASS']}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token, "Content-Type": "application/json"}
        self.conn = None
        self.ids = 0

    def call(self, method, params=None):
        """Returns (result_or_None, error_or_None, t_send, t_recv) using time.perf_counter()."""
        self.ids += 1
        body = {"jsonrpc": "2.0", "method": method, "id": self.ids}
        if params is not None:
            body["params"] = params
        data = json.dumps(body)  # floats stay floats ("1.0"), which Kodi's view-mode call requires
        for attempt in (0, 1):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
                t0 = time.perf_counter()
                self.conn.request("POST", "/jsonrpc", data, self.headers)
                resp = self.conn.getresponse()
                raw = resp.read()
                t1 = time.perf_counter()
                msg = json.loads(raw)
                return msg.get("result"), msg.get("error"), t0, t1
            except (http.client.HTTPException, OSError):
                self.conn = None
                if attempt:
                    raise

    def set_zoom(self, zoom):
        return self.call("Player.SetViewMode", {"viewmode": {"zoom": float(zoom), "verticalshift": 0.0}})

    def player_time(self):
        res, err, t0, t1 = self.call("Player.GetProperties", {"playerid": 1, "properties": ["time"]})
        if not res:
            return None, t0, t1
        t = res["time"]
        return t["hours"] * 3600 + t["minutes"] * 60 + t["seconds"] + t["milliseconds"] / 1000.0, t0, t1


class Clock:
    """Maps time.perf_counter() to Kodi's player clock (offset = median of recent player_t - wall_mid)."""

    def __init__(self, kodi):
        self.kodi = kodi
        self.samples = []
        self.last_poll = 0.0

    def poll(self):
        pt, t0, t1 = self.kodi.player_time()
        if pt is None:
            return False
        self.samples.append(pt - (t0 + t1) / 2)
        self.samples = self.samples[-9:]
        self.last_poll = time.perf_counter()
        return True

    def offset(self):
        return statistics.median(self.samples)

    def now(self):
        return time.perf_counter() + self.offset()


def detect_pc_ip(shield_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((shield_ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def build_events(timeline, ramp_lead, seed):
    fps_num, fps_den = (24000, 1001) if str(timeline["fps"]).startswith("23.9") else (24, 1)
    ftime = lambda f: f * fps_den / fps_num
    rng = random.Random(seed)
    cuts = timeline["cuts"]
    leads = [LEAD_SET[i % len(LEAD_SET)] for i in range(len(cuts))]
    rng.shuffle(leads)
    events = []
    for cut, lead in zip(cuts, leads):
        events.append({"kind": "cut", "frame": cut["frame"], "from": cut["from"], "to": cut["to"],
                       "zoom": cut["zoom_to"], "lead": lead, "target": ftime(cut["frame"]) - lead})
    for ramp in timeline["ramps"]:
        for i, z in enumerate(ramp["zoom"]):
            f = ramp["start_frame"] + i
            events.append({"kind": "ramp_" + ramp["kind"], "frame": f, "zoom": z, "lead": ramp_lead,
                           "target": ftime(f) - ramp_lead})
    events.sort(key=lambda e: e["target"])
    return events, ftime(timeline["frames"])


def open_playback(kodi, url, attempts=6):
    for a in range(1, attempts + 1):
        kodi.call("Player.Stop", {"playerid": 1})
        time.sleep(4)
        kodi.call("Player.Open", {"item": {"file": url}})
        for _ in range(16):
            time.sleep(0.5)
            pt, _, _ = kodi.player_time()
            if pt is not None and pt > 0.3:
                print(f"playing after attempt {a} (t={pt:.2f})")
                return True
        print(f"attempt {a}: playback did not start")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="boundary_hdr10_23976.mkv")
    ap.add_argument("--predelay", type=float, default=20.0, help="seconds to wait before starting (time to start filming)")
    ap.add_argument("--ramp-lead", type=float, default=0.20)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--pc-ip")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stem = args.file[:-4] if args.file.endswith(".mkv") else args.file
    timeline = json.loads((OUT / f"{stem}.timeline.json").read_text(encoding="utf-8"))
    events, duration = build_events(timeline, args.ramp_lead, args.seed)
    cuts = [e for e in events if e["kind"] == "cut"]
    print(f"{len(cuts)} cuts, {len(events) - len(cuts)} ramp commands, video {duration:.1f} s")
    print("cut frame -> lead (s):", ", ".join(f"{e['frame']}:{e['lead']:.2f}" for e in sorted(cuts, key=lambda e: e['frame'])))
    if args.dry_run:
        return

    env = load_env()
    kodi = Kodi(env)
    pc_ip = args.pc_ip or detect_pc_ip(env["SHIELD_IP"])
    url = f"http://{pc_ip}:8765/{args.file}"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = OUT / f"drive_{stem}_{stamp}.jsonl"
    (OUT / f"drive_{stem}_{stamp}.plan.json").write_text(json.dumps(events, indent=1), encoding="utf-8")

    print(f"head start {args.predelay:.0f} s: start filming now")
    time.sleep(args.predelay)
    if not open_playback(kodi, url):
        print("giving up: playback never started")
        return
    kodi.call("Player.SetRepeat", {"playerid": 1, "repeat": "off"})
    kodi.set_zoom(1.0)
    info = kodi.call("XBMC.GetInfoLabels", {"labels": ["VideoPlayer.HdrType", "VideoPlayer.VideoResolution"]})[0]
    clock = Clock(kodi)
    for _ in range(5):
        clock.poll()
        time.sleep(0.05)
    if not clock.samples:
        print("abort: could not read the player clock", file=sys.stderr)
        return
    print(f"clock offset {clock.offset():.3f} s; first event target {events[0]['target']:.3f} s, "
          f"player now {clock.now():.3f} s", flush=True)
    wall_start = datetime.now().isoformat(timespec="milliseconds")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps({"start": wall_start, "info": info, "offset_s": clock.offset()}) + "\n")
        log.flush()
        pending = list(events)
        failed_polls = 0
        sent = 0
        while pending:
            ev = pending[0]
            wait = ev["target"] - clock.now()
            if wait > 0.15 and time.perf_counter() - clock.last_poll > 0.5:
                if clock.poll():
                    failed_polls = 0
                else:
                    failed_polls += 1
                    clock.last_poll = time.perf_counter()
                    if failed_polls >= 4:
                        print("abort: player is gone", file=sys.stderr)
                        break
                continue
            if wait > 0.003:
                time.sleep(min(wait - 0.002, 0.05))
                continue
            est = clock.now()
            res, err, t0, t1 = kodi.set_zoom(ev["zoom"])
            log.write(json.dumps({"kind": ev["kind"], "frame": ev["frame"], "zoom": ev["zoom"], "lead": ev["lead"],
                                  "target_t": ev["target"], "sent_est_t": est, "late_ms": (est - ev["target"]) * 1000,
                                  "rtt_ms": (t1 - t0) * 1000, "ok": res == "OK" and not err,
                                  "wall": datetime.now().isoformat(timespec="milliseconds")}) + "\n")
            log.flush()
            pending.pop(0)
            sent += 1
            if sent <= 3 or sent % 40 == 0:
                print(f"sent {sent}/{len(events)}: {ev['kind']} frame {ev['frame']} zoom {ev['zoom']:.4f} "
                      f"late {1000 * (est - ev['target']):+.0f} ms, rtt {1000 * (t1 - t0):.0f} ms", flush=True)
        # let the video finish, then reset. After the end Kodi reports time 0 for playerid 1 rather than
        # "no player", so also stop on an empty active-player list or a hard timeout.
        deadline = time.perf_counter() + max(0.0, duration + 8.0 - clock.now())
        while time.perf_counter() < deadline:
            pt, _, _ = kodi.player_time()
            active = kodi.call("Player.GetActivePlayers")[0]
            if pt is None or pt >= duration - 0.4 or not active:
                break
            time.sleep(0.1)
        kodi.set_zoom(1.0)
        kodi.call("Player.Stop", {"playerid": 1})
        log.write(json.dumps({"end": datetime.now().isoformat(timespec="milliseconds")}) + "\n")
    print("done; log:", log_path)


if __name__ == "__main__":
    main()
