"""Read-only: watch what Kodi on the Shield is playing and print each change (Phase 5 step 1).

Polls Kodi's JSON-RPC (credentials from .env: KODI_USER, KODI_PASS, SHIELD_IP, KODI_HTTP_PORT) every 2 s and prints
the playing file's URL (Plex tokens and other query values redacted), its Plex ids, the player's position and
speed, and the video stream's size. Nothing is sent to Kodi except these queries.

usage: python tools/watch_playback.py [seconds]      (default: until stopped)
"""

import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def env() -> dict[str, str]:
    out = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def redact(url: str) -> str:
    """Keep the path and the names of the query values; drop the values that could be secrets."""
    parts = urlsplit(url)
    keep = {"X-Plex-Token": "REDACTED", "X-Plex-Client-Identifier": "REDACTED"}
    query = "&".join(f"{k}={keep.get(k, v if len(v) < 40 else v[:12] + '...')}" for k, v in parse_qsl(parts.query))
    host = re.sub(r"\d+\.\d+\.\d+\.\d+", "<ip>", parts.netloc)
    return f"{parts.scheme}://{host}{parts.path}" + (f"?{query}" if query else "")


def main() -> None:
    cfg = env()
    base = f"http://{cfg['SHIELD_IP']}:{cfg.get('KODI_HTTP_PORT', '8080')}/jsonrpc"
    auth = base64.b64encode(f"{cfg['KODI_USER']}:{cfg['KODI_PASS']}".encode()).decode()

    def call(method: str, params: dict | None = None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        req = urllib.request.Request(base, body, {"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - LAN Kodi
            return json.load(resp).get("result")

    deadline = time.time() + float(sys.argv[1]) if len(sys.argv) > 1 else None
    last = None
    while deadline is None or time.time() < deadline:
        try:
            players = call("Player.GetActivePlayers") or []
            if not players:
                state = ("idle",)
            else:
                pid = players[0]["playerid"]
                item = call("Player.GetItem", {"playerid": pid, "properties": ["file", "title", "year", "streamdetails", "mediapath"]})["item"]
                props = call("Player.GetProperties", {"playerid": pid, "properties": ["time", "speed", "percentage", "currentvideostream"]})
                info = call("XBMC.GetInfoLabels", {"labels": ["Player.Filenameandpath", "Player.Process(videowidth)", "Player.Process(videoheight)", "Player.Process(videodar)", "Player.Process(deintmethod)"]})
                t = props["time"]
                state = (
                    "playing",
                    redact(item.get("file", "")),
                    redact(info.get("Player.Filenameandpath", "")),
                    item.get("title"),
                    item.get("year"),
                    f"{info.get('Player.Process(videowidth)')}x{info.get('Player.Process(videoheight)')} dar={info.get('Player.Process(videodar)')}",
                    f"speed={props['speed']} t={t['hours']}:{t['minutes']:02}:{t['seconds']:02}",
                )
            # print on a change of what is playing, not on every tick of the clock
            key = state[:6] if state[0] == "playing" else state
            if key != last:
                print(time.strftime("%H:%M:%S"), *state, flush=True)
                last = key
        except Exception as exc:  # noqa: BLE001 - Kodi may be starting or the network may drop; keep watching
            if last != ("error", str(exc)[:60]):
                print(time.strftime("%H:%M:%S"), "error", str(exc)[:100], flush=True)
                last = ("error", str(exc)[:60])
        time.sleep(2)


if __name__ == "__main__":
    main()
