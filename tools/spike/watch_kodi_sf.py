"""Record what Kodi's own video layer shows on the Shield while a title plays (read-only; nothing is changed on the Shield).

usage: python watch_kodi_sf.py <out dir> [seconds]        (default 600)
Every 5 s it takes `dumpsys SurfaceFlinger --latency` of Kodi's video layer (about the last 20 s of frames), and it
writes logcat to a file at the same time. Afterwards run  python watch_kodi_sf.py <out dir> --report  to merge the
snapshots and print, per 5 s of playback, how many frames were shown, how many gaps were longer than 1.5 frame periods (a
missed frame) and shorter than half a period (a late frame followed by a catch-up), for 24p content, so a ramp shows up as a run of bad seconds.
Needs SHIELD_IP / SHIELD_ADB_PORT in the repo's .env.
"""

import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

NEVER = 9223372036854775807


def find_adb() -> str:
    hits = list((Path.home() / "AppData/Local/Microsoft/WinGet/Packages").glob("Google.PlatformTools*/platform-tools/adb.exe"))
    return str(hits[0]) if hits else "adb"


ADB = find_adb()


def env() -> dict[str, str]:
    out = {}
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


CFG = env()
DEV = f"{CFG['SHIELD_IP']}:{CFG['SHIELD_ADB_PORT']}"


def adb(cmd: str, timeout: int = 30) -> str:
    done = subprocess.run([ADB, "-s", DEV, "shell", cmd], capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    return done.stdout


def kodi_layers() -> list[str]:
    names = [n.strip() for n in adb("dumpsys SurfaceFlinger --list").splitlines()]
    return [n for n in names if n.startswith("SurfaceView") and "org.xbmc.kodi" in n]


def capture(out: Path, seconds: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([ADB, "connect", DEV], capture_output=True, check=False)  # noqa: S603
    subprocess.run([ADB, "-s", DEV, "logcat", "-c"], capture_output=True, check=False)  # noqa: S603
    cap = subprocess.Popen([ADB, "-s", DEV, "logcat", "-v", "threadtime"], stdout=(out / "logcat.txt").open("w", encoding="utf-8"))  # noqa: S603
    end = time.time() + seconds
    n = 0
    try:
        while time.time() < end:
            for layer in kodi_layers():
                text = adb(f"dumpsys SurfaceFlinger --latency '{layer}'")
                if text.count("\n") > 5:
                    (out / f"snap_{n:04d}.txt").write_text(f"# layer {layer}\n{text}", encoding="utf-8")
                    n += 1
            time.sleep(5)
    finally:
        cap.terminate()
    print(f"{n} snapshots in {out}")


def report(out: Path) -> None:
    rows: dict[int, tuple[int, int]] = {}
    period = 16683333
    for f in sorted(out.glob("snap_*.txt")):
        lines = f.read_text(encoding="utf-8").splitlines()[1:]
        if not lines:
            continue
        period = int(lines[0].split()[0])
        for ln in lines[1:]:
            p = ln.split()
            if len(p) >= 3 and int(p[0]) > 0:
                rows[int(p[0])] = (int(p[0]), int(p[1]))
    ordered = sorted(rows.values())
    shown = [r for r in ordered if 0 < r[1] < NEVER]
    print(f"{len(ordered)} buffers in all, {len(ordered) - len(shown)} never shown, refresh {1e9 / period:.2f} Hz")
    if not shown:
        return
    start = shown[0][1]
    frame_ns = 1e9 * 1001 / 24000  # the clips in question are 23.976 fps; on a 23.976 Hz display one frame is one refresh
    buckets: dict[int, Counter] = {}
    for a, b in zip(shown, shown[1:], strict=False):
        gap = b[1] - a[1]
        k = int((b[1] - start) / 5e9)
        c = buckets.setdefault(k, Counter())
        c["frames"] += 1
        if gap > 1.5 * frame_ns:
            c["long"] += 1  # a frame missed: the gap is two or more frame periods
        elif gap < 0.5 * frame_ns:
            c["short"] += 1  # a frame shown late and followed at once by the next
    print("window (s of capture): frames shown, long gaps (a frame missed), short gaps (late then catch-up); 5 s = 120 frames at 24p")
    for k in sorted(buckets):
        c = buckets[k]
        flag = "   <==" if c["long"] or c["short"] else ""
        print(f"  {k * 5:4d}-{k * 5 + 5:4d} s: {c['frames']:3d}  {c['long']:2d}  {c['short']:2d}{flag}")
    log = out / "logcat.txt"
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        changed = re.findall(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d+).*XBMCVideoView: Changed", text, re.M)
        print(f"logcat: {len(changed)} 'XBMCVideoView: Changed' lines" + (f", first {changed[0]}, last {changed[-1]}" if changed else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    folder = Path(sys.argv[1])
    if "--report" in sys.argv:
        report(folder)
    else:
        capture(folder, int(sys.argv[2]) if len(sys.argv) > 2 else 600)
