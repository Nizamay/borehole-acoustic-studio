# TFM 2.0 — Temperature Field Map
# Python Dash re-implementation of TFM v2.0.html
# Author: N. Ikhsanov · Halliburton

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update, ALL, MATCH
import dash_bootstrap_components as dbc

try:
    import lasio
    HAS_LASIO = True
except ImportError:
    HAS_LASIO = False

try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────
DARK_BG = "#0b0e14"
DARK_PANEL = "#111520"
DARK_CARD = "#161d2e"
DARK_BORDER = "#1f2d45"
DARK_MUTED = "#2a3d5c"
DARK_TEXT = "#d8e4f0"
DARK_SUB = "#5a7599"
DARK_ACCENT = "#3b9ede"
DARK_RED = "#c0392b"
DARK_GOLD = "#e8b84b"
DARK_GREEN = "#27ae60"
DARK_ORANGE = "#e67e22"

PLOT_LAYOUT_BASE = dict(
    paper_bgcolor=DARK_PANEL,
    plot_bgcolor=DARK_BG,
    font=dict(color=DARK_TEXT, family="IBM Plex Mono", size=9),
    legend=dict(bgcolor="rgba(17,21,32,0.85)", bordercolor=DARK_BORDER,
                borderwidth=1, font=dict(size=9)),
)

AXIS_DARK = dict(
    color=DARK_SUB,
    gridcolor=DARK_BORDER,
    zerolinecolor=DARK_BORDER,
    tickfont=dict(size=9, color=DARK_SUB),
    title_font=dict(size=9, color=DARK_SUB),
)

WELL_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c",
    "#e67e22", "#c0392b", "#27ae60", "#2980b9", "#8e44ad", "#16a085",
    "#f1c40f", "#d35400", "#e91e63", "#00bcd4", "#8bc34a", "#ff5722",
]

TP_CMP_COLORS = [
    "#e74c3c", "#f39c12", "#9b59b6", "#1abc9c",
    "#e67e22", "#e91e63", "#ff5722", "#00bcd4",
]

TP_COMBOS = [
    {"t": "Producer", "f": "gas",   "lbl": "Gas Producer",    "col": "#e74c3c"},
    {"t": "Producer", "f": "oil",   "lbl": "Oil Producer",    "col": "#4caf6e"},
    {"t": "Producer", "f": "water", "lbl": "Water Producer",  "col": "#3b9ede"},
    {"t": "Producer", "f": "",      "lbl": "Producer",        "col": "#8bc34a"},
    {"t": "Injector", "f": "gas",   "lbl": "Gas Injector",    "col": "#ff5722"},
    {"t": "Injector", "f": "oil",   "lbl": "Oil Injector",    "col": "#1abc9c"},
    {"t": "Injector", "f": "water", "lbl": "Water Injector",  "col": "#9b59b6"},
    {"t": "Injector", "f": "",      "lbl": "Injector",        "col": "#673ab7"},
    {"t": "Observer", "f": "",      "lbl": "Observer",        "col": "#e8b84b"},
    {"t": "Observer", "f": "gas",   "lbl": "Observer (gas)",  "col": "#f39c12"},
    {"t": "Observer", "f": "oil",   "lbl": "Observer (oil)",  "col": "#e67e22"},
    {"t": "Observer", "f": "water", "lbl": "Observer (water)","col": "#ff9800"},
    {"t": "",         "f": "",      "lbl": "Unknown",         "col": "#7a8fa8"},
]

MAX_DEPTH_PTS = 400  # downsample LAS curves to this many points

# ─────────────────────────────────────────────────────────────────
#  HELPERS — DATA PROCESSING
# ─────────────────────────────────────────────────────────────────

def _find_curve(las, candidates: list[str]) -> np.ndarray | None:
    """Return first curve array matching any candidate name (case-insensitive prefix)."""
    upper = {c.upper(): c for c in las.keys()}
    for cand in candidates:
        cu = cand.upper()
        if cu in upper:
            arr = las[upper[cu]]
            arr = np.where(arr < -9000, np.nan, arr)
            return arr
        # prefix match
        for key_u, key in upper.items():
            if key_u.startswith(cu) and key_u != "DEPT" and key_u != "DEPTH":
                arr = las[key]
                arr = np.where(arr < -9000, np.nan, arr)
                return arr
    return None


def parse_las_bytes(filename: str, content_bytes: bytes) -> dict | None:
    """Parse a LAS file from bytes. Returns well dict or None on failure."""
    if not HAS_LASIO:
        return None
    try:
        las = lasio.read(io.StringIO(content_bytes.decode("utf-8", errors="replace")))
    except Exception:
        return None

    dept = las.index  # depth array

    temp = _find_curve(las, ["TEMP", "T", "TMPF", "TMP", "TEMPC"])
    pres = _find_curve(las, ["PRES", "PNOR", "PORE", "PP"])
    gr   = _find_curve(las, ["GR", "GR_EC", "SGR", "CGR"])

    has_temp = temp is not None and np.any(np.isfinite(temp))
    has_pres = pres is not None and np.any(np.isfinite(pres))
    has_gr   = gr   is not None and np.any(np.isfinite(gr))

    # Downsample to MAX_DEPTH_PTS
    n = len(dept)
    if n > MAX_DEPTH_PTS:
        idx = np.round(np.linspace(0, n - 1, MAX_DEPTH_PTS)).astype(int)
    else:
        idx = np.arange(n)

    def _clean(arr):
        if arr is None:
            return [None] * len(idx)
        out = arr[idx].tolist()
        return [v if (v is not None and np.isfinite(v)) else None for v in out]

    dept_s = dept[idx].tolist()

    well_name = (
        getattr(las.well, "WELL", None) and las.well.WELL.value
        or getattr(las.well, "well", None) and las.well.well.value
        or filename.rsplit(".", 1)[0]
    )
    field_name = ""
    try:
        field_name = las.well.FLD.value or ""
    except Exception:
        pass
    date_str = ""
    try:
        date_str = las.well.DATE.value or ""
    except Exception:
        pass
    dept_unit = ""
    try:
        dept_unit = str(las.curves[0].unit).lower()
        if "ft" in dept_unit:
            dept_unit = "ft"
        else:
            dept_unit = "m"
    except Exception:
        dept_unit = "m"

    return {
        "well": str(well_name).strip(),
        "field": str(field_name).strip(),
        "date": str(date_str).strip(),
        "strt": round(float(las.well.STRT.value), 1) if hasattr(las.well, "STRT") else (dept_s[0] if dept_s else 0),
        "stop": round(float(las.well.STOP.value), 1) if hasattr(las.well, "STOP") else (dept_s[-1] if dept_s else 0),
        "dept": dept_s,
        "temp": _clean(temp) if has_temp else [],
        "pres": _clean(pres) if has_pres else [],
        "gr":   _clean(gr)   if has_gr   else [],
        "has_temp": has_temp,
        "has_pres": has_pres,
        "has_gr": has_gr,
        "dept_unit": dept_unit,
    }


def parse_coords_bytes(filename: str, content_bytes: bytes) -> dict:
    """Parse Excel/CSV coordinate file. Returns {well_name: {lon, lat}}."""
    coords = {}
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content_bytes))
        else:
            df = pd.read_excel(io.BytesIO(content_bytes))
        df.columns = [str(c).strip() for c in df.columns]
        cols_u = {c.upper(): c for c in df.columns}

        # Find well name column
        well_col = None
        for cand in ["WELL", "WELLBORE", "WELL NAME", "WELLNAME", "NAME", "WELL_NAME"]:
            if cand in cols_u:
                well_col = cols_u[cand]
                break
        if well_col is None and len(df.columns) >= 1:
            well_col = df.columns[0]

        # Find X/lon and Y/lat columns
        x_col = y_col = None
        for cand in ["LON", "LONGITUDE", "X", "EASTING", "X_COORD"]:
            if cand in cols_u:
                x_col = cols_u[cand]
                break
        for cand in ["LAT", "LATITUDE", "Y", "NORTHING", "Y_COORD"]:
            if cand in cols_u:
                y_col = cols_u[cand]
                break

        if well_col and x_col and y_col:
            for _, row in df.iterrows():
                wname = str(row[well_col]).strip()
                try:
                    lon = float(row[x_col])
                    lat = float(row[y_col])
                    if wname and not np.isnan(lon) and not np.isnan(lat):
                        coords[wname] = {"lon": lon, "lat": lat}
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return coords


