"""Build Roboto fonts that also carry (black and white) emoji, for PM4K's text on the Shield.

Kodi draws text from the one font file a skin names, in a single colour, and has no fallback to the colour emoji font
Android uses; an emoji in a title (a collection called "Trending" with a flame in front) therefore shows as a box. This
merges Google's monochrome Noto Emoji into Roboto, so the Roboto look is kept and each emoji is drawn as a flat glyph in
the text colour.

  Roboto-Regular.ttf + Noto Emoji at weight 400 -> RobotoEmoji-Regular.ttf
  Roboto-Medium.ttf  + Noto Emoji at weight 500 -> RobotoEmoji-Medium.ttf

usage: python build_emoji_font.py <Roboto-Regular.ttf> <Roboto-Medium.ttf> <output folder> [--emoji-font NotoEmoji.ttf]
  Needs `pip install fonttools`. The Roboto files come from the Shield's /system/fonts (Apache 2.0). Noto Emoji (SIL
  OFL 1.1) is downloaded once from github.com/google/fonts into <output folder>/NotoEmoji[wght].ttf unless given.
  Where Roboto and Noto Emoji both have a character (digits, # and *), Roboto's is kept.
"""

import sys
import urllib.request
from pathlib import Path

EMOJI_URL = "https://github.com/google/fonts/raw/main/ofl/notoemoji/NotoEmoji%5Bwght%5D.ttf"
EMOJI_NAME = "NotoEmoji[wght].ttf"
DOWNLOAD_TIMEOUT_S = 60
PAIRS = (("Regular", 400), ("Medium", 500))  # Roboto weight name, Noto Emoji weight to match it


def fetch_emoji_font(folder: Path) -> Path:
    target = folder / EMOJI_NAME
    if not target.exists():
        print(f"downloading Noto Emoji to {target}")
        with urllib.request.urlopen(EMOJI_URL, timeout=DOWNLOAD_TIMEOUT_S) as response:
            data = response.read()
        if data[:4] != b"\x00\x01\x00\x00":
            raise SystemExit("the download is not a TrueType font")
        target.write_bytes(data)
    return target


def build(roboto: Path, emoji_font: Path, weight: int, out: Path) -> int:
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    emoji = instancer.instantiateVariableFont(TTFont(emoji_font), {"wght": weight})
    base = TTFont(roboto)
    if base["head"].unitsPerEm != emoji["head"].unitsPerEm:
        raise SystemExit(f"units per em differ: {base['head'].unitsPerEm} and {emoji['head'].unitsPerEm}")
    for table in ("vhea", "vmtx", "STAT", "gasp"):  # Roboto has none of these, and the merger cannot merge a table that only one font has
        if table in emoji:
            del emoji[table]
    temp = out.with_suffix(".emoji.tmp.ttf")
    emoji.save(temp)
    try:
        merged = Merger().merge([str(roboto), str(temp)])
    finally:
        temp.unlink(missing_ok=True)
    merged.save(out)
    return len(TTFont(out).getGlyphOrder())


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 3:
        print(__doc__)
        return 2
    regular, medium, outdir = Path(args[0]), Path(args[1]), Path(args[2])
    outdir.mkdir(parents=True, exist_ok=True)
    emoji_font = Path(argv[argv.index("--emoji-font") + 1]) if "--emoji-font" in argv else fetch_emoji_font(outdir)
    for (name, weight), source in zip(PAIRS, (regular, medium), strict=True):
        out = outdir / f"RobotoEmoji-{name}.ttf"
        glyphs = build(source, emoji_font, weight, out)
        print(f"{out.name}: {glyphs} glyphs, {out.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
