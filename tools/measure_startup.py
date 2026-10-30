"""Measure how long Kodi on the Shield takes to open PM4K, from the launch to PM4K's first window (Phase 5).

Restarts Kodi N times (Application.Quit over JSON-RPC, then a launch over adb) and reads each run's kodi.log. Nothing
is changed on the Shield except that Kodi is restarted; it refuses to run while something is playing.

usage: python tools/measure_startup.py [runs]        (default 3)
Prints, per run, the seconds from Kodi's first log line to: the skin being up, this add-on's service starting, PM4K's
script starting, and PM4K's first window loading (the number that matters).
"""

import base64
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ADB = next(
    (str(p) for p in (Path.home() / "AppData/Local/Microsoft/WinGet/Packages").glob("Google.PlatformTools*/platform-tools/adb.exe")),
    "adb",
)
KODI = "/sdcard/Android/data/org.xbmc.kodi/files/.kodi"
MARKS = {
    "skin up": r"Loading skin file: Startup\.xml",
    "our service": r"\[service\.cih\.zoom\] started",
    "PM4K script": r"script\.plexmod: SingleInstance lockfile",
    "PM4K window": r"script\.plexmod/resources/skins/Main/1080i/script-plex-(user_s|background|home)",
}


def env() -> dict[str, str]:
    out = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


CFG = env()
DEV = f"{CFG['SHIELD_IP']}:{CFG['SHIELD_ADB_PORT']}"


def adb(*args: str) -> str:
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=60, check=False).stdout  # noqa: S603


def rpc(method: str, params: dict | None = None):
    req = urllib.request.Request(
        f"http://{CFG['SHIELD_IP']}:{CFG.get('KODI_HTTP_PORT', '8080')}/jsonrpc",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"{CFG['KODI_USER']}:{CFG['KODI_PASS']}".encode()).decode(),
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - LAN Kodi
        return json.load(resp).get("result")


def stamp(line: str) -> float | None:
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)", line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp() if m else None


def one_run() -> dict[str, float | None]:
    subprocess.run([ADB, "connect", DEV], capture_output=True, check=False)  # noqa: S603
    if adb("shell", "pidof", "org.xbmc.kodi").strip():
        try:
            if rpc("Player.GetActivePlayers"):
                sys.exit("something is playing: not restarting Kodi")
            rpc("Application.Quit")
        except OSError:
            adb("shell", "am", "force-stop", "org.xbmc.kodi")
        for _ in range(20):
            time.sleep(1)
            if not adb("shell", "pidof", "org.xbmc.kodi").strip():
                break
    time.sleep(3)
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "monkey", "-p", "org.xbmc.kodi", "-c", "android.intent.category.LAUNCHER", "1")
    found: dict[str, float | None] = dict.fromkeys(MARKS)
    start = None
    for _ in range(90):
        time.sleep(1)
        lines = adb("shell", "cat", f"{KODI}/temp/kodi.log").splitlines()
        stamps = [(stamp(line), line) for line in lines if stamp(line)]
        if not stamps:
            continue
        start = stamps[0][0]
        for name, pattern in MARKS.items():
            for t, line in stamps:
                if found[name] is None and re.search(pattern, line):
                    found[name] = round(t - start, 1)
        if found["PM4K window"] is not None:
            break
    return found


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    results = []
    for n in range(1, runs + 1):
        r = one_run()
        results.append(r)
        print(f"run {n}: " + ", ".join(f"{k} {v}" for k, v in r.items()), flush=True)
    done = [r["PM4K window"] for r in results if r["PM4K window"] is not None]
    if done:
        print(f"PM4K window after {min(done):.1f} to {max(done):.1f} s (mean {sum(done) / len(done):.1f} s)")