def parse_tops_bytes(filename: str, content_bytes: bytes) -> dict:
    """Parse Excel/CSV formation tops. Returns {well_name: [{name, md, tvdss}]}."""
    tops: dict[str, list] = {}
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content_bytes))
        else:
            df = pd.read_excel(io.BytesIO(content_bytes))
        df.columns = [str(c).strip() for c in df.columns]
        cols_u = {c.upper(): c for c in df.columns}

        well_col = None
        for cand in ["WELLBORE", "WELL", "WELL NAME", "WELLNAME"]:
            if cand in cols_u:
                well_col = cols_u[cand]
                break

        fm_col = None
        for cand in ["FORMATION", "FM", "HORIZON", "NAME", "TOP"]:
            if cand in cols_u:
                fm_col = cols_u[cand]
                break

        md_col = None
        for cand in ["MD", "DEPTH MD", "MEASURED DEPTH", "AHD", "MDEPTH"]:
            if cand in cols_u:
                md_col = cols_u[cand]
                break

        tvdss_col = None
        for cand in ["TVDSS", "TVD SS", "SUBSEA", "TVDSS_M"]:
            if cand in cols_u:
                tvdss_col = cols_u[cand]
                break

        if well_col and fm_col and md_col:
            for _, row in df.iterrows():
                wname = str(row[well_col]).strip()
                fm = str(row[fm_col]).strip()
                try:
                    md = float(row[md_col])
                except (ValueError, TypeError):
                    continue
                tvdss = None
                if tvdss_col:
                    try:
                        tvdss = float(row[tvdss_col])
                    except (ValueError, TypeError):
                        pass
                if wname not in tops:
                    tops[wname] = []
                tops[wname].append({"name": fm, "md": md, "tvdss": tvdss})
    except Exception:
        pass
    return tops


def interp_temp_at_depth(wd: dict, depth: float) -> float | None:
    """Linear interpolation of temperature at given depth for a well."""
    dept = wd.get("dept", [])
    temp = wd.get("temp", [])
    if not dept or not temp or len(dept) < 2:
        return None
    if depth < dept[0] or depth > dept[-1]:
        return None
    lo, hi = 0, len(dept) - 1
    while lo < hi - 1:
        mid = (lo + hi) >> 1
        if dept[mid] <= depth:
            lo = mid
        else:
            hi = mid
    v0, v1 = temp[lo], temp[hi]
    if v0 is None or v1 is None:
        return None
    if dept[lo] == dept[hi]:
        return v0
    return v0 + (depth - dept[lo]) / (dept[hi] - dept[lo]) * (v1 - v0)


def compute_tp_gradient(wells_data: list[dict]) -> dict | None:
    """
    Compute average temperature-vs-depth gradient across multiple wells.
    Adaptive smoothing: wider window when fewer wells contribute.
    """
    wds = [w for w in wells_data if w and w.get("has_temp") and w.get("dept") and w.get("temp")]
    if not wds:
        return None
    min_d = min(w["dept"][0] for w in wds)
    max_d = max(w["dept"][-1] for w in wds)
    step = max(5, round((max_d - min_d) / 300))
    depths = list(np.arange(np.ceil(min_d / step) * step, max_d + step * 0.5, step))
    if not depths:
        return None

    temps = []
    counts = []
    for d in depths:
        vals = [interp_temp_at_depth(w, d) for w in wds]
        vals = [v for v in vals if v is not None and np.isfinite(v)]
        counts.append(len(vals))
        temps.append(sum(vals) / len(vals) if vals else None)

    # Adaptive smoothing
    win_base = 9
    smoothed = []
    n_pts = len(temps)
    for i, v in enumerate(temps):
        if v is None:
            smoothed.append(None)
            continue
        n = counts[i] or 1
        win = round(win_base * (1 + 1 / n))
        hw = win // 2
        s, c = 0.0, 0
        for j in range(max(0, i - hw), min(n_pts, i + hw + 1)):
            if temps[j] is not None:
                s += temps[j]
                c += 1
        smoothed.append(s / c if c > 0 else v)

    # Trim leading/trailing None
    s_i, e_i = 0, len(depths) - 1
    while s_i < e_i and smoothed[s_i] is None:
        s_i += 1
    while e_i > s_i and smoothed[e_i] is None:
        e_i -= 1

    return {
        "depths": depths[s_i:e_i + 1],
        "temps": smoothed[s_i:e_i + 1],
    }


def idw_interpolate(known_x, known_y, known_z, grid_x, grid_y, power=2):
    """IDW interpolation onto a 2-D grid."""
    result = np.full(grid_x.shape, np.nan)
    for i in range(grid_x.shape[0]):
        for j in range(grid_x.shape[1]):
            dist2 = (known_x - grid_x[i, j]) ** 2 + (known_y - grid_y[i, j]) ** 2
            if np.any(dist2 == 0):
                result[i, j] = known_z[np.argmin(dist2)]
            else:
                w = 1.0 / dist2 ** (power / 2)
                result[i, j] = np.sum(w * known_z) / np.sum(w)
    return result


def _decode_upload(content: str) -> bytes:
    """Decode a dcc.Upload content string to bytes."""
    _, b64 = content.split(",", 1)
    return base64.b64decode(b64)


# ─────────────────────────────────────────────────────────────────
#  FIGURE BUILDERS
# ─────────────────────────────────────────────────────────────────

def _empty_fig(message="Load data to begin") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **PLOT_LAYOUT_BASE,
        annotations=[dict(
            text=message, x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=12, color=DARK_SUB),
        )],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


def build_3d_figure(wells: dict, coords: dict, wellinfo: dict, color_by="temp",
                    colorscale="Jet") -> go.Figure:
    """3D scatter of well temperature/pressure/GR data."""
    fig = go.Figure()
    has_data = False

    unit_label = {"temp": "°C", "pres": "kPa", "gr": "GAPI"}.get(color_by, "")
    axis_label = {"temp": "Temperature", "pres": "Pressure", "gr": "Gamma Ray"}.get(color_by, color_by)

    all_vals = []
    for k, wd in wells.items():
        xy = coords.get(k)
        if not xy:
            continue
        arr = {"temp": wd.get("temp", []), "pres": wd.get("pres", []), "gr": wd.get("gr", [])}.get(color_by, [])
        if not arr:
            continue
        good = [(d, v) for d, v in zip(wd["dept"], arr) if v is not None and np.isfinite(v)]
        if not good:
            continue
        all_vals.extend(v for _, v in good)

    vmin = min(all_vals) if all_vals else 0
    vmax = max(all_vals) if all_vals else 100

    for idx, (k, wd) in enumerate(wells.items()):
        xy = coords.get(k)
        if not xy:
            continue
        arr = {"temp": wd.get("temp", []), "pres": wd.get("pres", []), "gr": wd.get("gr", [])}.get(color_by, [])
        if not arr:
            continue
        dept = wd["dept"]
        good = [(d, v) for d, v in zip(dept, arr) if v is not None and np.isfinite(v)]
        if not good:
            continue
        d_arr, v_arr = zip(*good)

        fig.add_trace(go.Scatter3d(
            x=[xy["lon"]] * len(d_arr),
            y=[xy["lat"]] * len(d_arr),
            z=[-d for d in d_arr],  # depth is negative Z
            mode="markers",
            marker=dict(
                size=3,
                color=list(v_arr),
                colorscale=colorscale,
                cmin=vmin,
                cmax=vmax,
                showscale=(idx == 0),
                colorbar=dict(
                    title=dict(text=f"{axis_label} ({unit_label})", font=dict(size=9)),
                    thickness=12,
                    tickfont=dict(size=8, color=DARK_SUB),
                    bgcolor="rgba(17,21,32,0.7)",
                    bordercolor=DARK_BORDER,
                ) if idx == 0 else None,
            ),
            name=wd.get("well", k),
            hovertemplate=(
                f"<b>{wd.get('well', k)}</b><br>"
                f"Depth: %{{z:.0f}} m<br>"
                f"{axis_label}: %{{marker.color:.1f}} {unit_label}<extra></extra>"
            ),
        ))
        has_data = True

    layout = dict(
        **PLOT_LAYOUT_BASE,
        scene=dict(
            bgcolor=DARK_BG,
            xaxis=dict(title="X / Lon", color=DARK_SUB, gridcolor=DARK_BORDER, backgroundcolor=DARK_BG),
            yaxis=dict(title="Y / Lat", color=DARK_SUB, gridcolor=DARK_BORDER, backgroundcolor=DARK_BG),
            zaxis=dict(title="Depth (m)", color=DARK_SUB, gridcolor=DARK_BORDER, backgroundcolor=DARK_BG),
        ),
        margin=dict(l=0, r=0, t=30, b=0),
    )

    if not has_data:
        layout["annotations"] = [dict(
            text="No wells with coordinates and data",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=12, color=DARK_SUB),
        )]

    fig.update_layout(**layout)
    return fig


def build_log_figure(wd: dict, tops: dict, curve: str, track_idx: int) -> go.Figure:
    """Build a single log track figure."""
    dept = wd.get("dept", [])
    arr = {"temp": wd.get("temp", []), "pres": wd.get("pres", []), "gr": wd.get("gr", [])}.get(curve, [])
    unit = {"temp": "°C", "pres": "kPa", "gr": "GAPI"}.get(curve, "")
    color = {"temp": DARK_GOLD, "pres": DARK_ACCENT, "gr": DARK_GREEN}.get(curve, DARK_TEXT)

    fig = go.Figure()

    if dept and arr:
        clean = [(d, v) for d, v in zip(dept, arr) if v is not None]
        if clean:
            d_arr, v_arr = zip(*clean)
            fig.add_trace(go.Scatter(
                x=list(v_arr), y=list(d_arr),
                mode="lines",
                line=dict(color=color, width=1.5),
                name=curve.upper(),
                hovertemplate=f"%{{x:.1f}} {unit} @ %{{y:.0f}} m<extra></extra>",
            ))

    # Formation tops
    wname = wd.get("well", "")
    well_tops = tops.get(wname, [])
    top_colors = ["#e8b84b", "#e74c3c", "#3b9ede", "#2ecc71", "#9b59b6",
                  "#1abc9c", "#e67e22", "#00bcd4", "#f1c40f", "#ff5722"]
    for ti, top in enumerate(well_tops):
        fig.add_shape(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=top["md"], y1=top["md"],
            line=dict(color=top_colors[ti % len(top_colors)], width=1, dash="dash"),
        )
        fig.add_annotation(
            x=0.02, xref="paper", y=top["md"],
            text=top["name"],
            font=dict(size=7, color=top_colors[ti % len(top_colors)]),
            showarrow=False,
            xanchor="left",
        )

    fig.update_layout(
        **PLOT_LAYOUT_BASE,
        margin=dict(l=50, r=8, t=8, b=30),
        xaxis=dict(**AXIS_DARK, title=dict(text=f"{curve.upper()} ({unit})")),
        yaxis=dict(**AXIS_DARK, autorange="reversed", title=dict(text="Depth (m)")),
        showlegend=False,
    )
    return fig


