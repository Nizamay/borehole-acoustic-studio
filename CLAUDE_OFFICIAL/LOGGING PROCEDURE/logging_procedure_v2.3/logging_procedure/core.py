"""
Core calculation and Y-axis compression (ported from LOGGING PROCEDURE CALCULATOR 2.3.html).
v2.3 additions:
  - format_ls_time(mins) helper
  - SEGMENT_KINDS constant
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

CONDITIONS: List[str] = [
    "Shut-in",
    "Flowing",
    "Transient",
    "Refilling",
    "Injection",
    "A-ann Bleed-off",
    "B-ann Bleed-off",
    "C-ann Bleed-off",
    "D-ann Bleed-off",
    "E-ann Bleed-off",
    "Pumping",
    "Circulation",
]

RUN_COLORS: List[str] = [
    "#e8a020",
    "#3db87a",
    "#3ba3d0",
    "#e05555",
    "#9b72e8",
    "#b91c1c",
    "#f472b6",
    "#facc15",
]

# Segment kind identifiers used by chart.py
SEGMENT_KINDS: List[str] = ["cont", "lst", "st"]

COND_RGB: Dict[str, Tuple[int, int, int]] = {
    "Shut-in": (200, 30, 50),
    "Flowing": (30, 80, 200),
    "Transient": (30, 160, 60),
    "Refilling": (160, 140, 30),
    "Injection": (100, 100, 100),
    "Pumping": (0, 180, 200),
    "Circulation": (200, 120, 20),
    "A-ann Bleed-off": (130, 40, 180),
    "B-ann Bleed-off": (130, 40, 180),
    "C-ann Bleed-off": (130, 40, 180),
    "D-ann Bleed-off": (130, 40, 180),
    "E-ann Bleed-off": (130, 40, 180),
}


def min_to_dhm(mins: float) -> str:
    total_min = int(round(mins))
    d = total_min // 1440
    h = (total_min % 1440) // 60
    m = total_min % 60
    return (f"{d}d " if d > 0 else "") + f"{h}h {m}m"


def format_ls_time(mins: float) -> str:
    """Format long station hold time.

    If the duration is a whole number of minutes (or rounds to >= 1 min),
    return e.g. "5.0 min".  Otherwise return the equivalent in seconds,
    e.g. "45 s".
    """
    secs = mins * 60.0
    if mins >= 1.0 or (mins > 0 and round(secs) % 60 == 0):
        # whole-minute display
        return f"{mins:.1f} min"
    else:
        return f"{round(secs):.0f} s"


@dataclass
class Segment:
    row: Dict[str, Any]
    top: float
    bottom: float
    interval: float
    seg_time: float
    num_st: int
    is_down: bool
    is_long_st: bool = False
    hold_depth: float = 0.0


@dataclass
class CalcResult:
    segments: List[Segment] = field(default_factory=list)
    total_time: float = 0.0
    total_stations: int = 0


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def calc_run(rows: List[Dict[str, Any]]) -> CalcResult:
    """Mirror of JS calcRun(run). Mutates each row dict with _seg_time (minutes)."""
    segments: List[Segment] = []
    total_time = 0.0
    total_stations = 0

    for row in rows:
        direction = str(row.get("direction", "down"))
        is_long_st = direction == "longst"

        if is_long_st:
            top_val = _f(row.get("top"))
            row["bottom"] = top_val
            hold_sec = _f(row.get("stDuration"), 45.0)
            seg_time = hold_sec / 60.0
            row["_seg_time"] = seg_time
            total_time += seg_time
            segments.append(
                Segment(
                    row=row,
                    top=top_val,
                    bottom=top_val,
                    interval=0.0,
                    seg_time=seg_time,
                    num_st=0,
                    is_down=False,
                    is_long_st=True,
                    hold_depth=top_val,
                )
            )
            continue

        top_str = "" if row.get("top") is None else str(row.get("top"))
        bot_str = "" if row.get("bottom") is None else str(row.get("bottom"))
        if top_str in ("", "undefined") or bot_str in ("", "undefined"):
            continue

        top = _f(row.get("top"))
        bottom = _f(row.get("bottom"))
        if top == bottom:
            continue

        interval = abs(bottom - top)
        is_down = direction in ("down", "rih")
        seg_time = 0.0
        num_st = 0

        if row.get("mode", "cont") == "cont":
            spd = _f(row.get("contSpeed"), 20.0)
            if spd <= 0:
                spd = 20.0
            seg_time = interval / spd
        else:
            st_int = _f(row.get("stInterval"), 30.0)
            st_dur = _f(row.get("stDuration"), 45.0)
            st_spd = _f(row.get("stSpeed"), 20.0)
            if st_int <= 0:
                st_int = 30.0
            if st_dur <= 0:
                st_dur = 45.0
            if st_spd <= 0:
                st_spd = 20.0
            num_st = int(interval // st_int)
            if num_st < 1:
                num_st = 1
            st_total_time = num_st * (st_dur / 60.0)
            move_total_time = interval / st_spd
            seg_time = st_total_time + move_total_time
            total_stations += num_st

        row["_seg_time"] = seg_time
        total_time += seg_time
        segments.append(
            Segment(
                row=row,
                top=top,
                bottom=bottom,
                interval=interval,
                seg_time=seg_time,
                num_st=num_st,
                is_down=is_down,
                is_long_st=False,
            )
        )

    return CalcResult(segments=segments, total_time=total_time, total_stations=total_stations)


@dataclass
class YAxisModel:
    """Compressed depth -> canvas Y (downward positive), same convention as JS."""

    compress: bool
    ty: Callable[[float], float]
    surface: float
    log_zone_min: float
    log_zone_max: float
    zones: List[Dict[str, Any]]
    dep_step: float
    transit_dep_step: float
    total_depth_span: float


def build_y_axis(
    segs: List[Segment],
    min_depth: float,
    max_depth: float,
    ph: float,
    pad_t: float,
    compress_y_enabled: bool,
) -> YAxisModel:
    surface = min(min_depth, 0.0)
    total_depth_span = max(max_depth - surface, 1e-9)

    log_segs = [s for s in segs if s.row.get("direction") not in ("rih", "pooh")]

    log_depths: List[float] = []
    for s in log_segs:
        log_depths.extend([s.top, s.bottom])
    log_zone_min = min(log_depths) if log_depths else max_depth
    log_zone_max = max(log_depths) if log_depths else max_depth

    breaks = [surface]
    for s in log_segs:
        lo = min(s.top, s.bottom)
        hi = max(s.top, s.bottom)
        breaks.extend([lo, hi])
    breaks.append(max_depth)
    breaks.sort()
    uniq: List[float] = []
    for v in breaks:
        if not uniq or v != uniq[-1]:
            uniq.append(v)
    breaks = uniq

    zones: List[Dict[str, Any]] = []
    compress_thresh = 0.20
    for bi in range(len(breaks) - 1):
        z_from = breaks[bi]
        z_to = breaks[bi + 1]
        z_span = z_to - z_from
        if z_span <= 0:
            continue

        def covers(zf: float, zt: float) -> bool:
            for s in log_segs:
                lo = min(s.top, s.bottom)
                hi = max(s.top, s.bottom)
                if lo <= zf and hi >= zt:
                    return True
            return False

        has_log = covers(z_from, z_to)
        is_gap = (not has_log) and (z_span / total_depth_span) > compress_thresh
        zones.append({"from": z_from, "to": z_to, "span": z_span, "gap": is_gap})

    num_gaps = sum(1 for z in zones if z["gap"])
    num_log_zones = sum(1 for z in zones if not z["gap"])
    compress_frac = 0.25 if num_log_zones <= 1 else 0.15

    total_gap_span = sum(z["span"] for z in zones if z["gap"])
    total_log_span = sum(z["span"] for z in zones if not z["gap"])
    total_gap_ph = num_gaps * compress_frac * ph
    total_log_ph = ph - total_gap_ph
    gap_ppd = total_gap_ph / total_gap_span if total_gap_span > 0 else 0.0
    log_ppd = total_log_ph / total_log_span if total_log_span > 0 else ph / total_depth_span

    multi_compress = any(z["gap"] for z in zones) and compress_y_enabled

    zone_pix_start: List[float] = []
    cum_px = 0.0
    for z in zones:
        zone_pix_start.append(cum_px)
        cum_px += z["span"] * (gap_ppd if z["gap"] else log_ppd)

    def ty(d: float) -> float:
        if not multi_compress:
            return pad_t + ((d - surface) / total_depth_span) * ph
        for zi, z in enumerate(zones):
            if d <= z["to"] or zi == len(zones) - 1:
                local_frac = (d - z["from"]) / (z["span"] or 1.0)
                px_in_zone = local_frac * z["span"] * (gap_ppd if z["gap"] else log_ppd)
                return pad_t + zone_pix_start[zi] + px_in_zone
        return pad_t + ph

    dep_range = max_depth - surface
    if dep_range <= 300:
        dep_step = 50.0
    elif dep_range <= 1000:
        dep_step = 100.0
    elif dep_range <= 3000:
        dep_step = 200.0
    else:
        dep_step = 500.0

    transit_span = log_zone_min - surface
    transit_dep_step = max(dep_step * 5, math.ceil(transit_span / 3 / 1000) * 1000)
    if transit_dep_step < 500:
        transit_dep_step = 500.0

    return YAxisModel(
        compress=multi_compress,
        ty=ty,
        surface=surface,
        log_zone_min=log_zone_min,
        log_zone_max=log_zone_max,
        zones=zones,
        dep_step=dep_step,
        transit_dep_step=transit_dep_step,
        total_depth_span=total_depth_span,
    )
