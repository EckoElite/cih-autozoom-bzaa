"""The container entry point, started for real as a process: it scans, analyses, serves, and shuts down cleanly."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cih_analyzer.config import Settings
from cih_analyzer.server import build, main

from .conftest import NEEDS_FFMPEG
from .synth import Seg, make_video, rect_for_ratio

SRC = str(Path(__file__).resolve().parents[1] / "src")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, key=None):
    request = urllib.request.Request(url, headers={"X-Api-Key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@NEEDS_FFMPEG
def test_the_service_starts_analyses_a_film_serves_its_timeline_and_stops(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    w, h = 640, 360
    make_video(
        media / "Film (2020).mkv",
        [Seg(8, rect_for_ratio(2.39, w, h), level=110), Seg(8, rect_for_ratio(16 / 9, w, h), level=150)],
    )
    base = str(media).replace("\\", "/")
    port = free_port()
    env = dict(os.environ)
    env.update(
        PYTHONPATH=SRC, CONFIG_DIR=str(tmp_path / "config"), MEDIA_ROOTS=base, PORT=str(port),
        PATH_MAP=f"{base}|/movies", API_KEY="k3y-value", SCAN_INTERVAL="30", LOG_LEVEL="INFO",
    )  # fmt: skip
    log = tmp_path / "server.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "cih_analyzer.server"], env=env, stdout=out, stderr=subprocess.STDOUT
        )  # noqa: S603
        try:
            root = f"http://127.0.0.1:{port}"
            deadline = time.time() + 30
            status = None
            while time.time() < deadline:
                try:
                    status, body = get(f"{root}/health")
                    break
                except OSError:
                    time.sleep(0.2)
            assert status == 200 and body["ok"] is True
            assert get(f"{root}/api/v1/status")[0] == 401  # the key is enforced
            timeline = None
            while time.time() < deadline + 60:
                code, body = get(f"{root}/api/v1/timeline?path=/movies/Film%20(2020).mkv", key="k3y-value")
                if code == 200:
                    timeline = body
                    break
                time.sleep(0.5)
            assert timeline is not None, log.read_text(errors="replace")
            assert [s["boundary"]["frame"] for s in timeline["segments"]] == [0, 200] and timeline[
                "override"
            ] is None
            assert get(f"{root}/api/v1/status", key="k3y-value")[1]["counts"] == {"done": 1}
        finally:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail("the server did not stop when asked")
    text = log.read_text(errors="replace")
    assert "k3y-value" not in text  # the API key never reaches the log
    assert (tmp_path / "config" / "cih-analyzer.sqlite").exists()


def test_a_bad_setting_stops_the_service_with_a_message(monkeypatch, capsys):
    monkeypatch.setenv("QUIET_HOURS", "evening")
    assert main() == 2
    assert "QUIET_HOURS" in capsys.readouterr().err


def test_build_wires_the_app_to_a_store_in_the_config_dir(tmp_path):
    app, scheduler, store = build(Settings(config_dir=tmp_path / "new" / "dir"))
    assert (tmp_path / "new" / "dir" / "cih-analyzer.sqlite").exists() and app.title.startswith("CIH")
    assert scheduler.store is store
    store.close()