def build_xsection_figure(wells: dict, coords: dict, tops: dict, section_wells: list[str],
                           color_by="temp", colorscale="Jet", show_tops=True) -> go.Figure:
    """Cross-section with interpolated fill between wells."""
    fig = go.Figure()

    if len(section_wells) < 2:
        fig.update_layout(
            **PLOT_LAYOUT_BASE,
            annotations=[dict(text="Select at least 2 wells for cross-section",
                              x=0.5, y=0.5, xref="paper", yref="paper",
                              showarrow=False, font=dict(size=12, color=DARK_SUB))],
        )
        return fig

    valid = [k for k in section_wells if k in wells]
    if len(valid) < 2:
        fig.update_layout(**PLOT_LAYOUT_BASE)
        return fig

    # Build horizontal distance axis
    cum_dist = [0.0]
    for i in range(1, len(valid)):
        c1, c2 = coords.get(valid[i - 1]), coords.get(valid[i])
        if c1 and c2:
            dx = c2["lon"] - c1["lon"]
            dy = c2["lat"] - c1["lat"]
            cum_dist.append(cum_dist[-1] + (dx**2 + dy**2) ** 0.5)
        else:
            cum_dist.append(cum_dist[-1] + 1.0)

    unit_label = {"temp": "°C", "pres": "kPa", "gr": "GAPI"}.get(color_by, "")
    axis_label = {"temp": "Temperature", "pres": "Pressure", "gr": "Gamma Ray"}.get(color_by, color_by)
    track_colors = {"temp": DARK_GOLD, "pres": DARK_ACCENT, "gr": DARK_GREEN}

    all_vals = []
    well_data_list = []
    for k in valid:
        wd = wells[k]
        arr = {"temp": wd.get("temp", []), "pres": wd.get("pres", []), "gr": wd.get("gr", [])}.get(color_by, [])
        good = [(d, v) for d, v in zip(wd["dept"], arr) if v is not None and np.isfinite(v)]
        well_data_list.append(good)
        all_vals.extend(v for _, v in good)

    vmin = min(all_vals) if all_vals else 0
    vmax = max(all_vals) if all_vals else 100

    # Collect depth range
    min_d = min((w["dept"][0] for k in valid for w in [wells[k]]), default=0)
    max_d = max((w["dept"][-1] for k in valid for w in [wells[k]]), default=800)
    n_depth = 150
    depth_grid = np.linspace(min_d, max_d, n_depth)

    # Build interpolated 2D grid
    n_wells = len(valid)
    temp_matrix = np.full((n_depth, n_wells), np.nan)
    for wi, k in enumerate(valid):
        wd = wells[k]
        for di, d in enumerate(depth_grid):
            v = interp_temp_at_depth(wd, d)
            if v is not None:
                temp_matrix[di, wi] = v

    # Add heatmap
    n_x = max(n_wells * 10, 50)
    x_positions = np.linspace(0, cum_dist[-1], n_x)
    # Interpolate horizontally
    z_interp = np.full((n_depth, n_x), np.nan)
    for di in range(n_depth):
        row = temp_matrix[di, :]
        valid_wi = [i for i in range(n_wells) if not np.isnan(row[i])]
        if len(valid_wi) >= 2:
            xp = [cum_dist[i] for i in valid_wi]
            fp = [row[i] for i in valid_wi]
            z_interp[di, :] = np.interp(x_positions, xp, fp)

    fig.add_trace(go.Heatmap(
        z=z_interp,
        x=x_positions,
        y=depth_grid,
        colorscale=colorscale,
        zmin=vmin, zmax=vmax,
        colorbar=dict(
            title=dict(text=f"{axis_label} ({unit_label})", font=dict(size=9)),
            thickness=12,
            tickfont=dict(size=8, color=DARK_SUB),
        ),
        hovertemplate=f"Dist: %{{x:.2f}}<br>Depth: %{{y:.0f}} m<br>{axis_label}: %{{z:.1f}} {unit_label}<extra></extra>",
    ))

    # Well log curves + well lines
    for wi, k in enumerate(valid):
        wd = wells[k]
        arr = {"temp": wd.get("temp", []), "pres": wd.get("pres", []), "gr": wd.get("gr", [])}.get(color_by, [])
        clean = [(d, v) for d, v in zip(wd["dept"], arr) if v is not None]
        # Vertical well line
        fig.add_shape(
            type="line",
            x0=cum_dist[wi], x1=cum_dist[wi],
            y0=min_d, y1=max_d,
            line=dict(color=DARK_MUTED, width=1, dash="dot"),
        )
        # Well label
        fig.add_annotation(
            x=cum_dist[wi], y=min_d - (max_d - min_d) * 0.02,
            text=wd.get("well", k),
            font=dict(size=8, color=DARK_TEXT),
            showarrow=False,
        )

        # Formation tops
        if show_tops:
            wname = wd.get("well", k)
            well_tops = tops.get(wname, [])
            top_colors_list = ["#e8b84b", "#e74c3c", "#3b9ede", "#2ecc71", "#9b59b6"]
            for ti, top in enumerate(well_tops):
                fig.add_shape(
                    type="line",
                    x0=cum_dist[wi] - (cum_dist[-1] * 0.02),
                    x1=cum_dist[wi] + (cum_dist[-1] * 0.02),
                    y0=top["md"], y1=top["md"],
                    line=dict(color=top_colors_list[ti % len(top_colors_list)], width=1),
                )

    fig.update_layout(
        **PLOT_LAYOUT_BASE,
        xaxis=dict(**AXIS_DARK, title=dict(text="Distance along section")),
        yaxis=dict(**AXIS_DARK, autorange="reversed", title=dict(text="Depth (m)")),
        margin=dict(l=60, r=20, t=20, b=50),
    )
    return fig


def build_surface_map(wells: dict, coords: dict, wellinfo: dict, tops: dict,
                      depth: float = 300.0, color_by="temp",
                      colorscale="Jet", show_contours=False) -> go.Figure:
    """2D plan view with IDW-interpolated temperature at selected depth."""
    fig = go.Figure()

    unit_label = {"temp": "°C", "pres": "kPa", "gr": "GAPI"}.get(color_by, "")
    axis_label = {"temp": "Temperature", "pres": "Pressure", "gr": "Gamma Ray"}.get(color_by, color_by)

    known_x, known_y, known_z = [], [], []
    for k, wd in wells.items():
        xy = coords.get(k)
        if not xy:
            continue
        v = interp_temp_at_depth(wd, depth)
        if v is not None and np.isfinite(v):
            known_x.append(xy["lon"])
            known_y.append(xy["lat"])
            known_z.append(v)

    if len(known_x) < 2:
        fig.update_layout(
            **PLOT_LAYOUT_BASE,
            annotations=[dict(text="Need ≥ 2 wells with coordinates and data at this depth",
                              x=0.5, y=0.5, xref="paper", yref="paper",
                              showarrow=False, font=dict(size=12, color=DARK_SUB))],
        )
        return fig

    known_x = np.array(known_x)
    known_y = np.array(known_y)
    known_z = np.array(known_z)

    margin_x = (known_x.max() - known_x.min()) * 0.1 or 0.5
    margin_y = (known_y.max() - known_y.min()) * 0.1 or 0.5
    gx = np.linspace(known_x.min() - margin_x, known_x.max() + margin_x, 50)
    gy = np.linspace(known_y.min() - margin_y, known_y.max() + margin_y, 50)
    GX, GY = np.meshgrid(gx, gy)

    GZ = idw_interpolate(known_x, known_y, known_z, GX, GY, power=2)

    vmin, vmax = known_z.min(), known_z.max()

    fig.add_trace(go.Heatmap(
        z=GZ,
        x=gx,
        y=gy,
        colorscale=colorscale,
        zmin=vmin, zmax=vmax,
        colorbar=dict(
            title=dict(text=f"{axis_label} ({unit_label})", font=dict(size=9)),
            thickness=12,
            tickfont=dict(size=8, color=DARK_SUB),
        ),
        hovertemplate=f"X: %{{x:.2f}}<br>Y: %{{y:.2f}}<br>{axis_label}: %{{z:.1f}} {unit_label}<extra></extra>",
    ))

    if show_contours:
        fig.add_trace(go.Contour(
            z=GZ, x=gx, y=gy,
            showscale=False,
            colorscale=colorscale, zmin=vmin, zmax=vmax,
            contours=dict(showlabels=True, labelfont=dict(size=8, color=DARK_TEXT)),
            line=dict(color="white", width=0.5),
            opacity=0.4,
        ))

    # Well symbols by type/fluid
    type_color_map = {
        "Producer": DARK_GREEN,
        "Injector": DARK_ACCENT,
        "Observer": DARK_GOLD,
        "": DARK_SUB,
    }
    symbol_map = {
        "Producer": "circle",
        "Injector": "triangle-up",
        "Observer": "diamond",
        "": "circle-open",
    }

    # Scatter well points
    for k, wd in wells.items():
        xy = coords.get(k)
        if not xy:
            continue
        wi = wellinfo.get(k, {})
        wtype = wi.get("type", "")
        color = type_color_map.get(wtype, DARK_SUB)
        symbol = symbol_map.get(wtype, "circle")

        v = interp_temp_at_depth(wd, depth)
        hover = f"<b>{wd.get('well', k)}</b><br>Type: {wtype or 'Unknown'}<br>"
        if v is not None:
            hover += f"{axis_label}: {v:.1f} {unit_label}"

        fig.add_trace(go.Scatter(
            x=[xy["lon"]], y=[xy["lat"]],
            mode="markers+text",
            marker=dict(size=8, color=color, symbol=symbol,
                        line=dict(color="white", width=1)),
            text=[wd.get("well", k)],
            textposition="top center",
            textfont=dict(size=7, color=DARK_TEXT),
            hovertemplate=hover + "<extra></extra>",
            showlegend=False,
        ))

    fig.update_layout(
        **PLOT_LAYOUT_BASE,
        xaxis=dict(**AXIS_DARK, title=dict(text="X / Lon"), scaleanchor="y"),
        yaxis=dict(**AXIS_DARK, title=dict(text="Y / Lat")),
        margin=dict(l=60, r=20, t=30, b=50),
        title=dict(text=f"{axis_label} at depth {depth:.0f} m",
                   font=dict(size=10, color=DARK_SUB), x=0.5),
    )
    return fig


