"""Manual override from the remote (spec R11): cycle the playing title through automatic, off and the ratio presets.

Run with RunScript(service.cih.zoom) from a keymap (see keymap.example.xml). Each press moves one step along
automatic -> off -> 1.33 -> 1.78 -> 1.85 -> 2.00 -> 2.35 -> 2.39 -> automatic, saves the step in the analyzer (so it is
kept for this title) and tells the service to reload.
"""

import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

import client as client_mod
import identify

CYCLE = ("auto", "off", 4 / 3, 16 / 9, 1.85, 2.00, 2.35, 2.39)
LABELS = {"auto": "Automatic", "off": "Off (no zoom)"}
RELOAD_PROPERTY = "service.cih.zoom.reload"


def current_step(override):
    if not override:
        return "auto"
    if override.get("mode") == "disable":
        return "off"
    preset = override.get("preset")
    return min(CYCLE[2:], key=lambda p: abs(p - float(preset))) if preset else "auto"


def next_step(step):
    return CYCLE[(CYCLE.index(step) + 1) % len(CYCLE)]


def label(step):
    return LABELS.get(step) or "%.2f:1" % step


def main():
    addon = xbmcaddon.Addon()
    dialog = xbmcgui.Dialog()
    if not xbmc.Player().isPlayingVideo():
        return
    ident = identify.identify_all(xbmc.getInfoLabel("Player.Filenameandpath"), xbmc.Player().getPlayingFile())
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    api = client_mod.AnalyzerClient(addon.getSetting("analyzer_url"), addon.getSetting("api_key"),
                                    os.path.join(profile, "cache"), timeout=2.0)  # fmt: skip
    found = api.timeline(ident, request_analysis=False)
    step = next_step(current_step(found.override))
    if step == "auto":
        done = api.set_override(ident, "clear")
    elif step == "off":
        done = api.set_override(ident, "disable")
    else:
        done = api.set_override(ident, "preset", step)
    if not done:
        dialog.notification("CIH zoom", "Could not reach the analyzer", xbmcgui.NOTIFICATION_ERROR, 3000)
        return
    xbmcgui.Window(10000).setProperty(RELOAD_PROPERTY, "1")
    dialog.notification("CIH zoom", label(step), xbmcgui.NOTIFICATION_INFO, 2000, False)


if __name__ == "__main__":
    main()
