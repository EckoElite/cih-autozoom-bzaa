"""What one analysed episode says about the rest of its season (the `season_hint` of an unanalysed episode's answer).

A season's episodes are nearly always mastered alike, so a player that has no timeline for an episode yet can use the
picture ratio of the season's sample instead of the plain fit. The hint is the sample's ratios by share of its running
time, longest first; `preset` and `aspect` are those of the longest one.
"""

from __future__ import annotations

import json
from typing import Any

SIGNIFICANT_SHARE = 0.03  # a ratio with less of the running time than this does not make a season variable
PRESET_DIGITS = 2


def _round_ratio(value: float | None) -> float | None:
    return None if value is None else round(float(value), PRESET_DIGITS)


def ratio_shares(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """The sample's ratios, longest first: `{"preset": 1.78 or None, "aspect": 1.777, "share": 0.99}` with the share of
    the running time covered by segments. A picture that matches no preset is told apart by its aspect, to two
    decimals."""
    totals: dict[tuple[float | None, float | None], list[float]] = {}  # key -> [seconds, aspect x seconds]
    for segment in timeline.get("segments") or []:
        seconds = float(segment["end"]) - float(segment["start"])
        if seconds <= 0:
            continue
        preset = _round_ratio(segment.get("preset"))
        aspect = float(segment["aspect"])
        key = (preset, None if preset is not None else _round_ratio(aspect))
        entry = totals.setdefault(key, [0.0, 0.0])
        entry[0] += seconds
        entry[1] += aspect * seconds
    whole = sum(v[0] for v in totals.values())
    if whole <= 0:
        return []
    ratios = [
        {"preset": key[0], "aspect": round(v[1] / v[0], 3), "share": round(v[0] / whole, 3)}
        for key, v in totals.items()
    ]
    ratios.sort(key=lambda r: (-r["share"], r["aspect"]))
    return ratios


def season_hint(sample: dict[str, Any]) -> dict[str, Any] | None:
    """The hint built from a season's finished episode (a store row with `timeline`, `rating_key`, `season`,
    `episode`), or None when its timeline says nothing usable."""
    try:
        timeline = json.loads(sample["timeline"])
        ratios = ratio_shares(timeline)
    except (TypeError, ValueError, KeyError):
        return None
    if not ratios:
        return None
    main = ratios[0]
    return {
        "preset": main["preset"],
        "aspect": main["aspect"],
        "ratios": ratios,
        "variable": sum(1 for r in ratios if r["share"] >= SIGNIFICANT_SHARE) > 1,
        "from": {
            "rating_key": sample.get("rating_key"),
            "episode": sample.get("episode"),
            "season": sample.get("season"),
        },
    }