def build_tp_figure(wells: dict, wellinfo: dict,
                    excluded: list, compare: list,
                    mode: str = "all") -> go.Figure:
    """Temperature Pattern Analysis figure."""
    active_wks = [k for k in wells if wells[k].get("has_temp") and k not in excluded]

    layout = dict(
        **PLOT_LAYOUT_BASE,
        xaxis=dict(**AXIS_DARK, title=dict(text="Temperature (°C)")),
        yaxis=dict(**AXIS_DARK, autorange="reversed", title=dict(text="Depth (m)")),
        margin=dict(l=62, r=16, t=18, b=48),
        hovermode="closest",
    )

    if not active_wks and not compare:
        fig = go.Figure()
        fig.update_layout(
            **layout,
            annotations=[dict(text="Load wells with temperature data",
                              x=0.5, y=0.5, xref="paper", yref="paper",
                              showarrow=False, font=dict(size=12, color=DARK_SUB))],
        )
        return fig

    traces = []

    def _add_group(wks, label, color):
        group_wds = [wells[k] for k in wks if k in wells]
        grad = compute_tp_gradient(group_wds)
        if not grad:
            return
        traces.append(go.Scatter(
            x=grad["temps"], y=grad["depths"],
            mode="lines",
            name=label,
            line=dict(color=color, width=2),
            hovertemplate=f"%{{x:.1f}}°C @ %{{y:.0f}} m<extra>{label}</extra>",
        ))

    if mode == "all":
        _add_group(active_wks, f"All wells ({len(active_wks)})", DARK_ACCENT)

    elif mode == "type":
        groups = [
            ("Producer", "#4caf6e"),
            ("Injector", "#3b9ede"),
            ("Observer", "#e8b84b"),
            ("", "#7a8fa8"),
        ]
        lbl_map = {"": "Unknown type"}
        for wtype, color in groups:
            gkeys = [k for k in active_wks if (wellinfo.get(k, {}).get("type", "") == wtype)]
            if not gkeys:
                continue
            lbl = lbl_map.get(wtype, wtype)
            _add_group(gkeys, f"{lbl} ({len(gkeys)})", color)

    elif mode == "fluid":
        groups = [
            ("oil",   "#f5a623"),
            ("water", "#3b9ede"),
            ("gas",   "#e74c3c"),
            ("",      "#7a8fa8"),
        ]
        lbl_map = {"": "Unknown fluid", "oil": "Oil", "water": "Water", "gas": "Gas"}
        for fluid, color in groups:
            gkeys = [k for k in active_wks if (wellinfo.get(k, {}).get("fluid", "") == fluid)]
            if not gkeys:
                continue
            _add_group(gkeys, f"{lbl_map[fluid]} ({len(gkeys)})", color)

    elif mode == "combo":
        matched = set()
        for combo in TP_COMBOS:
            t, f_val, lbl, col = combo["t"], combo["f"], combo["lbl"], combo["col"]
            if f_val == "":
                # catch-all for type, exclude specific fluid matches
                specific_fluids = {c["f"] for c in TP_COMBOS if c["t"] == t and c["f"] != ""}
                gkeys = [k for k in active_wks
                         if wellinfo.get(k, {}).get("type", "") == t
                         and wellinfo.get(k, {}).get("fluid", "") not in specific_fluids]
            else:
                gkeys = [k for k in active_wks
                         if wellinfo.get(k, {}).get("type", "") == t
                         and wellinfo.get(k, {}).get("fluid", "") == f_val]
            if not gkeys:
                continue
            for k in gkeys:
                matched.add(k)
            _add_group(gkeys, f"{lbl} ({len(gkeys)})", col)

        unmatched = [k for k in active_wks if k not in matched]
        if unmatched:
            _add_group(unmatched, f"Unknown ({len(unmatched)})", "#7a8fa8")

    # Compare wells — thicker lines
    for cmp in compare:
        k, color = cmp.get("key"), cmp.get("color", DARK_ACCENT)
        wd = wells.get(k)
        if not wd or not wd.get("has_temp"):
            continue
        dept = wd["dept"]
        temp = wd["temp"]
        clean = [(d, v) for d, v in zip(dept, temp) if v is not None]
        if not clean:
            continue
        d_arr, t_arr = zip(*clean)
        traces.append(go.Scatter(
            x=list(t_arr), y=list(d_arr),
            mode="lines",
            name=wd.get("well", k),
            line=dict(color=color, width=3),
            hovertemplate=f"%{{x:.1f}}°C @ %{{y:.0f}} m<extra>{wd.get('well', k)}</extra>",
        ))

    fig = go.Figure(data=traces)
    fig.update_layout(**layout)
    return fig


# ─────────────────────────────────────────────────────────────────
#  LAYOUT HELPERS
# ─────────────────────────────────────────────────────────────────

def _section_header(label, style_extra=None):
    s = {"fontFamily": "IBM Plex Mono", "fontSize": "9px", "letterSpacing": "2px",
         "color": DARK_SUB, "textTransform": "uppercase", "padding": "8px 12px",
         "borderBottom": f"1px solid {DARK_BORDER}"}
    if style_extra:
        s.update(style_extra)
    return html.Div(label, style=s)


def _ctrl_label(text):
    return html.Div(text, style={"fontFamily": "IBM Plex Mono", "fontSize": "9px",
                                  "color": DARK_SUB, "marginBottom": "3px",
                                  "letterSpacing": "1px", "textTransform": "uppercase"})


def _select(id_, options, value=None, **kwargs):
    return dcc.Dropdown(
        id=id_,
        options=options,
        value=value,
        clearable=False,
        style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
               "fontFamily": "IBM Plex Mono"},
        className="dark-select",
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────
#  APP LAYOUT
# ─────────────────────────────────────────────────────────────────

