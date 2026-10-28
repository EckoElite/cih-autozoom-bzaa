"""CIH zoom service (Kodi 21, Python 3): applies a per-title zoom timeline while a video plays.

Prototype stage (Phase 2, Spike A): the timeline is fetched from the same HTTP location as a test video named
boundary_*.mkv (boundary_x.mkv -> boundary_x.timeline.json). The analyzer lookup replaces that in Phase 5.
Every event is logged to the add-on's profile folder (events.jsonl) and to kodi.log.
"""
import json
import os
import re
import threading
import time
import urllib.request

import xbmc
import xbmcaddon
import xbmcvfs

import zoomcore

ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[service.cih.zoom] " + msg, level)


def set_zoom(zoom):
    reply = xbmc.executeJSONRPC(zoomcore.zoom_request(zoom))
    return '"result":"OK"' in reply.replace(" ", "")


class ZoomPlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.timeline = None
        self.worker = None
        self.stop_flag = threading.Event()
        self.logfile = None

    # --- lifecycle ---------------------------------------------------------------------------------------------
    def onAVStarted(self):
        self.begin()

    def onPlayBackResumed(self):
        self.begin()

    def onPlayBackSeek(self, time_ms, seek_offset):
        self.begin()

    def onPlayBackPaused(self):
        self.halt()

    def onPlayBackStopped(self):
        self.end()

    def onPlayBackEnded(self):
        self.end()

    def begin(self):
        self.halt()
        if not ADDON.getSettingBool("enabled"):
            return
        if self.timeline is None:
            self.timeline = self.load_timeline()
        if not self.timeline:
            return
        self.stop_flag.clear()
        self.worker = threading.Thread(target=self.work, name="cih-zoom", daemon=True)
        self.worker.start()

    def halt(self):
        self.stop_flag.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(1.0)
        self.worker = None

    def end(self):
        self.halt()
        self.timeline = None
        set_zoom(1.0)
        if self.logfile:
            self.logfile.close()
            self.logfile = None

    # --- timeline ----------------------------------------------------------------------------------------------
    def load_timeline(self):
        try:
            url = self.getPlayingFile()
        except RuntimeError:
            return None
        if not re.search(r"/boundary_[^/]*\.mkv$", url):
            return None
        tl_url = re.sub(r"\.mkv$", ".timeline.json", url)
        try:
            with urllib.request.urlopen(tl_url, timeout=2) as resp:
                timeline = json.loads(resp.read().decode("utf-8"))
            log("loaded timeline %s (%d cuts, %d ramps)" % (tl_url, len(timeline["cuts"]), len(timeline["ramps"])))
            return timeline
        except Exception as exc:  # network or parse failure: do nothing rather than guess
            log("timeline fetch failed: %r" % (exc,), xbmc.LOGWARNING)
            return None

    # --- worker ------------------------------------------------------------------------------------------------
    def open_log(self):
        if self.logfile is None:
            xbmcvfs.mkdirs(PROFILE)
            self.logfile = open(os.path.join(PROFILE, "events.jsonl"), "a", encoding="utf-8")

    def emit(self, record):
        self.open_log()
        record["wall"] = time.time()
        self.logfile.write(json.dumps(record) + "\n")
        self.logfile.flush()

    def work(self):
        clock = zoomcore.PlayerClock(self.getTime)
        try:
            # about 0.12 s of sampling to start (the clock keeps being refined while events are pending)
            for _ in range(24):
                clock.sample()
                time.sleep(0.005)
            if not clock.ready():
                log("player clock not readable", xbmc.LOGWARNING)
                return
            lead = ADDON.getSettingInt("lead_ms") / 1000.0
            frame = zoomcore.frame_at(clock.player_now(), self.timeline["fps"])
            now_zoom = zoomcore.zoom_at_frame(self.timeline, frame)
            set_zoom(now_zoom)
            self.emit({"kind": "start", "frame": frame, "zoom": now_zoom, "lead_s": lead,
                       "offset_s": clock.offset()})
            events = zoomcore.build_events(self.timeline, lead, from_frame=frame + 1)
            zoomcore.run_events(events, clock, set_zoom, self.stop_flag.is_set,
                                log=lambda r: self.emit(dict(r, kind="event")))
            self.emit({"kind": "done"})
        except Exception as exc:
            log("worker failed: %r" % (exc,), xbmc.LOGERROR)


def main():
    monitor = xbmc.Monitor()
    player = ZoomPlayer()
    log("started")
    while not monitor.abortRequested():
        if monitor.waitForAbort(1.0):
            break
    player.halt()
    log("stopped")


if __name__ == "__main__":
    main()
