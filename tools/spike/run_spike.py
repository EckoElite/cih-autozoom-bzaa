"""Drive the zoom spike app on the Shield from the PC (read docs/ZOOM_SPIKE_REPORT.md first).

usage: python run_spike.py <out dir> <mode:clip:lead[:WxH]> [...]   (WxH sets the surface buffer size of A2 and B)
  e.g. python run_spike.py out A:clip_4k_hdr10.mkv:0 C:clip_4k_hdr10.mkv:0 C:clip_4k_hdr10.mkv:1

For each run: clears logcat, starts the app (am start -S), waits for the app to report the finished run, pulls its CSV
and the logcat of the run, and snapshots `dumpsys SurfaceFlinger --latency` for the app's video layer shortly after the
first cut (10 s), the eased ramp (33 s) and the first of the alternating cuts (49 s) of the clip. Needs adb on PATH or in
the WinGet platform-tools folder, and SHIELD_IP / SHIELD_ADB_PORT in the repo's .env.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

PKG = "dev.cih.zoomspike"
REMOTE = f"/sdcard/Android/data/{PKG}/files"
SNAPSHOTS = [("cut10", 11.0), ("ramp30", 34.0), ("cuts47", 49.5)]  # seconds after the run's first frame is due


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


def adb(*args: str, timeout: int = 60) -> str:
    done = subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    return done.stdout


def layer_names() -> list[str]:
    names = [n.strip() for n in adb("shell", "dumpsys SurfaceFlinger --list").splitlines()]
    return [n for n in names if ("zoomspike" in n) and (n.startswith(("SurfaceView - ", "zoomspike-video")))]


def one_run(out: Path, mode: str, clip: str, lead: int, fixed: str = "") -> None:
    tag = f"{mode}_{clip.rsplit('.', 1)[0]}_lead{lead}" + (f"_fixed{fixed}" if fixed else "")
    adb("shell", "am", "force-stop", PKG)
    adb("logcat", "-c")
    log = out / f"logcat_{tag}.txt"
    cap = subprocess.Popen([ADB, "-s", DEV, "logcat", "-v", "threadtime"], stdout=log.open("w", encoding="utf-8"))  # noqa: S603
    try:
        extra = ["--es", "fixed", fixed] if fixed else []
        adb("shell", "am", "start", "-S", "-n", f"{PKG}/.MainActivity", "--es", "mode", mode, "--es", "clip", clip,
            "--ei", "lead", str(lead), *extra)
        began = None
        snapshots = list(SNAPSHOTS)
        deadline = time.time() + 150
        while time.time() < deadline:
            time.sleep(0.5)
            text = log.read_text(encoding="utf-8", errors="replace")
            if began is None and re.search(r"ZoomSpike: start ", text):
                began = time.time() + 0.5  # the first frame is due about half a second after the start line
            if "ZoomSpike: wrote " in text:
                break
            if "FATAL EXCEPTION" in text:
                print(f"{tag}: the app crashed; see {log.name}")
                break
            if began and snapshots and time.time() - began >= snapshots[0][1]:
                name, _ = snapshots.pop(0)
                for layer in layer_names():
                    # one remote command string: the layer names contain spaces
                    (out / f"sf_{tag}_{name}_{re.sub(r'[^A-Za-z0-9]+', '_', layer)[:40]}.txt").write_text(
                        adb("shell", f"dumpsys SurfaceFlinger --latency '{layer}'"), encoding="utf-8")
        time.sleep(2)
    finally:
        cap.terminate()
    listing = adb("shell", "ls", "-t", REMOTE).split()
    csvs = [n for n in listing if n.startswith(f"run_{mode}_") and n.endswith(".csv")]
    if csvs:
        subprocess.run([ADB, "-s", DEV, "pull", f"{REMOTE}/{csvs[0]}", str(out / f"{tag}.csv")], capture_output=True, check=False)  # noqa: S603
        print(f"{tag}: ok -> {tag}.csv", flush=True)
    else:
        print(f"{tag}: no CSV found", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([ADB, "connect", DEV], capture_output=True, check=False)  # noqa: S603
    for spec in sys.argv[2:]:
        m, c, ld, *rest = spec.split(":")
        one_run(out_dir, m, c, int(ld), rest[0] if rest else "")