def build_sidebar():
    upload_style = {
        "display": "flex", "flexDirection": "column", "alignItems": "center",
        "gap": "2px", "padding": "9px", "background": DARK_CARD,
        "border": f"1px dashed {DARK_MUTED}", "borderRadius": "3px",
        "cursor": "pointer", "fontSize": "10px", "color": DARK_SUB,
        "textAlign": "center", "marginBottom": "5px",
    }
    upload_sm_style = {
        "padding": "7px 4px", "background": DARK_CARD,
        "border": f"1px dashed {DARK_MUTED}", "borderRadius": "3px",
        "cursor": "pointer", "fontSize": "9px", "color": DARK_SUB,
        "textAlign": "center", "flex": "1",
    }

    return html.Div(id="sidebar", children=[
        # ── Input Data ──────────────────────────────────
        html.Div(style={"borderBottom": f"1px solid {DARK_BORDER}"}, children=[
            _section_header("Input Data"),
            html.Div(style={"padding": "0 12px 9px"}, children=[
                dcc.Upload(
                    id="upload-las",
                    children=html.Div([
                        html.Div("📄"),
                        html.Div("Input LAS files", style={"fontWeight": "600"}),
                        html.Div(".las — multiple", style={"fontSize": "8px"}),
                    ]),
                    style=upload_style,
                    multiple=True,
                    accept=".las",
                ),
                html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "4px"}, children=[
                    dcc.Upload(
                        id="upload-coords",
                        children=html.Div(["📍", html.Br(), "Coordinates", html.Br(),
                                           html.Span(".xlsx/.csv", style={"fontSize": "7px"})]),
                        style=upload_sm_style,
                        multiple=False,
                        accept=".xlsx,.xls,.csv",
                    ),
                    dcc.Upload(
                        id="upload-tops",
                        children=html.Div(["🪨", html.Br(), "Fm. Tops", html.Br(),
                                           html.Span(".xlsx/.csv", style={"fontSize": "7px"})]),
                        style=upload_sm_style,
                        multiple=False,
                        accept=".xlsx,.xls,.csv",
                    ),
                ]),
                html.Div(id="upload-status", style={"fontSize": "9px", "color": DARK_SUB, "marginTop": "4px"}),
            ]),
        ]),

        # ── Wells ────────────────────────────────────────
        html.Div(style={"borderBottom": f"1px solid {DARK_BORDER}"}, children=[
            _section_header("Wells"),
            html.Div(id="well-list", style={"padding": "0 8px 8px"}),
        ]),

        # ── Well Data ────────────────────────────────────
        html.Div(style={"borderBottom": f"1px solid {DARK_BORDER}"}, children=[
            _section_header("Well Data"),
            html.Div(style={"padding": "0 12px 9px"}, children=[
                _ctrl_label("Well"),
                dcc.Dropdown(
                    id="wd-well-sel",
                    options=[], value=None,
                    placeholder="— Select well —",
                    style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                           "fontFamily": "IBM Plex Mono", "marginBottom": "6px"},
                ),
                html.Div(style={"display": "flex", "gap": "4px", "marginBottom": "4px"}, children=[
                    html.Div(style={"flex": "1"}, children=[
                        _ctrl_label("Type"),
                        dcc.Dropdown(
                            id="wd-type-sel",
                            options=[
                                {"label": "—", "value": ""},
                                {"label": "Producer", "value": "Producer"},
                                {"label": "Injector", "value": "Injector"},
                                {"label": "Observer", "value": "Observer"},
                            ],
                            value="",
                            clearable=False,
                            style={"background": DARK_CARD, "color": DARK_TEXT,
                                   "fontSize": "10px", "fontFamily": "IBM Plex Mono"},
                        ),
                    ]),
                    html.Div(style={"flex": "1"}, children=[
                        _ctrl_label("Fluid"),
                        dcc.Dropdown(
                            id="wd-fluid-sel",
                            options=[
                                {"label": "—", "value": ""},
                                {"label": "Oil", "value": "oil"},
                                {"label": "Water", "value": "water"},
                                {"label": "Gas", "value": "gas"},
                            ],
                            value="",
                            clearable=False,
                            style={"background": DARK_CARD, "color": DARK_TEXT,
                                   "fontSize": "10px", "fontFamily": "IBM Plex Mono"},
                        ),
                    ]),
                ]),
                html.Button(
                    "Apply",
                    id="wd-apply-btn",
                    style={"width": "100%", "background": DARK_ACCENT, "color": "white",
                           "border": "none", "borderRadius": "3px", "padding": "5px",
                           "fontFamily": "IBM Plex Mono", "fontSize": "9px", "cursor": "pointer"},
                ),
            ]),
        ]),
    ], style={"width": "258px", "minWidth": "258px", "background": DARK_PANEL,
              "borderRight": f"1px solid {DARK_BORDER}", "display": "flex",
              "flexDirection": "column", "overflowY": "auto"})


def build_3d_tab():
    sb_style = {"width": "165px", "minWidth": "150px", "background": DARK_PANEL,
                "borderRight": f"1px solid {DARK_BORDER}", "display": "flex",
                "flexDirection": "column", "overflowY": "auto", "padding": "9px",
                "gap": "4px", "flexShrink": "0"}
    return html.Div(id="tab-3d", className="tab-panel", children=[
        html.Div(style=sb_style, children=[
            html.Div("Colour Curve", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="td-colorby",
                options=[
                    {"label": "Temperature (°C)", "value": "temp"},
                    {"label": "Pressure (kPa)", "value": "pres"},
                    {"label": "Gamma Ray (GAPI)", "value": "gr"},
                ],
                value="temp", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER, "margin": "3px 0"}),
            html.Div("Colour Scale", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="td-colorscale",
                options=[{"label": s, "value": s}
                         for s in ["Jet", "Hot", "RdBu", "Viridis", "Plasma", "Turbo", "YlOrRd"]],
                value="Jet", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
        ]),
        dcc.Graph(id="plot-3d", className="plot-container",
                  config={"displayModeBar": False, "responsive": True},
                  style={"flex": "1", "minWidth": "0", "height": "100%"}),
    ], style={"display": "flex", "flex": "1", "overflow": "hidden", "minHeight": "0"})


def build_log_tab():
    bar_style = {"padding": "6px 14px", "background": DARK_PANEL,
                 "borderBottom": f"1px solid {DARK_BORDER}",
                 "display": "flex", "gap": "8px", "alignItems": "center",
                 "flexShrink": "0", "flexWrap": "wrap"}
    return html.Div(id="tab-log", className="tab-panel", children=[
        html.Div(style=bar_style, children=[
            html.Span("Well:", style={"fontSize": "9px", "color": DARK_SUB,
                                      "fontFamily": "IBM Plex Mono"}),
            dcc.Dropdown(
                id="log-well-sel",
                options=[], value=None,
                placeholder="— Select well —",
                clearable=True,
                style={"width": "200px", "background": DARK_CARD, "color": DARK_TEXT,
                       "fontSize": "10px", "fontFamily": "IBM Plex Mono"},
            ),
            html.Span(id="log-info", style={"marginLeft": "auto", "fontFamily": "IBM Plex Mono",
                                             "fontSize": "8px", "color": DARK_SUB}),
        ]),
        html.Div(id="log-tracks-container",
                 style={"flex": "1", "display": "flex", "overflow": "hidden", "minHeight": "0"}),
    ], style={"display": "flex", "flexDirection": "column", "flex": "1",
              "overflow": "hidden", "minHeight": "0"})


