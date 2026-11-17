"""Make PM4K come back to the title page after a failed playback (lib/windows/videoplayer.py).

The problem: when Kodi cannot play something it opens its own "Playback Failed" dialog at the moment PM4K closes its video
window. Kodi refuses to activate the page that started the video while a dialog is open ("Activate of window '13007'
refused because there are active modal dialogs"), then ends up one window too far back. The title page is gone, the page
before it (an actor page, a library list) is the active window, but PM4K's own bookkeeping still has the title page open
above it, so Back and Up on the page that is showing do nothing useful.

The fix: videoplayer.play() remembers the page that asked for the video; when the video window closes while a dialog is
still open, it waits for the dialog to be closed and then activates that page again.

usage: python patch_videoplayer.py <script.plexmod folder> [--check]
  videoplayer.py keeps its original as videoplayer.py.cih-orig (once). Every replacement must match exactly once,
  otherwise the script stops. Running it again is harmless.
"""

import sys
from pathlib import Path

MARK = "cihRestoreWindow"

HELPER = (
    "def cihRestoreWindow(prev, command):\n"
    '    """CIH: after a failed playback Kodi shows its \'Playback Failed\' dialog while this window closes, refuses to go back to\n'
    "    the page that started the video and lands one window too far back. Wait for the dialog, then activate that page again.\"\"\"\n"
    "    try:\n"
    "        if command or not prev or prev < 13000 or xbmcgui.getCurrentWindowDialogId() <= 9999:\n"
    "            return\n"
    "        waited = 0\n"
    "        while xbmcgui.getCurrentWindowDialogId() > 9999 and waited < 1200 and not util.MONITOR.abortRequested():\n"
    "            util.MONITOR.waitForAbort(0.1)\n"
    "            waited += 1\n"
    "        util.MONITOR.waitForAbort(0.5)\n"
    "        current = xbmcgui.getCurrentWindowId()\n"
    "        util.DEBUG_LOG('VideoPlayer: dialog closed, window {0}, page to return to {1}', current, prev)\n"
    "        if current != prev:\n"
    "            xbmc.executebuiltin('ActivateWindow({0})'.format(prev))\n"
    "    except Exception:\n"
    "        util.ERROR()\n"
    "\n\n"
)

PLAY_OLD = (
    "def play(video=None, play_queue=None, resume=False, bgm=False, **kwargs):\n"
    "    try:\n"
    "        w = VideoPlayerWindow.open("
)
PLAY_NEW = (
    "def play(video=None, play_queue=None, resume=False, bgm=False, **kwargs):\n"
    "    prevWin = kodigui.BaseFunctions.lastWinID  # CIH: the page that asked for this video\n"
    "    try:\n"
    "        w = VideoPlayerWindow.open("
)
RET_OLD = "        del w\n        util.garbageCollect()\n        return command\n"
RET_NEW = "        del w\n        util.garbageCollect()\n        cihRestoreWindow(prevWin, command)  # CIH\n        return command\n"

# (old, new): the helper goes in front of play()
EDITS = [(PLAY_OLD, PLAY_NEW), (RET_OLD, RET_NEW)]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]) / "lib" / "windows" / "videoplayer.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    patched = MARK in text
    if "--check" in argv[1:]:
        print("patched" if patched else "not patched")
        return 0
    if patched:
        print("videoplayer.py already patched")
        return 0
    for old, _ in EDITS:
        if text.count(old) != 1:
            print("STOPPED: this edit no longer fits videoplayer.py")
            return 1
    for old, new in EDITS:
        text = text.replace(old, new)
    text = text.replace("def play(video=None", HELPER + "def play(video=None", 1)
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("patched videoplayer.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
