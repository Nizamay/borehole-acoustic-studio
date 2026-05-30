"""
Matplotlib chart: time (min) vs depth with optional Y compression.
v2.3 visual improvements:
  - Long Station: amber dashed hold line + grey dot-dash boundary verticals
  - Continuous segments: line uses run's colour
  - Station segments: dashed red hold lines + solid move lines
  - Callout boxes (ax.annotate) for narrow-segment labels
  - Run separator dashed verticals (multi-run view)
  - Rotated X pass-boundary labels
  - plot_all_runs() for combined multi-run chart
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.figure import Figure

from .core import COND_RGB, CalcResult, Segment, YAxisModel, build_y_axis, format_ls_time

# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------
_LS_AMBER = "#f59e0b"
_LS_BOUNDARY = "#999999"
_ST_HOLD_RED = "#dc2626"
_ST_MOVE_RED = "#b91c1c"
_SEP_GREY = (180 / 255, 180 / 255, 180 / 255, 0.30)

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _end_depth(seg: Segment, start_depth: float) -> float:
    if seg.is_long_st:
        return start_depth
    if seg.is_down:
        return max(seg.top, seg.bottom)
    return min(seg.top, seg.bottom)


def _initial_depth(segs: List[Segment]) -> float:
    if not segs:
        return 0.0
    s0 = segs[0]
    if s0.is_long_st:
        return float(s0.top)
    return float(min(s0.top, s0.bottom) if s0.is_down else max(s0.top, s0.bottom))


def _mpl_y(yaxis: YAxisModel, depth: float, pad_t: float, ph: float) -> float:
    """Canvas ty grows downward; matplotlib y grows upward — mirror around plot band."""
    tyv = yaxis.ty(depth)
    return pad_t + ph - (tyv - pad_t)


# ---------------------------------------------------------------------------
# Time-depth series
# ---------------------------------------------------------------------------

def build_time_depth_series(result: CalcResult) -> Tuple[List[float], List[float]]:
    """Piecewise (t, depth) path; stations use the same 10-step schematic as the canvas."""
    segs = result.segments
    t_all: List[float] = []
    d_all: List[float] = []
    if not segs:
        return t_all, d_all

    t_off = 0.0
    cur = _initial_depth(segs)
    t_all.append(t_off)
    d_all.append(cur)

    for seg in segs:
        if seg.is_long_st:
            t_all.extend([t_off, t_off + seg.seg_time])
            d_all.extend([cur, cur])
            t_off += seg.seg_time
            continue

        row = seg.row
        if row.get("mode", "cont") == "cont":
            end_d = _end_depth(seg, cur)
            t_all.extend([t_off, t_off + seg.seg_time])
            d_all.extend([cur, end_d])
            cur = end_d
            t_off += seg.seg_time
            continue

        end_d = _end_depth(seg, cur)
        interval = abs(end_d - cur)
        steps = 10
        st_dur_min = float(row.get("stDuration") or 45) / 60.0
        num_st = max(1, seg.num_st)
        st_spd = float(row.get("stSpeed") or 20)
        st_total_time = num_st * st_dur_min
        move_total_time = interval / st_spd if st_spd > 0 else 0.0
        total_seg_time = seg.seg_time
        each_step_dur = st_total_time / steps
        each_move_dur = move_total_time / steps
        each_depth_step = interval / steps if steps else interval
        depth_dir = 1 if seg.is_down else -1

        pts_lt = [0.0]
        pts_d = [cur]
        lt = 0.0
        d = cur
        for _ in range(steps):
            lt += each_step_dur
            pts_lt.append(lt)
            pts_d.append(d)
            d += depth_dir * each_depth_step
            lt += each_move_dur
            pts_lt.append(lt)
            pts_d.append(d)

        denom = pts_lt[-1] or 1.0
        scale = total_seg_time / denom
        for i in range(1, len(pts_lt)):
            t_all.append(t_off + pts_lt[i] * scale)
            d_all.append(pts_d[i])
        cur = end_d
        t_off += seg.seg_time

    return t_all, d_all


# ---------------------------------------------------------------------------
# Y-axis tick helpers
# ---------------------------------------------------------------------------

def _depth_ticks(
    yaxis: YAxisModel, min_depth: float, max_depth: float, pad_t: float, ph: float
) -> Tuple[List[float], List[str]]:
    positions: List[float] = []
    labels: List[str] = []
    surface = yaxis.surface

    if yaxis.compress and yaxis.zones:
        gap_step = yaxis.transit_dep_step or 2000.0
        for z in yaxis.zones:
            step = (
                max(gap_step, math.ceil(z["span"] / 2 / 1000) * 1000)
                if z["gap"]
                else yaxis.dep_step
            )
            d = math.ceil(z["from"] / step) * step
            while d <= z["to"]:
                positions.append(_mpl_y(yaxis, d, pad_t, ph))
                labels.append(str(int(round(d))))
                d += step
    else:
        step = yaxis.dep_step
        d = math.ceil(surface / step) * step
        while d <= max_depth:
            positions.append(_mpl_y(yaxis, d, pad_t, ph))
            labels.append(str(int(round(d))))
            d += step
    return positions, labels


# ---------------------------------------------------------------------------
# Segment drawing helpers
# ---------------------------------------------------------------------------

def _draw_long_station(
    ax,
    t0: float,
    t1: float,
    y_hold: float,
) -> None:
    """Amber dashed hold line + grey dot-dash vertical boundaries."""
    # Horizontal dashed amber hold line
    ax.plot(
        [t0, t1],
        [y_hold, y_hold],
        color=_LS_AMBER,
        linewidth=1.3,
        linestyle=(0, (6, 3)),
        solid_capstyle="round",
        zorder=3,
    )
    # Vertical grey dot-dash at start
    ax.axvline(
        t0,
        color=_LS_BOUNDARY,
        linewidth=0.8,
        linestyle=(0, (8, 3, 1, 3)),
        zorder=2,
    )
    # Vertical grey dot-dash at end
    ax.axvline(
        t1,
        color=_LS_BOUNDARY,
        linewidth=0.8,
        linestyle=(0, (8, 3, 1, 3)),
        zorder=2,
    )


def _draw_cont_segment(
    ax,
    t_pts: List[float],
    y_pts: List[float],
    run_color: str,
) -> None:
    """Continuous segment: diagonal line in run colour."""
    ax.plot(
        t_pts,
        y_pts,
        color=run_color,
        linewidth=1.8,
        solid_capstyle="round",
        zorder=4,
    )


def _draw_stat_segment(
    ax,
    t_pts: List[float],
    y_pts: List[float],
) -> None:
    """Station segment: draw stair pattern.
    Even pairs are horizontal holds (dashed red), odd pairs are moves (solid dark red).
    """
    if len(t_pts) < 2:
        return
    # Pair up consecutive points and classify by whether depth changes
    for i in range(len(t_pts) - 1):
        x0, x1 = t_pts[i], t_pts[i + 1]
        y0, y1 = y_pts[i], y_pts[i + 1]
        if abs(y1 - y0) < 0.5:
            # Horizontal hold
            ax.plot(
                [x0, x1], [y0, y1],
                color=_ST_HOLD_RED,
                linewidth=1.2,
                linestyle=(0, (4, 3)),
                solid_capstyle="round",
                zorder=4,
            )
        else:
            # Move line
            ax.plot(
                [x0, x1], [y0, y1],
                color=_ST_MOVE_RED,
                linewidth=1.5,
                solid_capstyle="round",
                zorder=4,
            )


# ---------------------------------------------------------------------------
# Callout / label helpers
# ---------------------------------------------------------------------------

def _seg_label_text(seg: Segment, units: str) -> List[str]:
    """Return list of text lines for the segment label."""
    du = "ft" if units == "emp" else "m"
    lines: List[str] = []
    if seg.is_long_st:
        lines.append(f"LS @ {seg.hold_depth:.0f}{du}")
        lines.append(format_ls_time(seg.seg_time))
    elif seg.row.get("mode", "cont") == "cont":
        spd = seg.row.get("contSpeed", 20)
        lines.append(f"CONT {spd}{du}/min")
        lines.append(f"{seg.seg_time:.1f} min")
    else:
        lines.append(f"STAT n={seg.num_st}")
        lines.append(f"{seg.seg_time:.1f} min")
    return lines


def _place_callouts(
    ax,
    label_infos: List[dict],
    total_time: float,
    pad_t: float,
    ph: float,
) -> None:
    """Draw callout annotations for narrow segments.

    Each item in label_infos:
      {t0, t1, y_mid, lines: List[str]}
    """
    # Track placed box centres for rudimentary anti-collision
    placed: List[Tuple[float, float]] = []

    y_top = pad_t + ph  # top of plot area in data coords (matplotlib y)
    y_margin = 0.06 * ph

    for info in label_infos:
        t0 = info["t0"]
        t1 = info["t1"]
        t_mid = (t0 + t1) / 2.0
        y_mid = info["y_mid"]
        lines = info["lines"]
        text = "\n".join(lines)

        # Place box: above-right unless near right edge
        x_box = t_mid + 0.5
        y_box = y_mid + y_margin * 1.5

        # Near-right-edge: shift left
        if t_mid > total_time * 0.75:
            x_box = t_mid - 0.5

        # Clamp y into plot area
        if y_box > y_top - y_margin:
            y_box = y_top - y_margin

        # Anti-collision: nudge if too close to an existing box
        for px, py in placed:
            if abs(x_box - px) < 2.0 and abs(y_box - py) < y_margin * 2:
                y_box -= y_margin * 2.2

        placed.append((x_box, y_box))

        ax.annotate(
            text,
            xy=(t_mid, y_mid),
            xytext=(x_box, y_box),
            fontsize=7.5,
            color="#374151",
            ha="center",
            va="bottom",
            bbox=dict(
                boxstyle="round,pad=0.35",
                facecolor="#fffbeb",
                edgecolor=_LS_AMBER,
                linewidth=0.9,
                alpha=0.92,
            ),
            arrowprops=dict(
                arrowstyle="->",
                color=_LS_AMBER,
                lw=0.9,
            ),
            zorder=8,
        )


# ---------------------------------------------------------------------------
# Single run chart
# ---------------------------------------------------------------------------

def plot_result(
    result: CalcResult,
    *,
    units: str,
    compress_y: bool,
    title: str,
    run_color: str = "#e8a020",
    figsize: tuple = (12.0, 6.0),
) -> Figure:
    """Draw a single-run depth-time chart with v2.3 visuals."""
    segs = result.segments
    fig, ax = plt.subplots(figsize=figsize, dpi=120, facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")

    if not segs or result.total_time <= 0:
        ax.text(
            0.5, 0.5,
            "No segments — add rows and Calculate",
            ha="center", va="center",
            transform=ax.transAxes,
        )
        ax.set_axis_off()
        return fig

    depths = [0.0]
    for s in segs:
        depths.extend([s.top, s.bottom])
    min_depth = min(depths)
    max_depth = max(depths)
    total_time = max(result.total_time, 1e-6)

    pad_l, pad_r, pad_t, pad_b = 76, 32, 72, 62
    h_px = figsize[1] * 120
    ph = h_px - pad_t - pad_b

    yaxis = build_y_axis(segs, min_depth, max_depth, ph, pad_t, compress_y)

    # ── Condition background spans ───────────────────────────────────────────
    t_acc = 0.0
    for seg in segs:
        t0, t1 = t_acc, t_acc + seg.seg_time
        cond = str(seg.row.get("condition", ""))
        rgb = COND_RGB.get(cond, (120, 120, 120))
        ax.axvspan(
            t0, t1,
            facecolor=(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255),
            alpha=0.14,
            linewidth=0,
            zorder=0,
        )
        t_acc = t1

    # ── Build time-depth series ──────────────────────────────────────────────
    t_series, d_series = build_time_depth_series(result)
    y_series = [_mpl_y(yaxis, d, pad_t, ph) for d in d_series]

    # ── Draw each segment individually with appropriate style ────────────────
    # Map time-series indices back to segment start positions
    t_acc = 0.0
    series_idx = 0  # current position in t_series / y_series

    # We need per-segment slices from the series.  Re-walk segments and slice.
    seg_slices: List[Tuple[List[float], List[float]]] = []
    # t_series[0] is always (0, initial_depth)
    pos = 0
    cur_depth = _initial_depth(segs)
    t_series_by_seg: List[Tuple[List[float], List[float]]] = []
    t_cursor = 0.0
    idx = 1  # skip leading [0] point; we'll prepend manually

    for seg in segs:
        seg_t: List[float] = [t_cursor]
        seg_d: List[float] = [cur_depth]

        if seg.is_long_st:
            seg_t.append(t_cursor + seg.seg_time)
            seg_d.append(cur_depth)
            idx += 2
        elif seg.row.get("mode", "cont") == "cont":
            seg_t.append(t_cursor + seg.seg_time)
            end_d = _end_depth(seg, cur_depth)
            seg_d.append(end_d)
            cur_depth = end_d
            idx += 2
        else:
            steps_pairs = 2 * 10  # 10 station steps, each 2 points
            end_d = _end_depth(seg, cur_depth)
            # Pull from t_series/y_series
            for _ in range(steps_pairs):
                if idx < len(t_series):
                    seg_t.append(t_series[idx])
                    seg_d.append(d_series[idx])
                    idx += 1
            # Make sure y series are converted
            cur_depth = end_d

        t_cursor += seg.seg_time
        t_series_by_seg.append((seg_t, seg_d))

    # Now draw per-segment
    narrow_callouts: List[dict] = []
    CALLOUT_PX_THRESH = 55.0  # pixels wide below which we use a callout
    # Estimate pixels-per-minute
    # figsize[0] is inches, dpi=120 → width_px, effective plot width ≈ width_px * 0.86
    plot_width_px = figsize[0] * 120 * 0.86

    t_acc = 0.0
    for seg, (seg_t, seg_d) in zip(segs, t_series_by_seg):
        t0 = t_acc
        t1 = t_acc + seg.seg_time
        seg_y = [_mpl_y(yaxis, d, pad_t, ph) for d in seg_d]

        if seg.is_long_st:
            hold_y = _mpl_y(yaxis, seg.hold_depth, pad_t, ph)
            _draw_long_station(ax, t0, t1, hold_y)
        elif seg.row.get("mode", "cont") == "cont":
            _draw_cont_segment(ax, seg_t, seg_y, run_color)
        else:
            _draw_stat_segment(ax, seg_t, seg_y)

        # Label: inline if wide enough, else callout
        chord_px = (seg.seg_time / total_time) * plot_width_px
        y_mid = (seg_y[0] + seg_y[-1]) / 2.0
        t_mid = (t0 + t1) / 2.0

        if chord_px >= 55:
            label_lines = _seg_label_text(seg, units)
            ax.text(
                t_mid, y_mid,
                "\n".join(label_lines),
                ha="center", va="center",
                fontsize=7.5,
                color="#1e293b",
                alpha=0.85,
                zorder=7,
            )
        else:
            narrow_callouts.append({
                "t0": t0,
                "t1": t1,
                "y_mid": y_mid,
                "lines": _seg_label_text(seg, units),
            })

        t_acc = t1

    # Draw callouts for narrow segments
    if narrow_callouts:
        _place_callouts(ax, narrow_callouts, total_time, pad_t, ph)

    # ── Axes setup ───────────────────────────────────────────────────────────
    ax.set_xlim(0, total_time)
    yticks, yticklab = _depth_ticks(yaxis, min_depth, max_depth, pad_t, ph)
    if yticks:
        ax.set_yticks(yticks)
        ax.set_yticklabels(yticklab, fontsize=9, color="#b91c1c")
    ax.set_ylim(pad_t, pad_t + ph)

    du = "ft" if units == "emp" else "m"
    ax.set_xlabel("TIME (min)", fontsize=11, color="#475569")
    ax.set_ylabel(
        f"DEPTH ({du}) — compressed transit zones" if yaxis.compress else f"DEPTH ({du})",
        fontsize=10,
        color="#475569",
    )
    ax.set_title(title, fontsize=13, fontweight="bold", color="#991b1b", pad=12)

    t_step = max(1, int(math.ceil(total_time / 9 / 5) * 5))
    xt = list(range(0, int(math.ceil(total_time)) + 1, t_step))
    if not xt or xt[-1] < total_time:
        xt.append(int(round(total_time)))
    ax.set_xticks(xt)
    ax.set_xticklabels([str(v) for v in xt])

    # Rotated X-axis pass-boundary labels at segment boundaries
    boundary_times: List[float] = []
    t_acc = 0.0
    for seg in segs:
        t_acc += seg.seg_time
        boundary_times.append(t_acc)
    # Draw as rotated text artists on a secondary x axis layer
    y_label_row = pad_t - 14  # just below top of plot
    for bt in boundary_times[:-1]:  # skip last (== total_time, already on axis)
        ax.text(
            bt, pad_t - 10,
            f"{bt:.1f}",
            ha="center", va="top",
            fontsize=7.0,
            color="#94a3b8",
            rotation=-45,
            rotation_mode="anchor",
            clip_on=False,
            zorder=9,
        )

    ax.grid(True, axis="x", color="#cfd8e6", linewidth=0.8)
    ax.grid(True, axis="y", color="#e2e8f0", linewidth=0.6, linestyle=":")
    ax.tick_params(axis="x", colors="#475569")

    stats = (
        f"Segments: {len(segs)}  |  "
        f"Time: {result.total_time:.1f} min  |  "
        f"Stations: {result.total_stations}"
    )
    fig.text(0.5, 0.02, stats, ha="center", fontsize=9, color="#64748b", family="monospace")
    fig.subplots_adjust(left=0.10, right=0.96, top=0.88, bottom=0.12)
    return fig


# ---------------------------------------------------------------------------
# Multi-run combined chart
# ---------------------------------------------------------------------------

def plot_all_runs(
    results: list,          # list of CalcResult
    run_names: list,        # list of str
    run_colors: list,       # list of str
    *,
    units: str,
    compress_y: bool,
    title: str,
    figsize: tuple = (14.0, 6.0),
) -> Figure:
    """Draw all runs on one chart with time-axis concatenated and run separators."""

    valid = [(r, n, c) for r, n, c in zip(results, run_names, run_colors) if r and r.segments]
    if not valid:
        fig, ax = plt.subplots(figsize=figsize, dpi=120, facecolor="#f8fafc")
        ax.set_facecolor("#f8fafc")
        ax.text(0.5, 0.5, "No run data — Calculate each run first.",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return fig

    # Global depth extents across all runs
    all_depths = [0.0]
    for r, _, _ in valid:
        for s in r.segments:
            all_depths.extend([s.top, s.bottom])
    min_depth = min(all_depths)
    max_depth = max(all_depths)

    all_segs = [s for r, _, _ in valid for s in r.segments]
    total_time = sum(r.total_time for r, _, _ in valid)
    total_time = max(total_time, 1e-6)

    fig, ax = plt.subplots(figsize=figsize, dpi=120, facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")

    pad_l, pad_r, pad_t, pad_b = 76, 32, 72, 62
    h_px = figsize[1] * 120
    ph = h_px - pad_t - pad_b

    # Build a shared Y axis using all segments
    yaxis = build_y_axis(all_segs, min_depth, max_depth, ph, pad_t, compress_y)

    # ── Draw condition spans ─────────────────────────────────────────────────
    t_global = 0.0
    for r, _, _ in valid:
        for seg in r.segments:
            t0, t1 = t_global, t_global + seg.seg_time
            cond = str(seg.row.get("condition", ""))
            rgb = COND_RGB.get(cond, (120, 120, 120))
            ax.axvspan(
                t0, t1,
                facecolor=(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255),
                alpha=0.14,
                linewidth=0,
                zorder=0,
            )
            t_global += seg.seg_time

    # ── Draw each run, time-shifted ──────────────────────────────────────────
    t_run_start = 0.0
    run_separators: List[float] = []
    plot_width_px = figsize[0] * 120 * 0.86
    narrow_callouts: List[dict] = []

    for run_idx, (r, name, color) in enumerate(valid):
        segs = r.segments
        run_total = r.total_time

        # Rebuild per-segment slices offset by t_run_start
        t_series, d_series = build_time_depth_series(r)
        # Shift time
        t_series_shifted = [t + t_run_start for t in t_series]

        # Draw segments
        t_acc = t_run_start
        cur_depth = _initial_depth(segs)
        idx = 1

        for seg in segs:
            t0 = t_acc
            t1 = t_acc + seg.seg_time

            # Gather segment points from shifted series
            seg_t: List[float] = [t0]
            seg_d_raw: List[float] = [cur_depth]

            if seg.is_long_st:
                seg_t.append(t1)
                seg_d_raw.append(cur_depth)
                idx += 2
            elif seg.row.get("mode", "cont") == "cont":
                end_d = _end_depth(seg, cur_depth)
                seg_t.append(t1)
                seg_d_raw.append(end_d)
                cur_depth = end_d
                idx += 2
            else:
                end_d = _end_depth(seg, cur_depth)
                for _ in range(2 * 10):
                    if idx < len(t_series_shifted):
                        seg_t.append(t_series_shifted[idx])
                        seg_d_raw.append(d_series[idx])
                        idx += 1
                cur_depth = end_d

            seg_y = [_mpl_y(yaxis, d, pad_t, ph) for d in seg_d_raw]

            if seg.is_long_st:
                hold_y = _mpl_y(yaxis, seg.hold_depth, pad_t, ph)
                _draw_long_station(ax, t0, t1, hold_y)
            elif seg.row.get("mode", "cont") == "cont":
                _draw_cont_segment(ax, seg_t, seg_y, color)
            else:
                _draw_stat_segment(ax, seg_t, seg_y)

            # Labels
            chord_px = (seg.seg_time / total_time) * plot_width_px
            y_mid = (seg_y[0] + seg_y[-1]) / 2.0
            t_mid = (t0 + t1) / 2.0

            if chord_px >= 55:
                ax.text(
                    t_mid, y_mid,
                    "\n".join(_seg_label_text(seg, units)),
                    ha="center", va="center",
                    fontsize=7,
                    color="#1e293b",
                    alpha=0.80,
                    zorder=7,
                )
            else:
                narrow_callouts.append({
                    "t0": t0,
                    "t1": t1,
                    "y_mid": y_mid,
                    "lines": _seg_label_text(seg, units),
                })

            t_acc = t1

        # Run separator (after all but last run)
        run_end = t_run_start + run_total
        if run_idx < len(valid) - 1:
            run_separators.append(run_end)

        # Run name label
        ax.text(
            t_run_start + run_total / 2,
            pad_t + ph + 4,
            name,
            ha="center", va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color=color,
            zorder=8,
            clip_on=False,
        )

        t_run_start = run_end

    # Draw run separator lines
    for sep_t in run_separators:
        ax.axvline(
            sep_t,
            color=_SEP_GREY[:3],
            alpha=_SEP_GREY[3],
            linewidth=1.05,
            linestyle=(0, (6, 4)),
            zorder=5,
        )

    # Draw callouts
    if narrow_callouts:
        _place_callouts(ax, narrow_callouts, total_time, pad_t, ph)

    # ── Axes ─────────────────────────────────────────────────────────────────
    ax.set_xlim(0, total_time)
    yticks, yticklab = _depth_ticks(yaxis, min_depth, max_depth, pad_t, ph)
    if yticks:
        ax.set_yticks(yticks)
        ax.set_yticklabels(yticklab, fontsize=9, color="#b91c1c")
    ax.set_ylim(pad_t, pad_t + ph)

    du = "ft" if units == "emp" else "m"
    ax.set_xlabel("TIME (min)", fontsize=11, color="#475569")
    ax.set_ylabel(
        f"DEPTH ({du}) — compressed transit zones" if yaxis.compress else f"DEPTH ({du})",
        fontsize=10,
        color="#475569",
    )
    ax.set_title(title, fontsize=13, fontweight="bold", color="#991b1b", pad=12)

    t_step = max(1, int(math.ceil(total_time / 10 / 5) * 5))
    xt = list(range(0, int(math.ceil(total_time)) + 1, t_step))
    if not xt or xt[-1] < total_time:
        xt.append(int(round(total_time)))
    ax.set_xticks(xt)
    ax.set_xticklabels([str(v) for v in xt])

    # Rotated X-axis pass-boundary marks
    boundary_times_all: List[float] = []
    t_acc = 0.0
    for r, _, _ in valid:
        for seg in r.segments:
            t_acc += seg.seg_time
            boundary_times_all.append(t_acc)
    for bt in boundary_times_all[:-1]:
        ax.text(
            bt, pad_t - 10,
            f"{bt:.1f}",
            ha="center", va="top",
            fontsize=6.5,
            color="#94a3b8",
            rotation=-45,
            rotation_mode="anchor",
            clip_on=False,
            zorder=9,
        )

    ax.grid(True, axis="x", color="#cfd8e6", linewidth=0.8)
    ax.grid(True, axis="y", color="#e2e8f0", linewidth=0.6, linestyle=":")
    ax.tick_params(axis="x", colors="#475569")

    # Legend patches for run colours
    legend_patches = [
        mpatches.Patch(color=c, label=n, alpha=0.85)
        for _, n, c in valid
    ]
    ax.legend(
        handles=legend_patches,
        loc="upper right",
        fontsize=8,
        framealpha=0.85,
        edgecolor="#c8d0dc",
    )

    total_stations = sum(r.total_stations for r, _, _ in valid)
    total_segs = sum(len(r.segments) for r, _, _ in valid)
    stats = (
        f"Runs: {len(valid)}  |  "
        f"Segments: {total_segs}  |  "
        f"Total time: {total_time:.1f} min  |  "
        f"Stations: {total_stations}"
    )
    fig.text(0.5, 0.02, stats, ha="center", fontsize=9, color="#64748b", family="monospace")
    fig.subplots_adjust(left=0.10, right=0.96, top=0.88, bottom=0.12)
    return fig