def build_xsection_tab():
    sb_style = {"width": "200px", "minWidth": "180px", "background": DARK_PANEL,
                "borderRight": f"1px solid {DARK_BORDER}", "display": "flex",
                "flexDirection": "column", "overflowY": "auto", "padding": "9px",
                "gap": "5px", "flexShrink": "0"}
    return html.Div(id="tab-xs", className="tab-panel", children=[
        html.Div(style=sb_style, children=[
            html.Div("Wells in section", className="panel-sb-lbl"),
            dcc.Checklist(
                id="xs-wells",
                options=[], value=[],
                inputStyle={"accentColor": DARK_ACCENT, "marginRight": "4px"},
                labelStyle={"display": "flex", "alignItems": "center",
                            "fontSize": "9px", "fontFamily": "IBM Plex Mono",
                            "padding": "2px 4px", "cursor": "pointer"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            html.Div("Colour Curve", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="xs-colorby",
                options=[
                    {"label": "Temperature (°C)", "value": "temp"},
                    {"label": "Pressure (kPa)", "value": "pres"},
                    {"label": "Gamma Ray (GAPI)", "value": "gr"},
                ],
                value="temp", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
            html.Div("Colour Scale", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="xs-colorscale",
                options=[{"label": s, "value": s}
                         for s in ["Jet", "Hot", "RdBu", "Viridis", "Plasma", "Turbo", "YlOrRd"]],
                value="Jet", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            dcc.Checklist(
                id="xs-show-tops",
                options=[{"label": " Show formation tops", "value": "show"}],
                value=["show"],
                inputStyle={"accentColor": DARK_ACCENT, "marginRight": "4px"},
                labelStyle={"display": "flex", "alignItems": "center",
                            "fontSize": "10px", "cursor": "pointer"},
            ),
        ]),
        dcc.Graph(id="plot-xs", className="plot-container",
                  config={"displayModeBar": False, "responsive": True},
                  style={"flex": "1", "minWidth": "0", "height": "100%"}),
    ], style={"display": "flex", "flex": "1", "overflow": "hidden", "minHeight": "0"})


def build_map_tab():
    sb_style = {"width": "195px", "minWidth": "150px", "background": DARK_PANEL,
                "borderRight": f"1px solid {DARK_BORDER}", "display": "flex",
                "flexDirection": "column", "overflowY": "auto", "padding": "9px",
                "gap": "5px", "flexShrink": "0"}
    return html.Div(id="tab-map", className="tab-panel", children=[
        html.Div(style=sb_style, children=[
            html.Div("Variable", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="mp-curve",
                options=[
                    {"label": "Temperature (°C)", "value": "temp"},
                    {"label": "Pressure (kPa)", "value": "pres"},
                    {"label": "Gamma Ray (GAPI)", "value": "gr"},
                ],
                value="temp", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            html.Div("Colour Scale", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="mp-colorscale",
                options=[{"label": s, "value": s}
                         for s in ["Jet", "Hot", "RdBu", "Viridis", "Plasma", "Turbo", "YlOrRd"]],
                value="Jet", clearable=False,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            html.Div("Depth (m)", className="panel-sb-lbl"),
            dcc.Slider(
                id="mp-depth-slider",
                min=0, max=3000, value=300, step=10,
                marks={0: {"label": "0", "style": {"color": DARK_SUB, "fontSize": "8px"}},
                       1500: {"label": "1500", "style": {"color": DARK_SUB, "fontSize": "8px"}},
                       3000: {"label": "3000", "style": {"color": DARK_SUB, "fontSize": "8px"}}},
                tooltip={"placement": "bottom", "always_visible": True},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            dcc.Checklist(
                id="mp-contours",
                options=[{"label": " Show contours", "value": "show"}],
                value=[],
                inputStyle={"accentColor": DARK_ACCENT, "marginRight": "4px"},
                labelStyle={"display": "flex", "alignItems": "center",
                            "fontSize": "10px", "cursor": "pointer"},
            ),
        ]),
        dcc.Graph(id="plot-map", className="plot-container",
                  config={"displayModeBar": False, "responsive": True},
                  style={"flex": "1", "minWidth": "0", "height": "100%"}),
    ], style={"display": "flex", "flex": "1", "overflow": "hidden", "minHeight": "0"})


def build_tp_tab():
    sb_style = {"width": "215px", "minWidth": "200px", "background": DARK_PANEL,
                "borderRight": f"1px solid {DARK_BORDER}", "display": "flex",
                "flexDirection": "column", "overflowY": "auto", "padding": "9px",
                "gap": "4px", "flexShrink": "0"}
    return html.Div(id="tab-tp", className="tab-panel", children=[
        html.Div(style=sb_style, children=[
            html.Div("Baseline Mode", className="panel-sb-lbl"),
            dcc.RadioItems(
                id="tp-mode",
                options=[
                    {"label": " All wells combined", "value": "all"},
                    {"label": " By well type", "value": "type"},
                    {"label": " By fluid type", "value": "fluid"},
                    {"label": " By type & fluid", "value": "combo"},
                ],
                value="all",
                inputStyle={"accentColor": DARK_ACCENT, "marginRight": "5px"},
                labelStyle={"display": "flex", "alignItems": "center",
                            "fontSize": "10px", "cursor": "pointer",
                            "padding": "1px 0", "fontFamily": "IBM Plex Mono"},
            ),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            html.Div(style={"display": "flex", "justifyContent": "space-between",
                            "alignItems": "center"}, children=[
                html.Div("Wells in baseline", className="panel-sb-lbl",
                         style={"marginBottom": "0"}),
                html.Span(id="tp-bl-cnt", style={"fontFamily": "IBM Plex Mono",
                                                   "fontSize": "9px", "color": DARK_TEXT}),
            ]),
            html.Div(id="tp-well-list", style={"maxHeight": "200px", "overflowY": "auto"}),
            html.Div(style={"display": "flex", "gap": "3px"}, children=[
                html.Button("All", id="tp-select-all", n_clicks=0,
                            style={"flex": "1", "background": DARK_CARD, "color": DARK_TEXT,
                                   "border": f"1px solid {DARK_BORDER}", "borderRadius": "3px",
                                   "padding": "3px", "fontFamily": "IBM Plex Mono",
                                   "fontSize": "9px", "cursor": "pointer"}),
                html.Button("None", id="tp-select-none", n_clicks=0,
                            style={"flex": "1", "background": DARK_CARD, "color": DARK_TEXT,
                                   "border": f"1px solid {DARK_BORDER}", "borderRadius": "3px",
                                   "padding": "3px", "fontFamily": "IBM Plex Mono",
                                   "fontSize": "9px", "cursor": "pointer"}),
            ]),
            html.Div(style={"height": "1px", "background": DARK_BORDER}),
            html.Div("Compare Well", className="panel-sb-lbl"),
            dcc.Dropdown(
                id="tp-cmp-sel",
                options=[], value=None,
                placeholder="— Select well —",
                clearable=True,
                style={"background": DARK_CARD, "color": DARK_TEXT, "fontSize": "10px",
                       "fontFamily": "IBM Plex Mono", "marginBottom": "4px"},
            ),
            html.Button(
                "+ Add to comparison", id="tp-add-cmp", n_clicks=0,
                style={"width": "100%", "background": DARK_ACCENT, "color": "white",
                       "border": "none", "borderRadius": "3px", "padding": "5px 8px",
                       "fontFamily": "IBM Plex Mono", "fontSize": "9px", "cursor": "pointer"},
            ),
            html.Div(id="tp-cmp-list", style={"marginTop": "4px"}),
        ]),
        dcc.Graph(id="plot-tp", className="plot-container",
                  config={"displayModeBar": False, "responsive": True},
                  style={"flex": "1", "minWidth": "0", "height": "100%"}),
    ], style={"display": "flex", "flex": "1", "overflow": "hidden", "minHeight": "0"})


# ─────────────────────────────────────────────────────────────────
#  FULL APP LAYOUT
# ─────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[],  # using local assets/tfm.css
    suppress_callback_exceptions=True,
    title="TFM 2.0",
    update_title=None,
)

app.layout = html.Div(style={"background": DARK_BG, "color": DARK_TEXT,
                               "minHeight": "100vh", "display": "flex",
                               "flexDirection": "column", "fontFamily": "IBM Plex Sans"}, children=[

    # ── Header ──────────────────────────────────────────
    html.Div(id="header", style={
        "display": "flex", "alignItems": "center", "gap": "12px",
        "padding": "0 16px", "height": "44px",
        "background": DARK_PANEL, "borderBottom": f"1px solid {DARK_BORDER}",
        "flexShrink": "0",
    }, children=[
        html.Div("TFM", style={
            "fontFamily": "IBM Plex Mono", "fontSize": "9px", "fontWeight": "700",
            "letterSpacing": "3px", "color": DARK_ACCENT,
            "border": f"1px solid {DARK_ACCENT}", "padding": "3px 7px", "borderRadius": "2px",
        }),
        html.H1("TEMPERATURE FIELD MAP v2.0", style={
            "fontFamily": "IBM Plex Mono", "fontSize": "12px", "fontWeight": "600",
            "letterSpacing": "1px", "color": DARK_TEXT, "margin": "0",
        }),
        html.Div(style={"marginLeft": "auto", "fontFamily": "IBM Plex Mono",
                        "fontSize": "9px", "color": DARK_SUB, "textAlign": "right"}, children=[
            html.Div(id="hdr-well-count", children="0 wells"),
        ]),
    ]),

    # ── Body ─────────────────────────────────────────────
    html.Div(id="app-body", style={
        "display": "flex", "flex": "1",
        "height": "calc(100vh - 70px)", "overflow": "hidden",
    }, children=[

        build_sidebar(),

        # ── Main content ──────────────────────────────────
        html.Div(id="main-content", style={
            "flex": "1", "display": "flex", "flexDirection": "column",
            "overflow": "hidden", "minWidth": "0",
        }, children=[

            # Tab bar
            html.Div(style={
                "display": "flex", "background": DARK_PANEL,
                "borderBottom": f"1px solid {DARK_BORDER}",
                "padding": "0 14px", "gap": "2px", "flexShrink": "0",
            }, children=[
                html.Button("3D MODEL",     id="tab-btn-3d",  n_clicks=0, className="tab-btn active"),
                html.Button("LOG TRACKS",   id="tab-btn-log", n_clicks=0, className="tab-btn"),
                html.Button("X-SECTION",    id="tab-btn-xs",  n_clicks=0, className="tab-btn"),
                html.Button("SURFACE MAP",  id="tab-btn-map", n_clicks=0, className="tab-btn"),
                html.Button("TEMP PATTERN", id="tab-btn-tp",  n_clicks=0, className="tab-btn"),
            ]),

            # Tab panels container
            html.Div(id="tab-panels", style={
                "flex": "1", "display": "flex", "overflow": "hidden",
                "minHeight": "0",
            }, children=[
                build_3d_tab(),
                build_log_tab(),
                build_xsection_tab(),
                build_map_tab(),
                build_tp_tab(),
            ]),
        ]),
    ]),

    # ── Status bar ──────────────────────────────────────
    html.Div(id="status-bar", style={
        "height": "26px", "background": DARK_PANEL,
        "borderTop": f"1px solid {DARK_BORDER}",
        "padding": "0 14px", "display": "flex", "alignItems": "center",
        "gap": "16px", "fontFamily": "IBM Plex Mono", "fontSize": "8px",
        "color": DARK_SUB, "flexShrink": "0",
    }, children=[
        html.Span(["Wells: ", html.Span("0", id="sb-wells", style={"color": DARK_TEXT})]),
        html.Span(["Tops: ",  html.Span("0", id="sb-tops",  style={"color": DARK_TEXT})]),
        html.Span("TFM v2.0 · N. Ikhsanov · Halliburton",
                  style={"marginLeft": "auto"}),
    ]),

    # ── dcc.Store components ────────────────────────────
    dcc.Store(id="store-wells",   data={}),
    dcc.Store(id="store-coords",  data={}),
    dcc.Store(id="store-wellinfo",data={}),
    dcc.Store(id="store-tops",    data={}),
    dcc.Store(id="store-tp-excluded", data=[]),
    dcc.Store(id="store-tp-compare",  data=[]),
    dcc.Store(id="store-active-tab",  data="3d"),
])


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — FILE UPLOADS
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("store-wells", "data"),
    Output("upload-status", "children"),
    Input("upload-las", "contents"),
    State("upload-las", "filename"),
    State("store-wells", "data"),
    prevent_initial_call=True,
)
def handle_las_upload(contents_list, filenames, current_wells):
    if not contents_list:
        return no_update, no_update
    wells = dict(current_wells or {})
    loaded, failed = 0, 0
    for content, filename in zip(contents_list, filenames):
        try:
            raw = _decode_upload(content)
            wd = parse_las_bytes(filename, raw)
            if wd:
                key = wd["well"] or filename.rsplit(".", 1)[0]
                wells[key] = wd
                loaded += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    msg = f"Loaded {loaded} LAS"
    if failed:
        msg += f", {failed} failed"
    return wells, html.Span(msg, style={"color": DARK_GREEN if not failed else DARK_ORANGE})


@app.callback(
    Output("store-coords", "data"),
    Input("upload-coords", "contents"),
    State("upload-coords", "filename"),
    State("store-coords", "data"),
    prevent_initial_call=True,
)
def handle_coords_upload(content, filename, current_coords):
    if not content or not filename:
        return no_update
    coords = dict(current_coords or {})
    try:
        raw = _decode_upload(content)
        new_coords = parse_coords_bytes(filename, raw)
        coords.update(new_coords)
    except Exception:
        pass
    return coords


@app.callback(
    Output("store-tops", "data"),
    Input("upload-tops", "contents"),
    State("upload-tops", "filename"),
    State("store-tops", "data"),
    prevent_initial_call=True,
)
def handle_tops_upload(content, filename, current_tops):
    if not content or not filename:
        return no_update
    tops = dict(current_tops or {})
    try:
        raw = _decode_upload(content)
        new_tops = parse_tops_bytes(filename, raw)
        tops.update(new_tops)
    except Exception:
        pass
    return tops


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — WELL DATA EDITOR
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("wd-type-sel", "value"),
    Output("wd-fluid-sel", "value"),
    Input("wd-well-sel", "value"),
    State("store-wellinfo", "data"),
    prevent_initial_call=True,
)
def load_well_info(well_key, wellinfo):
    if not well_key:
        return "", ""
    wi = (wellinfo or {}).get(well_key, {})
    return wi.get("type", ""), wi.get("fluid", "")


@app.callback(
    Output("store-wellinfo", "data"),
    Input("wd-apply-btn", "n_clicks"),
    State("wd-well-sel", "value"),
    State("wd-type-sel", "value"),
    State("wd-fluid-sel", "value"),
    State("store-wellinfo", "data"),
    prevent_initial_call=True,
)
def apply_well_info(n_clicks, well_key, wtype, fluid, wellinfo):
    if not well_key:
        return no_update
    wi = dict(wellinfo or {})
    wi[well_key] = {"type": wtype or "", "fluid": fluid or ""}
    return wi


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — UI UPDATES FROM STORES
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("well-list", "children"),
    Output("log-well-sel", "options"),
    Output("wd-well-sel", "options"),
    Output("tp-cmp-sel", "options"),
    Output("xs-wells", "options"),
    Output("xs-wells", "value"),
    Output("hdr-well-count", "children"),
    Output("sb-wells", "children"),
    Output("sb-tops", "children"),
    Input("store-wells", "data"),
    Input("store-coords", "data"),
    Input("store-tops", "data"),
    State("xs-wells", "value"),
)
def update_ui_from_stores(wells, coords, tops, xs_current):
    wells = wells or {}
    coords = coords or {}
    tops = tops or {}

    def _nat_sort_key(k):
        parts = re.split(r"(\d+)", str(k))
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    sorted_keys = sorted(wells.keys(), key=_nat_sort_key)

    # Well list cards
    well_cards = []
    for k in sorted_keys:
        wd = wells[k]
        has_xy = k in coords
        badges = []
        if wd.get("has_temp"):
            badges.append(html.Span("T", className="badge bt"))
        if wd.get("has_pres"):
            badges.append(html.Span("P", className="badge bp"))
        if wd.get("has_gr"):
            badges.append(html.Span("GR", className="badge bg"))
        badges.append(html.Span("XY" if has_xy else "NO XY",
                                 className="badge " + ("bxy" if has_xy else "bnxy")))

        well_cards.append(html.Div(className="wi", children=[
            html.Div(wd.get("well", k), className="wi-name"),
            html.Div(
                f"MD {wd.get('strt', 0):.0f}–{wd.get('stop', 0):.0f} {wd.get('dept_unit', 'm')}",
                className="wi-meta",
            ),
            html.Div(badges, className="wi-badges"),
        ]))

    # Dropdown options
    well_options = [{"label": wells[k].get("well", k), "value": k} for k in sorted_keys]

    # X-section options (only wells with coords)
    xs_options = [{"label": f"{wells[k].get('well', k)}", "value": k}
                  for k in sorted_keys if k in coords]

    # Keep current xs selection if still valid
    xs_valid = [v for v in (xs_current or []) if v in {k for k in sorted_keys if k in coords}]

    # Tops count
    n_tops = sum(len(v) for v in tops.values())

    header_txt = f"{len(wells)} wells · {sum(1 for k in wells if k in coords)} with coords"

    return (well_cards, well_options, well_options, well_options,
            xs_options, xs_valid, header_txt, str(len(wells)), str(n_tops))


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — TAB SWITCHING
# ─────────────────────────────────────────────────────────────────

TAB_IDS = ["3d", "log", "xs", "map", "tp"]
TAB_PANEL_IDS = ["tab-3d", "tab-log", "tab-xs", "tab-map", "tab-tp"]


@app.callback(
    Output("tab-3d",  "style"),
    Output("tab-log", "style"),
    Output("tab-xs",  "style"),
    Output("tab-map", "style"),
    Output("tab-tp",  "style"),
    Output("tab-btn-3d",  "className"),
    Output("tab-btn-log", "className"),
    Output("tab-btn-xs",  "className"),
    Output("tab-btn-map", "className"),
    Output("tab-btn-tp",  "className"),
    Output("store-active-tab", "data"),
    Input("tab-btn-3d",  "n_clicks"),
    Input("tab-btn-log", "n_clicks"),
    Input("tab-btn-xs",  "n_clicks"),
    Input("tab-btn-map", "n_clicks"),
    Input("tab-btn-tp",  "n_clicks"),
    State("store-active-tab", "data"),
)
def switch_tab(n3d, nlog, nxs, nmap, ntp, current_tab):
    ctx = callback_context
    if not ctx.triggered:
        active = current_tab or "3d"
    else:
        btn_id = ctx.triggered[0]["prop_id"].split(".")[0]
        mapping = {
            "tab-btn-3d": "3d", "tab-btn-log": "log",
            "tab-btn-xs": "xs", "tab-btn-map": "map", "tab-btn-tp": "tp",
        }
        active = mapping.get(btn_id, current_tab or "3d")

    base_style = {"display": "flex", "flex": "1", "overflow": "hidden", "minHeight": "0"}
    hidden_style = {"display": "none"}

    panel_styles = [base_style if t == active else hidden_style for t in TAB_IDS]
    btn_classes = ["tab-btn active" if t == active else "tab-btn" for t in TAB_IDS]

    return (*panel_styles, *btn_classes, active)


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — 3D MODEL
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("plot-3d", "figure"),
    Input("store-wells", "data"),
    Input("store-coords", "data"),
    Input("store-wellinfo", "data"),
    Input("td-colorby", "value"),
    Input("td-colorscale", "value"),
)
def update_3d(wells, coords, wellinfo, color_by, colorscale):
    wells = wells or {}
    coords = coords or {}
    wellinfo = wellinfo or {}
    if not wells:
        return _empty_fig("Load LAS files and coordinates to view 3D model")
    return build_3d_figure(wells, coords, wellinfo, color_by, colorscale)


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — LOG TRACKS
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("log-tracks-container", "children"),
    Output("log-info", "children"),
    Input("log-well-sel", "value"),
    State("store-wells", "data"),
    State("store-tops", "data"),
)
def update_log_tracks(well_key, wells, tops):
    wells = wells or {}
    tops = tops or {}

    if not well_key or well_key not in wells:
        return (
            html.Div(style={"flex": "1", "display": "flex", "alignItems": "center",
                            "justifyContent": "center"}, children=[
                html.Div(["📊", html.Br(), "Select a well"],
                         style={"color": DARK_SUB, "fontFamily": "IBM Plex Mono",
                                "fontSize": "10px", "textAlign": "center", "opacity": "0.5"}),
            ]),
            "",
        )

    wd = wells[well_key]
    curves = [
        ("temp", "TEMPERATURE  (°C)"),
        ("pres", "PRESSURE  (kPa)"),
        ("gr",   "GAMMA RAY  (GAPI)"),
    ]
    has_flags = {"temp": wd.get("has_temp"), "pres": wd.get("has_pres"), "gr": wd.get("has_gr")}

    tracks = []
    for curve, title in curves:
        has = has_flags.get(curve, False)
        header = html.Div(title, style={
            "background": DARK_PANEL, "padding": "4px 6px",
            "fontFamily": "IBM Plex Mono", "fontSize": "8px", "color": DARK_SUB,
            "borderBottom": f"1px solid {DARK_BORDER}", "textAlign": "center",
            "flexShrink": "0",
        })
        if has:
            fig = build_log_figure(wd, tops, curve, 0)
            plot = dcc.Graph(
                figure=fig,
                config={"displayModeBar": False, "responsive": True},
                style={"flex": "1", "minHeight": "0"},
            )
        else:
            plot = html.Div("No data", style={
                "flex": "1", "display": "flex", "alignItems": "center",
                "justifyContent": "center", "color": DARK_SUB,
                "fontFamily": "IBM Plex Mono", "fontSize": "9px", "opacity": "0.5",
            })

        tracks.append(html.Div(style={
            "flex": "1", "borderRight": f"1px solid {DARK_BORDER}",
            "display": "flex", "flexDirection": "column", "minWidth": "0",
        }, children=[header, plot]))

    info = (f"{wd.get('well', '')}  ·  MD {wd.get('strt', 0):.0f}–"
            f"{wd.get('stop', 0):.0f} {wd.get('dept_unit', 'm')}")

    return html.Div(tracks, style={"display": "flex", "flex": "1",
                                    "overflow": "hidden", "minHeight": "0"}), info


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — X-SECTION
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("plot-xs", "figure"),
    Input("xs-wells", "value"),
    Input("xs-colorby", "value"),
    Input("xs-colorscale", "value"),
    Input("xs-show-tops", "value"),
    State("store-wells", "data"),
    State("store-coords", "data"),
    State("store-tops", "data"),
)
def update_xsection(selected_wells, color_by, colorscale, show_tops_val,
                    wells, coords, tops):
    wells = wells or {}
    coords = coords or {}
    tops = tops or {}
    selected_wells = selected_wells or []
    show_tops = bool(show_tops_val)

    if len(selected_wells) < 2:
        return _empty_fig("Select at least 2 wells in the sidebar to build a cross-section")

    return build_xsection_figure(wells, coords, tops, selected_wells,
                                  color_by, colorscale, show_tops)


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — SURFACE MAP
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("plot-map", "figure"),
    Input("store-wells", "data"),
    Input("store-coords", "data"),
    Input("store-wellinfo", "data"),
    Input("store-tops", "data"),
    Input("mp-curve", "value"),
    Input("mp-colorscale", "value"),
    Input("mp-depth-slider", "value"),
    Input("mp-contours", "value"),
)
def update_surface_map(wells, coords, wellinfo, tops, color_by, colorscale,
                        depth, contours_val):
    wells = wells or {}
    coords = coords or {}
    wellinfo = wellinfo or {}
    tops = tops or {}
    show_contours = bool(contours_val)

    if not wells or not coords:
        return _empty_fig("Load LAS files and coordinates to view surface map")

    return build_surface_map(wells, coords, wellinfo, tops,
                              float(depth), color_by, colorscale, show_contours)


