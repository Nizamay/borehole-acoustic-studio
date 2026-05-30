"""Streamlit entry — run:  streamlit run app.py
   v2.3 — mirrors LOGGING PROCEDURE CALCULATOR 2.3.html features
"""
from __future__ import annotations

import io
import json
from copy import deepcopy
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from logging_procedure.chart import plot_all_runs, plot_result
from logging_procedure.core import (
    CONDITIONS,
    RUN_COLORS,
    calc_run,
    format_ls_time,
    min_to_dhm,
)

PAGE_TITLE = "Logging Procedure Calculator v2.3"

ALL_RUNS_SENTINEL = "__ALL_RUNS__"

# ---------------------------------------------------------------------------
# Default row / state helpers
# ---------------------------------------------------------------------------

def _default_row() -> Dict[str, Any]:
    return {
        "condition": CONDITIONS[0],
        "direction": "down",
        "top": 0.0,
        "bottom": 2000.0,
        "mode": "cont",
        "contSpeed": 30.0,
        "stInterval": 30.0,
        "stDuration": 45.0,
        "stSpeed": 20.0,
        "mainTool": "HPT",
    }


def _init_state() -> None:
    if "project" in st.session_state:
        return
    st.session_state["project"] = {
        "version": 2,
        "units": "emp",
        "compress_y": True,
        "well_name": "",
        "runs": [
            {
                "name": "RUN-1",
                "color": RUN_COLORS[0],
                "rows": [_default_row()],
            }
        ],
        "active_run": 0,
        "results": {},   # run_index -> CalcResult
        "last_title": "",
    }


def _active_run() -> Dict[str, Any]:
    p = st.session_state["project"]
    return p["runs"][p["active_run"]]


def _renumber_auto_run_names(runs: List[Dict[str, Any]]) -> None:
    import re
    pat = re.compile(r"^RUN-0*\d+$", re.I)
    for i, r in enumerate(runs):
        n = str(r.get("name", "")).strip()
        if pat.match(n):
            r["name"] = f"RUN-{i + 1}"


def _rows_to_calc_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, rec in df.iterrows():
        d = {k: rec[k] for k in rec.index}
        for key in ("top", "bottom", "contSpeed", "stInterval", "stDuration", "stSpeed"):
            if key in d and pd.isna(d[key]):
                d[key] = ""
        rows.append(d)
    return rows


def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Per-run stats expander
# ---------------------------------------------------------------------------

