"""CIH zoom service (Kodi 21, Python 3): applies a per-title zoom timeline while a video plays (spec Phase 5).

On playback start it works out which title is playing, asks the analyzer for its timeline, turns the timeline into zoom
values (plan.py) and sends each zoom to Kodi shortly before its frame (zoomcore.py). With no timeline it applies the
fallback (the whole frame contained in the screen band), asks the analyzer for one, and keeps asking until it arrives.
Every event is logged to the add-on's profile folder (events.jsonl); tokens and keys are never logged.
"""

import json
import os
import re
import threading
import time

import xbmc
import xbmcaddon
import xbmcvfs

import client as client_mod
import identify
import plan as plan_mod
import zoomcore

ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
RELOAD_PROPERTY = "service.cih.zoom.reload"  # set on the home window by override.py; read by the main loop
POLL_FLOOR_S = 5  # the least time between asks while a title has no timeline


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[service.cih.zoom] " + msg, level)


def set_zoom(zoom):
    reply = xbmc.executeJSONRPC(zoomcore.zoom_request(zoom))
    return '"result":"OK"' in reply.replace(" ", "")


def geometry():
    return plan_mod.Geometry(
        band_top=ADDON.getSettingInt("band_top"),
        band_bottom=ADDON.getSettingInt("band_bottom"),
        scope_fill=ADDON.getSettingBool("scope_fill"),
    )


def make_client():
    return client_mod.AnalyzerClient(
        ADDON.getSetting("analyzer_url"),
        ADDON.getSetting("api_key"),
        os.path.join(PROFILE, "cache"),
    )


def playing_video_size():
    """The playing stream's size and display ratio as Kodi reports them, or None while they are not readable."""
    try:
        # Kodi writes numbers for display: "3,840" and "2,074"
        width = int(re.sub(r"\D", "", xbmc.getInfoLabel("Player.Process(videowidth)")) or 0)
        height = int(re.sub(r"\D", "", xbmc.getInfoLabel("Player.Process(videoheight)")) or 0)
        dar = float(xbmc.getInfoLabel("Player.Process(videodar)").replace(",", ".") or 0)
    except ValueError:
        return None
    if width < 16 or height < 16:
        return None
    sar = (dar * height / width) if dar > 0 else 1.0
    if abs(sar - 1.0) < 0.01:  # the label is rounded to two places: "1.78" for a 16:9 frame is square pixels
        sar = 1.0
    return plan_mod.Video(width, height, sar)


class ZoomPlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.ident = None
        self.lookup = None
        self.plan = None
        self.worker = None
        self.poller = None
        self.stop_flag = threading.Event()
        self.poll_stop = threading.Event()
        self.logfile = None

    # --- what Kodi tells us --------------------------------------------------------------------------------------
    def onPlayBackStarted(self):
        # Kodi restores the view each file was last left at, so a title can start at the wrong zoom. Correct it as
        # early as Kodi allows (here), before onAVStarted starts the scheduler.
        if not self.wanted():
            return
        self.halt()  # a title that follows another without a stop in between
        self.prepare()
        if self.plan:
            set_zoom(zoomcore.zoom_at_frame(self.plan, 0))

    def onAVStarted(self):
        if self.wanted():
            if self.ident is None:
                self.prepare()
            self.begin()

    def onPlayBackResumed(self):
        self.begin()

    def onPlayBackSeek(self, time_ms, seek_offset):
        self.begin()

    def onPlayBackSeekChapter(self, chapter):
        self.begin()

    def onPlayBackSpeedChanged(self, speed):
        # fast forward and rewind break the clock mapping; settle again at normal speed
        if speed == 1:
            self.begin()
        else:
            self.halt()

    def onPlayBackPaused(self):
        self.halt()

    def onPlayBackStopped(self):
        self.end()

    def onPlayBackEnded(self):
        self.end()

    # --- which playbacks we handle -------------------------------------------------------------------------------
    def wanted(self):
        if not ADDON.getSettingBool("enabled"):
            return False
        try:
            if not self.isPlayingVideo():
                return False
        except RuntimeError:
            return False
        # live TV, recordings and stereoscopic 3D are not ours (3D plays on another player)
        return not any(
            xbmc.getCondVisibility(c)
            for c in ("Pvr.IsPlayingTv", "Pvr.IsPlayingRecording", "VideoPlayer.IsStereoscopic")
        )

    # --- the title and its plan ----------------------------------------------------------------------------------
    def prepare(self):
        """Identify the title, ask the analyzer, and turn what comes back into a plan."""
        try:
            playing = self.getPlayingFile()
        except RuntimeError:
            playing = ""
        # the played URL (it carries the Plex part id) and the library item's own path are both asked about
        self.ident = identify.identify_all(xbmc.getInfoLabel("Player.Filenameandpath"), playing)
        self.lookup = make_client().timeline(self.ident, ADDON.getSettingBool("request_analysis"))
        self.refresh_plan()
        log("title %s, analyzer %s%s" % (self.ident["kind"], self.lookup.status, ", plan ready" if self.plan else ""))

    def refresh_plan(self):
        timeline = self.lookup.timeline if self.lookup else None
        self.plan = None
        if timeline:
            try:
                self.plan = plan_mod.build_plan(
                    timeline, geometry(), ADDON.getSetting("edge_bias") or "no_spill", self.lookup.override
                )
            except (KeyError, ValueError, TypeError, IndexError) as exc:  # a timeline we cannot read: fall back
                log("timeline not usable: %r" % (exc,), xbmc.LOGWARNING)

    def reload(self):
        """The override changed (or the analyzer has news): look the title up again and carry on from here."""
        if self.ident is None:
            return
        self.lookup = make_client().timeline(self.ident, False)
        self.refresh_plan()
        self.begin()

    # --- scheduling ----------------------------------------------------------------------------------------------
    def begin(self):
        self.halt(keep_poller=True)
        if not ADDON.getSettingBool("enabled") or self.ident is None:
            return
        self.stop_flag.clear()
        if self.plan:
            self.stop_polling()
            self.worker = threading.Thread(target=self.work, name="cih-zoom", daemon=True)
            self.worker.start()
            return
        self.apply_fallback()
        self.start_polling()

    def halt(self, keep_poller=False):
        self.stop_flag.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(1.0)
        self.worker = None
        if not keep_poller:
            self.stop_polling()

    def end(self):
        self.halt()
        self.ident = self.lookup = self.plan = None
        set_zoom(1.0)
        if self.logfile:
            self.logfile.close()
            self.logfile = None

    def apply_fallback(self):
        """R6: no timeline. Contain the whole frame by its display ratio (never cuts picture)."""
        video = playing_video_size()
        for _ in range(20):  # the stream's size is not always readable the moment playback starts
            if video is not None or self.stop_flag.is_set():
                break
            time.sleep(0.1)
            video = playing_video_size()
        video = video or plan_mod.Video(1920, 1080)  # unreadable: assume 16:9, whose zoom never overhangs the screen
        zoom = plan_mod.full_frame_zoom(video, geometry())
        set_zoom(zoom)
        self.emit({"kind": "fallback", "zoom": zoom, "analyzer": self.lookup.status if self.lookup else None})

    def start_polling(self):
        if self.poller is not None or self.lookup is None or self.lookup.status in ("unknown",):
            return
        self.poll_stop.clear()
        self.poller = threading.Thread(target=self.poll, name="cih-poll", daemon=True)
        self.poller.start()

    def stop_polling(self):
        self.poll_stop.set()
        if self.poller and self.poller is not threading.current_thread():
            self.poller.join(0.5)
        self.poller = None

    def poll(self):
        """The title is not analyzed yet (or the analyzer is unreachable): ask again until it is, then switch over."""
        while not self.poll_stop.wait(max(POLL_FLOOR_S, ADDON.getSettingInt("poll_s"))):
            try:
                lookup = make_client().timeline(self.ident, False)
            except Exception as exc:  # noqa: BLE001 - never let a lookup end the thread
                log("poll failed: %r" % (exc,), xbmc.LOGWARNING)
                continue
            if lookup.timeline:
                self.lookup = lookup
                self.refresh_plan()
                if self.plan:
                    self.poller = None
                    self.begin()
                    return

    # --- worker --------------------------------------------------------------------------------------------------
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
            frame = zoomcore.frame_at(clock.player_now(), self.plan["fps"])
            now_zoom = zoomcore.zoom_at_frame(self.plan, frame)
            set_zoom(now_zoom)
            self.emit({"kind": "start", "frame": frame, "zoom": now_zoom, "lead_s": lead,
                       "offset_s": clock.offset(), "override": self.plan.get("override")})  # fmt: skip
            events = zoomcore.build_events(self.plan, lead, from_frame=frame + 1)
            num, den = zoomcore.fps_pair(self.plan["fps"])
            sent = zoomcore.run_events(events, clock, set_zoom, self.stop_flag.is_set,
                                       log=lambda r: self.emit(dict(r, kind="event")), frame_s=den / float(num))  # fmt: skip
            self.emit({"kind": "finished" if sent == len(events) else "interrupted", "sent": sent,
                       "total": len(events)})  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            log("worker failed: %r" % (exc,), xbmc.LOGERROR)


def open_start_addon(monitor):
    """Open the add-on named in the settings (PM4K) once Kodi has drawn its home screen, so opening Kodi opens PM4K.
    Runs once per Kodi start, when this service starts; does nothing if something is already playing."""
    addon_id = ADDON.getSetting("start_addon").strip()
    if not addon_id or not ADDON.getSettingBool("open_at_start"):
        return
    waited = 0.0
    while not xbmc.getCondVisibility("Window.IsActive(home)") and waited < 30 and not monitor.abortRequested():
        monitor.waitForAbort(0.5)
        waited += 0.5
    if monitor.waitForAbort(2.0) or xbmc.getCondVisibility("Player.HasMedia"):
        return
    log("opening %s (home screen after %.1f s)" % (addon_id, waited))
    xbmc.executebuiltin("RunScript(%s)" % addon_id)  # RunAddon would open PM4K as a (failing) plugin folder


def main():
    import xbmcgui

    monitor = xbmc.Monitor()
    player = ZoomPlayer()
    home = xbmcgui.Window(10000)
    log("started")
    open_start_addon(monitor)
    while not monitor.abortRequested():
        if monitor.waitForAbort(1.0):
            break
        if home.getProperty(RELOAD_PROPERTY):
            home.clearProperty(RELOAD_PROPERTY)
            player.reload()
    player.halt()
    log("stopped")


if __name__ == "__main__":
    main()