# ─────────────────────────────────────────────────────────────────
#  CALLBACKS — TEMP PATTERN
# ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("store-tp-excluded", "data"),
    Input("tp-select-all", "n_clicks"),
    Input("tp-select-none", "n_clicks"),
    Input({"type": "tp-well-ck", "index": ALL}, "value"),
    State("store-wells", "data"),
    State("store-tp-excluded", "data"),
    prevent_initial_call=True,
)
def update_tp_excluded(n_all, n_none, ck_values, wells, excluded):
    ctx = callback_context
    wells = wells or {}
    excluded = list(excluded or [])

    if not ctx.triggered:
        return excluded

    trigger_id = ctx.triggered[0]["prop_id"]

    if "tp-select-all" in trigger_id:
        return []

    if "tp-select-none" in trigger_id:
        return list(wells.keys())

    # Individual checkbox changed
    all_props = ctx.inputs_list[2]  # list of {id, value} for ALL pattern
    new_excluded = []
    for prop in all_props:
        well_key = prop["id"]["index"]
        checked = prop.get("value") or []
        if not checked:
            new_excluded.append(well_key)
    return new_excluded


@app.callback(
    Output("store-tp-compare", "data"),
    Input("tp-add-cmp", "n_clicks"),
    Input({"type": "tp-remove-cmp", "index": ALL}, "n_clicks"),
    State("tp-cmp-sel", "value"),
    State("store-tp-compare", "data"),
    prevent_initial_call=True,
)
def update_tp_compare(n_add, n_remove_list, selected_well, compare):
    ctx = callback_context
    compare = list(compare or [])

    if not ctx.triggered:
        return compare

    trigger_id = ctx.triggered[0]["prop_id"]

    if "tp-add-cmp" in trigger_id:
        if not selected_well:
            return compare
        if any(c["key"] == selected_well for c in compare):
            return compare
        color = TP_CMP_COLORS[len(compare) % len(TP_CMP_COLORS)]
        compare.append({"key": selected_well, "color": color})
        return compare

    # Remove button
    try:
        prop_info = json.loads(trigger_id.split(".")[0])
        key_to_remove = prop_info.get("index")
        compare = [c for c in compare if c["key"] != key_to_remove]
        # Re-assign colors
        for i, c in enumerate(compare):
            c["color"] = TP_CMP_COLORS[i % len(TP_CMP_COLORS)]
    except Exception:
        pass

    return compare