def _show_run_stats(result, units: str, run_name: str) -> None:
    du = "ft" if units == "emp" else "m"
    segs = result.segments
    depths = [0.0] + [s.top for s in segs] + [s.bottom for s in segs]
    tdep = max(depths)

    with st.expander(f"Stats — {run_name}", expanded=False):
        cA, cB, cC, cD = st.columns(4)
        cA.metric("Max depth", f"{tdep:.0f} {du}")
        cB.metric("Segments", str(len(segs)))
        cC.metric("Total time", f"{result.total_time:.1f} min  ({result.total_time / 60:.2f} h)")
        cD.metric("Duration DHM", min_to_dhm(result.total_time))
        st.metric("Stations", str(result.total_stations))

        # Per-segment breakdown table
        rows_data = []
        t_acc = 0.0
        for i, seg in enumerate(segs):
            kind = "Long Station" if seg.is_long_st else (
                "Continuous" if seg.row.get("mode", "cont") == "cont" else "Station"
            )
            label_lines = []
            if seg.is_long_st:
                label_lines.append(format_ls_time(seg.seg_time))
            cond = str(seg.row.get("condition", ""))
            rows_data.append({
                "#": i + 1,
                "Kind": kind,
                "Condition": cond,
                f"Top ({du})": f"{seg.top:.0f}",
                f"Bot ({du})": f"{seg.bottom:.0f}",
                "Time (min)": f"{seg.seg_time:.2f}",
                "T-start": f"{t_acc:.2f}",
                "Stations": seg.num_st if not seg.is_long_st else "-",
            })
            t_acc += seg.seg_time

        st.dataframe(pd.DataFrame(rows_data), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    _init_state()
    proj = st.session_state["project"]

    st.title(PAGE_TITLE)
    st.caption("Logging Procedure Calculator v2.3 — mirrors LOGGING PROCEDURE CALCULATOR 2.3.html")

    # ── Global settings row ──────────────────────────────────────────────────
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        units = st.selectbox(
            "Units",
            options=["emp", "si"],
            format_func=lambda u: "ft / min" if u == "emp" else "m / min",
            index=0 if proj["units"] == "emp" else 1,
        )
        proj["units"] = units
    with c2:
        proj["compress_y"] = st.checkbox("Compress Y (transit zones)", value=proj.get("compress_y", True))
    with c3:
        proj["well_name"] = st.text_input("Well name", value=proj.get("well_name", ""))

    st.divider()

    # ── Run selector row ─────────────────────────────────────────────────────
    runs: List[Dict[str, Any]] = proj["runs"]
    rc1, rc2, rc3, rc4, rc5 = st.columns([2, 1, 1, 1, 1])
    with rc1:
        run_options = [(str(i), r["name"]) for i, r in enumerate(runs)] + [(ALL_RUNS_SENTINEL, "All Runs")]
        sel_key = st.selectbox(
            "Run",
            options=[k for k, _ in run_options],
            format_func=lambda k: dict(run_options).get(k, k),
            index=min(proj["active_run"], len(runs) - 1),
        )
        if sel_key == ALL_RUNS_SENTINEL:
            proj["active_run"] = ALL_RUNS_SENTINEL
        else:
            proj["active_run"] = int(sel_key)

    with rc2:
        new_name = st.text_input("New run name", value="", placeholder="RUN-2")
    with rc3:
        if st.button("+ Run") and new_name.strip():
            runs.append(
                {
                    "name": new_name.strip(),
                    "color": RUN_COLORS[len(runs) % len(RUN_COLORS)],
                    "rows": [_default_row()],
                }
            )
            proj["active_run"] = len(runs) - 1
            st.rerun()
    with rc4:
        can_delete = len(runs) > 1 and proj["active_run"] != ALL_RUNS_SENTINEL
        if st.button("Delete run", disabled=not can_delete):
            idx_del = int(proj["active_run"])
            runs.pop(idx_del)
            # Remove cached result
            proj["results"].pop(idx_del, None)
            # Re-index results dict
            new_results: Dict[int, Any] = {}
            for k, v in proj["results"].items():
                nk = k if k < idx_del else k - 1
                new_results[nk] = v
            proj["results"] = new_results
            _renumber_auto_run_names(runs)
            proj["active_run"] = min(idx_del, len(runs) - 1)
            st.rerun()
    with rc5:
        # Run colour picker (only when a single run is selected)
        if proj["active_run"] != ALL_RUNS_SENTINEL:
            run_obj = runs[int(proj["active_run"])]
            new_color = st.color_picker(
                "Run colour",
                value=run_obj.get("color", RUN_COLORS[0]),
                key=f"color_picker_{proj['active_run']}",
            )
            run_obj["color"] = new_color

    # ── All-Runs view ────────────────────────────────────────────────────────
    if proj["active_run"] == ALL_RUNS_SENTINEL:
        st.subheader("All Runs — combined chart")
        if st.button("▶ Calculate All Runs & Plot", type="primary"):
            for ri, r in enumerate(runs):
                rows_list = r.get("rows", [])
                df_tmp = pd.DataFrame(rows_list) if rows_list else pd.DataFrame([_default_row()])
                calc_rows = _rows_to_calc_list(df_tmp)
                proj["results"][ri] = calc_run(calc_rows)
            wn = (proj.get("well_name") or "").strip()
            all_names = [r["name"] for r in runs]
            proj["last_title"] = "All Runs" + (f" — {wn}" if wn else "")
            st.session_state["_all_runs_fig_cache"] = None  # invalidate

        # Draw chart if results available
        stored_results = [proj["results"].get(ri) for ri in range(len(runs))]
        if any(r is not None for r in stored_results):
            valid_pairs = [(r, runs[ri]) for ri, r in enumerate(stored_results) if r is not None]
            all_res = [p[0] for p in valid_pairs]
            all_names = [p[1]["name"] for p in valid_pairs]
            all_colors = [p[1].get("color", RUN_COLORS[i % len(RUN_COLORS)]) for i, p in enumerate(valid_pairs)]
            wn = (proj.get("well_name") or "").strip()
            chart_title = "All Runs" + (f" — {wn}" if wn else "")

            fig_all = st.session_state.get("_all_runs_fig_cache")
            if fig_all is None:
                fig_all = plot_all_runs(
                    all_res,
                    all_names,
                    all_colors,
                    units=proj["units"],
                    compress_y=proj["compress_y"],
                    title=chart_title,
                )
                st.session_state["_all_runs_fig_cache"] = fig_all

            st.pyplot(fig_all)

            # PNG export
            png_bytes = _fig_to_png_bytes(fig_all)
            slug = (proj.get("well_name") or "project").replace(" ", "_")[:40]
            st.download_button(
                "↓ Export chart as PNG",
                data=png_bytes,
                file_name=f"LoggingProcedure_AllRuns_{slug}_{date.today()}.png",
                mime="image/png",
            )

            # Per-run stats expanders
            for r, robj in valid_pairs:
                _show_run_stats(r, proj["units"], robj["name"])
        else:
            st.info("Click 'Calculate All Runs & Plot' to generate the combined chart.")

        _render_json_controls(proj, runs)
        return

    # ── Single-run view ──────────────────────────────────────────────────────
    run_idx = int(proj["active_run"])
    run = runs[run_idx]

    direction_opts = ["down", "up", "longst", "rih", "pooh"]
    mode_opts = ["cont", "stat"]

    df = pd.DataFrame(run["rows"])
    if df.empty:
        df = pd.DataFrame([_default_row()])
        run["rows"] = df.to_dict("records")

    st.subheader(f"Segments — {run['name']}")
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "condition": st.column_config.SelectboxColumn("Condition", options=CONDITIONS, required=True),
            "direction": st.column_config.SelectboxColumn(
                "Direction",
                options=direction_opts,
                required=True,
            ),
            "top": st.column_config.NumberColumn("Top", format="%.2f"),
            "bottom": st.column_config.NumberColumn("Bottom", format="%.2f"),
            "mode": st.column_config.SelectboxColumn("Mode", options=mode_opts, required=True),
            "contSpeed": st.column_config.NumberColumn("Cont spd", format="%.2f"),
            "stInterval": st.column_config.NumberColumn("St intvl", format="%.2f"),
            "stDuration": st.column_config.NumberColumn("St dur (s)", format="%.2f"),
            "stSpeed": st.column_config.NumberColumn("St spd", format="%.2f"),
            "mainTool": st.column_config.SelectboxColumn(
                "Tool",
                options=["HPT", "NOISE", "EM", "PLT", "PNL", "CEM", "DAS-DTS"],
                required=True,
            ),
        },
        hide_index=True,
    )

    run["rows"] = edited.to_dict("records")

    bc1, bc2, bc3 = st.columns([1, 1, 2])
    with bc1:
        calc = st.button("▶ Calculate & plot", type="primary")
    with bc2:
        uploaded = st.file_uploader("Load JSON", type=["json"], key="proj_upload")

    # Handle JSON upload
    if uploaded is not None:
        fid = f"{uploaded.name}:{uploaded.size}"
        if st.session_state.get("_upload_fingerprint") != fid:
            st.session_state["_upload_fingerprint"] = fid
            try:
                data = json.loads(uploaded.read().decode("utf-8"))
                if not data.get("runs"):
                    st.error("Invalid project: no runs")
                else:
                    proj["units"] = data.get("units", "emp")
                    proj["runs"] = []
                    for ri, sr in enumerate(data["runs"]):
                        proj["runs"].append(
                            {
                                "name": sr.get("name") or f"RUN-{ri + 1}",
                                "color": sr.get("color") or RUN_COLORS[ri % len(RUN_COLORS)],
                                "rows": [dict(r) for r in sr.get("rows", [])],
                            }
                        )
                    proj["active_run"] = 0
                    proj["results"] = {}
                    st.session_state.pop("_fig_cache", None)
                    st.session_state.pop("_all_runs_fig_cache", None)
                    # Note: calloutOverrides from HTML are silently ignored (drag is HTML-only)
                    st.success(f"Loaded {len(proj['runs'])} run(s). Click Calculate.")
                    st.rerun()
            except Exception as e:
                st.error(str(e))

    if calc:
        calc_rows = _rows_to_calc_list(edited)
        res = calc_run(calc_rows)
        proj["results"][run_idx] = res
        wn = (proj.get("well_name") or "").strip()
        proj["last_title"] = f"{run['name']}" + (f" — {wn}" if wn else "")
        st.session_state["_fig_cache"] = plot_result(
            res,
            units=proj["units"],
            compress_y=proj["compress_y"],
            title=proj["last_title"],
            run_color=run.get("color", RUN_COLORS[0]),
        )
        # Invalidate all-runs cache
        st.session_state.pop("_all_runs_fig_cache", None)

    res = proj["results"].get(run_idx)
    if res is not None:
        fig = st.session_state.get("_fig_cache")
        if fig is None:
            wn = (proj.get("well_name") or "").strip()
            chart_title = f"{run['name']}" + (f" — {wn}" if wn else "")
            fig = plot_result(
                res,
                units=proj["units"],
                compress_y=proj["compress_y"],
                title=chart_title,
                run_color=run.get("color", RUN_COLORS[0]),
            )
        st.pyplot(fig)

        # PNG export
        png_bytes = _fig_to_png_bytes(fig)
        slug = (proj.get("well_name") or "project").replace(" ", "_")[:40]
        st.download_button(
            "↓ Export chart as PNG",
            data=png_bytes,
            file_name=f"LoggingProcedure_{run['name']}_{slug}_{date.today()}.png",
            mime="image/png",
            key="png_export_single",
        )

        _show_run_stats(res, proj["units"], run["name"])

    _render_json_controls(proj, runs)


def _render_json_controls(proj: Dict[str, Any], runs: List[Dict[str, Any]]) -> None:
    """Render the Save project JSON download button."""
    out = {
        "version": 2,
        "savedAt": date.today().isoformat(),
        "units": proj["units"],
        "well_name": proj.get("well_name", ""),
        "runs": [
            {"name": r["name"], "color": r["color"], "rows": deepcopy(r["rows"])}
            for r in runs
        ],
    }
    slug = (proj.get("well_name") or "project").replace(" ", "_")[:60]
    st.download_button(
        "↓ Save project JSON",
        data=json.dumps(out, indent=2, ensure_ascii=False),
        file_name=f"LoggingProcedure_{slug}_{date.today()}.json",
        mime="application/json",
        key="json_save_btn",
    )


if __name__ == "__main__":
    main()
