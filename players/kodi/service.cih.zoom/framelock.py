"""Frame-locked zoom: the plan as a table the patched Kodi reads, so each zoom lands on its own frame.

Sending a zoom command shortly before a cut (zoomcore.run_events) lands on whichever frame Kodi happens to render
next, which wanders by about a frame because the command and the frame cycle are not locked together. The patched
Kodi (tools/kodi-build, patch 0003) instead looks the zoom up per frame, by the frame's own timestamp, in a table
this add-on writes: special://temp/cih_zoom.txt.

    CIH1 <serial>
    <seconds> <zoom>        one line per change, ascending; the zoom holds from that time on

Times are on the player's clock, as the analyzer's are (0 is the first frame). The patched Kodi writes
special://temp/cih_renderer.txt when it starts a playback, which is how this add-on knows the table will be read;
on a stock Kodi the file is absent and the add-on keeps sending commands.
"""

import zoomcore

TABLE = "special://temp/cih_zoom.txt"
MARKER = "special://temp/cih_renderer.txt"


def entries(plan, stride=1, shift_s=0.0):
    """[(seconds, zoom)] ascending, one per change of zoom, from the plan's initial zoom at the start to its last."""
    by_time = {0.0: zoomcore.initial_zoom(plan)}
    for event in zoomcore.build_events(plan, 0.0, from_frame=0, ramp_stride=stride):
        by_time[round(max(0.0, event["target"]), 6)] = float(event["zoom"])
    out = []
    for t in sorted(by_time):
        z = by_time[t]
        if out and abs(out[-1][1] - z) < 1e-9:
            continue  # no change: nothing to look up
        out.append((0.0 if not out else round(max(0.0, t + shift_s), 6), z))  # the initial zoom always starts at 0
    # a shift can push an entry before the one ahead of it only by rounding; keep the order strict
    ordered = []
    for t, z in out:
        if ordered and t <= ordered[-1][0]:
            t = ordered[-1][0] + 1e-6
        ordered.append((t, z))
    return ordered


def table_text(table, serial):
    return "CIH1 %s\n" % serial + "".join("%.6f %.6f\n" % (t, z) for t, z in table)