@app.callback(
    Output("tp-well-list", "children"),
    Output("tp-bl-cnt", "children"),
    Input("store-wells", "data"),
    Input("store-wellinfo", "data"),
    Input("store-tp-excluded", "data"),
)
def update_tp_well_list(wells, wellinfo, excluded):
    wells = wells or {}
    wellinfo = wellinfo or {}
    excluded = set(excluded or [])

    def _nat_sort_key(k):
        parts = re.split(r"(\d+)", str(k))
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    wks = sorted([k for k in wells if wells[k].get("has_temp")], key=_nat_sort_key)
    active_count = sum(1 for k in wks if k not in excluded)

    items = []
    for k in wks:
        wd = wells[k]
        wi = wellinfo.get(k, {})
        is_checked = k not in excluded
        items.append(html.Div(style={
            "display": "flex", "alignItems": "center", "gap": "5px",
            "padding": "3px 6px", "background": DARK_CARD,
            "border": f"1px solid {DARK_ACCENT if is_checked else DARK_BORDER}",
            "borderRadius": "3px", "fontSize": "9px", "fontFamily": "IBM Plex Mono",
            "marginBottom": "2px",
        }, children=[
            dcc.Checklist(
                id={"type": "tp-well-ck", "index": k},
                options=[{"label": "", "value": k}],
                value=[k] if is_checked else [],
                inputStyle={"accentColor": DARK_ACCENT, "cursor": "pointer"},
                style={"display": "inline"},
            ),
            html.Span(wd.get("well", k), style={
                "flex": "1", "overflow": "hidden", "textOverflow": "ellipsis",
                "whiteSpace": "nowrap",
            }),
            html.Span(wi.get("fluid", ""), style={"color": DARK_SUB, "fontSize": "7px"}),
            html.Span(wi.get("type", ""), style={"color": DARK_SUB, "fontSize": "7px",
                                                   "marginLeft": "auto"}),
        ]))

    return items, f"{active_count}/{len(wks)}"


@app.callback(
    Output("tp-cmp-list", "children"),
    Input("store-tp-compare", "data"),
    State("store-wells", "data"),
)
def update_tp_cmp_list(compare, wells):
    compare = compare or []
    wells = wells or {}
    items = []
    for c in compare:
        k = c["key"]
        wd = wells.get(k, {})
        items.append(html.Div(style={
            "display": "flex", "alignItems": "center", "gap": "5px",
            "padding": "4px 6px", "background": DARK_CARD,
            "border": f"1px solid {DARK_BORDER}",
            "borderRadius": "3px", "fontSize": "9px", "fontFamily": "IBM Plex Mono",
            "marginBottom": "3px",
        }, children=[
            html.Div(style={
                "width": "8px", "height": "8px", "borderRadius": "50%",
                "background": c["color"], "flexShrink": "0",
            }),
            html.Span(wd.get("well", k), style={
                "flex": "1", "overflow": "hidden", "textOverflow": "ellipsis",
                "whiteSpace": "nowrap", "margin": "0 4px",
            }),
            html.Button(
                "✕",
                id={"type": "tp-remove-cmp", "index": k},
                n_clicks=0,
                style={"background": "none", "border": "none", "color": DARK_SUB,
                       "cursor": "pointer", "fontSize": "10px", "padding": "0"},
            ),
        ]))
    return items


@app.callback(
    Output("plot-tp", "figure"),
    Input("tp-mode", "value"),
    Input("store-wells", "data"),
    Input("store-wellinfo", "data"),
    Input("store-tp-excluded", "data"),
    Input("store-tp-compare", "data"),
)
def update_tp(mode, wells, wellinfo, excluded, compare):
    wells = wells or {}
    wellinfo = wellinfo or {}
    excluded = excluded or []
    compare = compare or []
    return build_tp_figure(wells, wellinfo, excluded, compare, mode)


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=8050)
