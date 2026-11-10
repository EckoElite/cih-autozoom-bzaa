"""Put every CIH change to PM4K back in one go (after a theme change, a PM4K update, or anything that re-renders it).

PM4K writes its screens from templates whenever the theme changes or it is updated, which silently replaces the patched
files. This pulls the add-on's skin folder and the ScopeNox Font.xml from the Shield, patches them again from the PM4K
originals (restoring them first where a file is already patched, so the result never depends on how often it ran), and
pushes the result back. It never touches anything outside script.plexmod and the skin's font files.

usage: python reapply.py [--no-push] [--workdir DIR]
  The Shield is taken from ../../.env (SHIELD_IP, SHIELD_ADB_PORT). Stop Kodi first (the script does, with
  `am force-stop`) and start it again afterwards; PM4K takes about a minute to appear.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KODI = "/sdcard/Android/data/org.xbmc.kodi.cih/files/.kodi/addons"
PM4K = f"{KODI}/script.plexmod"
SKIN_FONTS = f"{KODI}/skin.scope.nox.omega"
SCALE_MARKER = "cih-scale-ui"
FONT_FILES = ("RobotoEmoji-Regular.ttf", "RobotoEmoji-Medium.ttf")  # needs `pip install fonttools` on the PC to build
OSD_FILE = "script-plex-seek_dialog.xml"


TARGET: list[str] = []  # ["-s", "<ip>:<port>"] once connected: an emulator may be attached too, and adb refuses to guess


def adb(*args: str, check: bool = True) -> str:
    prefix = TARGET if args and args[0] != "connect" else []
    done = subprocess.run(["adb", *prefix, *args], capture_output=True, text=True, check=False, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    if check and done.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def run(script: str, *args: str) -> str:
    done = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"{script} failed:\n{done.stdout}\n{done.stderr}")
    return done.stdout


def connect() -> None:
    env = {}
    for line in (HERE.parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    address = f"{env['SHIELD_IP']}:{env.get('SHIELD_ADB_PORT', '5555')}"
    adb("connect", address)
    TARGET[:] = ["-s", address]


def restore_originals(skin: Path) -> int:
    """Put PM4K's own copy back wherever a file was patched; forget stale backups where PM4K has written a new one."""
    restored = 0
    osd_patched = int(run("apply_osd_margin.py", str(skin), "--check").split()[0]) > 0  # "N of 15 edits already in place"
    for path in sorted(skin.glob("script-plex-*.xml")):
        backup = path.with_name(path.name + ".cih-orig")
        text = path.read_text(encoding="utf-8")
        patched = SCALE_MARKER in text or "<font>plex" in text or (path.name == OSD_FILE and osd_patched)
        if patched and backup.exists():
            shutil.copyfile(backup, path)
            restored += 1
        elif not patched and backup.exists():
            backup.unlink()  # PM4K re-rendered this screen: the old backup is of an older render
    return restored


def push_fonts(work: Path) -> None:
    """Put the Roboto + Noto Emoji files (see build_emoji_font.py) in the skin's fonts folder if they are not there yet."""
    missing = []
    for name in FONT_FILES:
        have = adb("shell", "ls", f"{SKIN_FONTS}/fonts/{name}", check=False)
        if name not in have or "No such file" in have:
            missing.append(name)
    if not missing:
        return
    sources = []
    for name in ("Roboto-Regular.ttf", "Roboto-Medium.ttf"):  # the Shield's own, from /system/fonts
        local = work / name
        adb("pull", f"/system/fonts/{name}", str(local))
        sources.append(str(local))
    print(run("build_emoji_font.py", *sources, str(work / "emoji-fonts")).strip())
    for name in missing:
        adb("push", str(work / "emoji-fonts" / name), f"{SKIN_FONTS}/fonts/{name}")


def main(argv: list[str]) -> int:
    push = "--no-push" not in argv
    work = Path(argv[argv.index("--workdir") + 1]) if "--workdir" in argv else Path(tempfile.mkdtemp(prefix="pm4k-"))
    work.mkdir(parents=True, exist_ok=True)
    connect()
    skin, fonts = work / "1080i", work / "Font.xml"
    shutil.rmtree(skin, ignore_errors=True)
    adb("pull", f"{PM4K}/resources/skins/Main/1080i", str(skin))
    adb("pull", f"{SKIN_FONTS}/scopeformat/Font.xml", str(fonts))
    if (work / "Font.xml.cih-bak").exists():
        shutil.copyfile(work / "Font.xml.cih-bak", fonts)  # start from the skin's own, then add again
    plex = work / "script.plexmod"
    (plex / "lib" / "windows").mkdir(parents=True, exist_ok=True)
    adb("pull", f"{PM4K}/lib/windows/dropdown.py", str(plex / "lib" / "windows" / "dropdown.py"))
    dropdown_orig = plex / "lib" / "windows" / "dropdown.py.cih-orig"
    adb("pull", f"{PM4K}/lib/windows/dropdown.py.cih-orig", str(dropdown_orig), check=False)

    print(f"restored {restore_originals(skin)} patched screens from their originals")
    print(run("scale_ui.py", str(skin)).strip().splitlines()[-1])
    print(run("apply_osd_margin.py", str(skin)).strip().splitlines()[-1])
    print(run("apply_font.py", str(skin), str(fonts)).strip().splitlines()[0])
    print(run("patch_dropdown.py", str(plex)).strip().splitlines()[-1])
    if not push:
        print(f"patched copy left in {work}")
        return 0

    adb("shell", "am", "force-stop", "org.xbmc.kodi.cih")
    adb("push", f"{skin}/.", f"{PM4K}/resources/skins/Main/1080i/")
    adb("push", str(fonts), f"{SKIN_FONTS}/scopeformat/Font.xml")
    if (work / "Font.xml.cih-bak").exists():
        adb("push", str(work / "Font.xml.cih-bak"), f"{SKIN_FONTS}/scopeformat/Font.xml.cih-bak")
    adb("push", str(plex / "lib" / "windows" / "dropdown.py"), f"{PM4K}/lib/windows/dropdown.py")
    push_fonts(work)
    print("pushed; start Kodi (adb shell am start -n org.xbmc.kodi.cih/.Splash)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
