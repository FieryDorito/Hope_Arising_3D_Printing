"""Operator screen. Run with:  streamlit run ui/app.py   (or double-click start.bat)

This file only handles the screen. All real work is in pipeline/:
library / sheet / intake (parts and files), agents (AI), run_job (model -> G-code).

Three pages:
  Make a print     - shop workers, one step per screen: find part -> options & size ->
                     check (preview) -> save to SD card / USB (the sidebar lists the steps)
  Past prints      - every finished print file: reprint, change and reprint, customer + status
  Library setup    - maintainers: add files for new sheet rows, label multi-file parts
On start-up the Google Sheet is checked once; changes pop up with what to do.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go_
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.agents import AgentError, get_agent  # noqa: E402
from pipeline.cad import preview_mesh  # noqa: E402
from pipeline.config import ROOT, settings  # noqa: E402
from pipeline.drives import DriveError, copy_to_drive, list_removable  # noqa: E402
from pipeline.history import (DEFAULT_STATUS, STATUSES, PastJob, list_jobs, load_job,  # noqa: E402
                              save_order)
from pipeline.intake import add_files, save_setup  # noqa: E402
from pipeline.netprint import NetPrintError, configured_printers, send  # noqa: E402
from pipeline.library import (Part, ensure_thumbnails, get_part, load_catalog,  # noqa: E402
                              search, thumb_path)
from pipeline.run_job import JobRequest, format_duration, make_gcode, prepare  # noqa: E402
from pipeline.sheet import apply_update, check_for_updates, sheet_edit_url  # noqa: E402

S = settings()
PAGES = ["Make a print", "Past prints", "Library setup"]
STEPS = ["Find the part", "Options and size", "Check the part", "Save and print"]
KEEP_PREFIXES = ("p_", "sel_", "cnt_", "one_")      # the order's settings (see below)
HELP_FILE = ROOT / "ui" / "help" / "how_to_use.md"
st.set_page_config(page_title="Hope Arising Print Shop", page_icon="🖨️", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1.5rem; max-width: 1200px;}
  .step {display:flex; align-items:center; gap:.6rem; margin:1.6rem 0 .4rem;}
  .step b {background:#E07A1F; color:#fff; border-radius:50%; width:2rem; height:2rem;
           display:inline-flex; align-items:center; justify-content:center; font-size:1.05rem;}
  .step span {font-size:1.35rem; font-weight:600;}
  .muted {color:#6b7280; font-size:.9rem;}
  .nopic {height:110px; display:flex; align-items:center; justify-content:center;
          background:#f3f4f6; border-radius:6px; color:#9ca3af;}
  div[data-testid="stMetricValue"] {font-size:1.6rem;}
</style>""", unsafe_allow_html=True)


def step(n: int, title: str) -> None:
    st.markdown(f'<div class="step"><b>{n}</b><span>{title}</span></div>', unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading parts library…")
def library(version: int):
    parts = load_catalog()
    ensure_thumbnails(parts)
    return parts


ss = st.session_state
ss.setdefault("lib_version", 0)
ss.setdefault("part_id", None)
ss.setdefault("job", None)
ss.setdefault("suggestion", None)


def reload_library() -> None:
    ss.lib_version += 1
    st.cache_resource.clear()


def rel_folder(p: Part) -> str:
    try:
        return str(p.folder.relative_to(ROOT))
    except ValueError:
        return str(p.folder)


# ---------------------------------------------------------------- start-up sheet check
if "sheet_checked" not in ss:
    ss.sheet_checked = True
    up = check_for_updates()
    ss.sheet_msg = up.message
    ss.sheet_ok = up.ok
    if up.ok and up.has_changes:
        apply_update(up)
        reload_library()
        ss.sheet_update = up

parts = library(ss.lib_version)


def upload_box(part: Part, key: str) -> None:
    """Drag-and-drop area that saves files straight into the part's folder."""
    files = st.file_uploader(
        f"Drag the downloaded files for **{part.name}** here (STL, 3MF, ZIP, PDF, TXT, pictures)",
        accept_multiple_files=True, key=f"up_{key}_{part.part_id}")
    if files and st.button("Save these files", key=f"save_{key}_{part.part_id}", type="primary"):
        msgs = add_files(part.folder, [(f.name, f.getvalue()) for f in files])
        ss.flash = f"{part.name}: " + " ".join(msgs)
        reload_library()
        st.rerun()


@st.dialog("Parts library was updated", width="large")
def update_dialog(up) -> None:
    st.write("The Google Sheet changed since this computer last checked. Here's what to do:")
    if up.new:
        st.markdown(f"#### {len(up.new)} new part(s)")
        for row in up.new:
            part = get_part(parts, row["part_id"])
            with st.container(border=True):
                st.markdown(f"**{row['part_name']}** · {row.get('category', '')} · "
                            f"{row.get('license', '') or 'license not filled in'}")
                if part and not part.commercial_ok:
                    st.info("License does not allow selling it, so no files are needed.")
                    continue
                st.markdown("1. Open the download page and download **all** files (a .zip is fine).  \n"
                            "2. Drag them into the box below and press **Save these files**.")
                if row.get("link"):
                    st.link_button("Open download page ↗", row["link"])
                if part and part.files:
                    st.success(f"Files already added ({len(part.files)} model files).")
                elif part:
                    upload_box(part, "dlg")
    if up.changed:
        st.markdown(f"#### {len(up.changed)} changed part(s)")
        for row, fields in up.changed:
            st.markdown(f"- **{row['part_name']}**: {', '.join(f.replace('_', ' ') for f in fields)} changed"
                        + (" — check the download page in case the files changed too." if "link" in fields else ""))
    if up.removed:
        st.markdown(f"#### {len(up.removed)} part(s) removed from the sheet")
        st.markdown("\n".join(f"- {r['part_name']}" for r in up.removed))
        st.caption("Their files are still on this computer, but they no longer appear in the app.")
    st.caption("You can do this later from **Library setup** in the side bar. After adding files, "
               "commit and push them in GitHub Desktop so the other computers get them.")
    if st.button("Done", type="primary"):
        ss.pop("sheet_update", None)
        st.rerun()


if ss.get("sheet_update"):
    update_dialog(ss.sheet_update)


@st.dialog("How to use", width="large")
def how_to_use() -> None:
    try:
        st.markdown(HELP_FILE.read_text(encoding="utf-8"))
    except OSError:
        st.write("The help file is missing (ui/help/how_to_use.md).")


# ---------------------------------------------------------------- keep the order's settings between steps
# Streamlit forgets a box's value when the box is not on screen. Each step is its own
# screen, so the order's settings are saved again at the start of every run.
for _k in list(ss.keys()):
    if _k in ("material", "strength") or str(_k).startswith(KEEP_PREFIXES):
        ss[_k] = ss[_k]
ss.setdefault("material", next(iter(S["materials"])))
ss.setdefault("strength", next(iter(S["strength"])))
ss.setdefault("step", 1)
ss.setdefault("page", PAGES[0])


def go(n: int) -> None:
    ss.step = n


def new_order() -> None:
    keep = {"sheet_checked", "sheet_msg", "sheet_ok", "lib_version"}
    for k in list(ss.keys()):
        if k not in keep:
            del ss[k]


# ---------------------------------------------------------------- selection state helpers

def sel_keys(part: Part):
    return ([f"sel_{part.part_id}_{i}" for i in range(len(part.files))],
            [f"cnt_{part.part_id}_{i}" for i in range(len(part.files))])


def choose(part_id: str, params: dict | None = None, material: str | None = None,
           strength: str | None = None, options: list[str] | None = None,
           files: dict | None = None) -> None:
    """Load a part (and optionally earlier settings) into the order, then go to step 2."""
    part = get_part(parts, part_id)
    ss.part_id = part_id
    ss.job = None
    vals = {**part.defaults(), **{k: v for k, v in (params or {}).items() if part.param(k)}}
    for p in part.params:
        ss[f"p_{part_id}_{p.name}"] = float(min(max(vals[p.name], p.min), p.max))
    sel = part.default_selection()
    picked = [part.file_by_label(o) for o in (options or [])]
    picked = [f for f in picked if f]
    if picked:
        sel = {f.rel: f.count for f in (picked[:1] if part.choose == "one" else picked)}
    if files:
        known = {r: int(n) for r, n in files.items() if part.file(r)}
        sel = known or sel
    sk, ck = sel_keys(part)
    for i, f in enumerate(part.files):
        ss[sk[i]] = f.rel in sel
        ss[ck[i]] = int(sel.get(f.rel, f.count))
    ss[f"one_{part_id}"] = next((f.label for f in part.files if f.rel in sel), part.files[0].label)
    if material in S["materials"]:
        ss.material = material
    if strength in S["strength"]:
        ss.strength = strength
    ss.step = 2


def files_from_state(part: Part) -> dict[str, int]:
    sk, ck = sel_keys(part)
    for i, f in enumerate(part.files):
        ss.setdefault(sk[i], f.rel in part.default_selection())
        ss.setdefault(ck[i], f.count)
    if len(part.files) == 1:
        return {part.files[0].rel: int(ss[ck[0]])}
    if part.choose == "one":
        f = part.file_by_label(ss.get(f"one_{part.part_id}", "")) or part.files[0]
        return {f.rel: int(ss[ck[part.files.index(f)]])}
    return {f.rel: int(ss[ck[i]]) for i, f in enumerate(part.files) if ss[sk[i]]}


def params_from_state(part: Part) -> dict:
    return {p.name: ss.get(f"p_{part.part_id}_{p.name}", float(p.default)) for p in part.params}


def current_request(part: Part) -> JobRequest:
    sug = ss.suggestion if ss.suggestion and ss.suggestion.get("part_id") == part.part_id else None
    return JobRequest(part.part_id, params_from_state(part), ss.material, ss.strength,
                      files=files_from_state(part), customer_request=sug["text"] if sug else "",
                      ai_suggestion=sug.get("raw") if sug else None)


def req_sig(req: JobRequest) -> str:
    return json.dumps([req.part_id, req.params, req.material, req.strength, req.files],
                      sort_keys=True, default=str)


def fresh_job(part: Part | None):
    """The prepared job, but only if nothing in step 2 changed since it was made."""
    job = ss.get("job")
    if not part or job is None or job.part is None or job.part.part_id != part.part_id:
        return None
    return job if ss.get("job_sig") == req_sig(current_request(part)) else None


def reachable(n: int, part: Part | None) -> bool:
    if n == 1:
        return True
    if not part or not part.printable:
        return False
    if n == 2:
        return True
    job = fresh_job(part)
    if n == 3:
        return bool(job and ss.get("prep_ok"))
    return bool(job and job.ok and job.sliced)


def show_docs(part: Part) -> None:
    if not part.docs:
        return
    with st.expander(f"📄 Instructions and pictures from the designer ({len(part.docs)})",
                     expanded=bool(part.instructions)):
        for d in part.instructions:
            if d.kind == "text":
                st.markdown(f"**{d.title}**")
                try:
                    text = d.path.read_text(encoding="utf-8", errors="replace")[:20000]
                except OSError:
                    text = "(could not open this file)"
                if d.path.suffix.lower() == ".md":
                    st.markdown(text)
                else:
                    st.text(text)
            elif d.kind == "pdf":
                st.download_button(f"📕 Open {d.path.name}", d.path.read_bytes(), file_name=d.path.name,
                                   mime="application/pdf", key=f"pdf_{part.part_id}_{d.path}")
        if part.images:
            cols = st.columns(4)
            for i, d in enumerate(part.images):
                cols[i % 4].image(str(d.path), caption=d.title, width="stretch")
        if part.link:
            st.markdown(f"[Original page ↗]({part.link})")


def param_input(part: Part, p):
    key = f"p_{part.part_id}_{p.name}"
    ss.setdefault(key, float(p.default))
    if p.kind == "choice":
        values = [float(v) for v, _ in p.choices]
        if ss[key] not in values:
            ss[key] = values[0]
        return st.selectbox(p.label, values, key=key, format_func=p.choice_label, help=p.help or None)
    unit = f" ({p.unit})" if p.unit else ""
    return st.number_input(f"{p.label}{unit}", min_value=float(p.min), max_value=float(p.max),
                           step=float(p.step), key=key, help=p.help or None)


def options_picker(part: Part) -> dict[str, int]:
    """Show the part's files as choices; return {rel: count}."""
    sk, ck = sel_keys(part)
    for i, f in enumerate(part.files):          # first visit without choose()
        ss.setdefault(sk[i], f.rel in part.default_selection())
        ss.setdefault(ck[i], f.count)
    if len(part.files) == 1:
        n = st.number_input("How many", 1, 50, key=ck[0])
        return {part.files[0].rel: int(n)}

    chosen: dict[str, int] = {}
    if part.choose == "one":
        st.markdown("**Which version?**")
        labels = [f.label for f in part.files]
        ss.setdefault(f"one_{part.part_id}", labels[0])
        cols = st.columns(min(len(labels), 4))
        for i, f in enumerate(part.files):
            t = thumb_path(part, f)
            if t.exists():
                cols[i % len(cols)].image(str(t), caption=f.label, width="stretch")
        label = st.radio("Version", labels, key=f"one_{part.part_id}", horizontal=True,
                         label_visibility="collapsed")
        f = part.file_by_label(label)
        n = st.number_input("How many", 1, 50, key=ck[part.files.index(f)])
        return {f.rel: int(n)}

    st.markdown("**Which pieces?** Tick each piece to print and how many.")
    for i, f in enumerate(part.files):
        c0, c1, c2 = st.columns([1, 4, 2])
        t = thumb_path(part, f)
        if t.exists():
            c0.image(str(t), width="stretch")
        size = f" · {f.size_mm[0]:.0f}×{f.size_mm[1]:.0f}×{f.size_mm[2]:.0f} mm" if f.size_mm else ""
        on = c1.checkbox(f"{f.label}{size}", key=sk[i])
        n = c2.number_input("How many", 1, 50, key=ck[i], disabled=not on, label_visibility="collapsed")
        if on:
            chosen[f.rel] = int(n)
    if not chosen:
        st.warning("Tick at least one piece.")
    return chosen


def settings_text(part: Part | None, params: dict) -> str:
    """'Pipe diameter: 25 mm · Strap width: 15 mm' (main sizes only)."""
    bits, hidden = [], 0
    for k, v in params.items():
        p = part.param(k) if part else None
        if p and not p.basic:
            hidden += 1
            continue
        if p and p.kind == "choice":
            bits.append(f"{p.label}: {p.choice_label(v)}")
        elif p:
            bits.append(f"{p.label}: {float(v):g}{' ' + p.unit if p.unit else ''}")
        else:
            bits.append(f"{k}: {v}")
    if hidden:
        bits.append(f"+ {hidden} other setting{'s' if hidden > 1 else ''}")
    return " · ".join(bits) or "Standard size"


# ---------------------------------------------------------------- ways to get the file to the printer

def save_to_card(files: list[Path], key: str) -> None:
    """Write the print files straight onto an SD card / USB stick plugged into this computer."""
    drives = list_removable()
    if not drives:
        st.info("💾 Put the SD card (or USB stick) into this computer, then press **Look again**.")
        st.button("🔄 Look again", key=f"look_{key}")
        return
    if len(drives) == 1:
        d = drives[0]
    else:
        names = [x.name for x in drives]
        pick = st.radio("Which card?", names, key=f"drv_{key}", horizontal=True)
        d = drives[names.index(pick)]
    what = "the print file" if len(files) == 1 else f"all {len(files)} print files"
    if st.button(f"💾 Save {what} to {d.name}", type="primary", key=f"card_{key}"):
        try:
            with st.spinner("Saving to the card…"):
                written = copy_to_drive(files, d.path)
            ss[f"saved_{key}"] = (f"Saved to {d.name}: " + ", ".join(w.name for w in written)
                                  + ". You can take the card out now and put it in the printer.")
        except DriveError as e:
            ss.pop(f"saved_{key}", None)
            st.error(str(e))
    if ss.get(f"saved_{key}"):
        st.success(ss[f"saved_{key}"])


def send_wifi(files: list[Path], key: str) -> None:
    printers = configured_printers()
    if not printers:
        st.button("📡 Send to a Wi-Fi printer", key=f"wifi_{key}", disabled=True,
                  help="Coming later. A maintainer can add Wi-Fi printers in config/settings.yaml.")
        st.caption("No Wi-Fi printer is set up yet. Ender 3 printers use the SD card.")
        return
    names = [p.name for p in printers]
    pick = st.selectbox("Printer", names, key=f"wifi_pick_{key}")
    start = st.checkbox("Start printing straight away", key=f"wifi_start_{key}",
                        help="Only if the bed is clear and the right filament is loaded.")
    if st.button("📡 Send to the printer", key=f"wifi_{key}"):
        pr = printers[names.index(pick)]
        try:
            with st.spinner(f"Sending to {pr.name}…"):
                for i, f in enumerate(files):
                    send(pr, f, start=start and i == 0)
            st.success(f"Sent to {pr.name}." + (" It is starting now." if start else
                                               " Choose the file on the printer screen."))
        except NetPrintError as e:
            st.error(str(e))
    st.caption("Experimental: not yet tried on a real printer.")


def download_buttons(files: list[Path], key: str) -> None:
    for i, f in enumerate(files):
        st.download_button(f"⬇️ Download {f.name}", data=f.read_bytes(), file_name=f.name,
                           mime="text/plain", key=f"dl_{key}_{i}")


# ---------------------------------------------------------------- page: make a print

def title_row(title: str) -> None:
    a, b = st.columns([5, 1])
    a.title(title)
    if b.button("❓ How to use", key=f"help_{title}", width="stretch"):
        how_to_use()


def step1() -> None:
    step(1, "Find the part")
    part = get_part(parts, ss.part_id) if ss.part_id else None
    if part and part.printable:
        c1, c2 = st.columns([3, 1])
        c1.info(f"This order: **{part.name}**. Choose another part below, or carry on.")
        c2.button("Continue →", on_click=go, args=(2,), type="primary", width="stretch")

    tab_ai, tab_browse = st.tabs(["✍️ Describe what the customer needs", "📚 Browse parts"])
    with tab_ai:
        text = st.text_area("What does the customer need?", height=90,
                            placeholder="Example: a cap for the end of a 25 mm pipe")
        if st.button("Find part", type="primary"):
            if not text.strip():
                st.warning("Type what the customer needs first.")
            else:
                try:
                    with st.spinner("Looking in the library…"):
                        sug = get_agent().suggest(text.strip(), parts)
                    ss.suggestion = {"text": text.strip(), **sug.__dict__}
                    if sug.part_id and get_part(parts, sug.part_id):
                        choose(sug.part_id, sug.params, sug.material, sug.strength, sug.options)
                        st.rerun()
                except AgentError as e:
                    ss.suggestion = None
                    st.warning(str(e))
        sug = ss.suggestion
        if sug and not (sug["part_id"] and get_part(parts, sug["part_id"])):
            st.info(f"Nothing in the library fits. {sug['explanation']}")
            st.caption("Try describing it another way, or look under Browse parts.")

    with tab_browse:
        c1, c2 = st.columns([3, 1])
        q = c1.text_input("Search", placeholder="hook, pipe, knob…", label_visibility="collapsed")
        cats = ["All"] + sorted({p.category for p in parts})
        cat = c2.selectbox("Category", cats, label_visibility="collapsed")
        found = search(parts, q, cat)
        if not found:
            st.write("No parts match.")
        cols = st.columns(4)
        for i, p in enumerate(found):
            with cols[i % 4].container(border=True):
                if p.thumbnail:
                    st.image(str(p.thumbnail), width="stretch")
                else:
                    st.markdown('<div class="nopic">no picture</div>', unsafe_allow_html=True)
                st.markdown(f"**{p.name}**")
                extra = f" · {len(p.files)} options" if len(p.files) > 1 else ""
                st.markdown(f'<span class="muted">{p.category} · {p.status}{extra}</span>',
                            unsafe_allow_html=True)
                st.button("Choose", key=f"choose_{p.part_id}", disabled=not p.printable,
                          on_click=choose, args=(p.part_id,), width="stretch")


def step2(part: Part) -> None:
    step(2, f"Options and size — {part.name}")
    sug = ss.suggestion if ss.suggestion and ss.suggestion.get("part_id") == part.part_id else None
    if sug:
        st.success(f"**{part.name}** — {sug['explanation']}")
        if sug.get("question"):
            st.warning(f"❓ **Ask the customer:** {sug['question']}")
    if part.description:
        st.caption(part.description)
    show_docs(part)
    left, right = st.columns([2, 1])
    with left:
        files = options_picker(part)
        params = {}
        basic = [p for p in part.params if p.basic]
        more = [p for p in part.params if not p.basic]
        for p in basic:
            params[p.name] = param_input(part, p)
        if more:
            with st.expander(f"More settings ({len(more)})"):
                st.caption("The designer's other settings. The starting values are usually right.")
                for p in more:
                    params[p.name] = param_input(part, p)
        k = params.get("scale_percent", 100) / 100
        sizes = [(part.file(r).label, part.file(r).size_mm) for r in files if part.file(r).size_mm]
        for label, (x, y, z) in sizes:
            name = f"{label}: " if len(part.files) > 1 else ""
            st.markdown(f"{name}finished size **{x * k:.0f} × {y * k:.0f} × {z * k:.0f} mm** "
                        "(width × depth × height)")
    with right:
        material = st.radio("Material", list(S["materials"]), key="material", horizontal=True)
        st.caption(S["materials"][material].get("note", ""))
        strength = st.radio("Strength", list(S["strength"]), key="strength", horizontal=True)
        st.caption(S["strength"][strength].get("note", ""))

    req = current_request(part)
    job = ss.get("job")
    if job is not None and ss.get("job_sig") == req_sig(req) and not ss.get("prep_ok"):
        for e in job.errors:
            st.error(e)

    st.divider()
    c1, c2 = st.columns(2)
    c1.button("← Choose a different part", on_click=go, args=(1,), width="stretch")
    if c2.button("Next: check the part →", type="primary", disabled=not files, width="stretch"):
        with st.spinner("Building the 3D model…"):
            job = prepare(req, parts)
        ss.job, ss.job_sig, ss.prep_ok = job, req_sig(req), job.ok
        ss.slice_errors = None
        if job.ok:
            ss.step = 3
        st.rerun()


def step3(part: Part) -> None:
    job = fresh_job(part)
    step(3, f"Check the part — {part.name}")
    for w in job.warnings:
        st.warning(w)
    fig, x0 = go_.Figure(), 0.0
    colors = ["#E07A1F", "#2F7DBA", "#5A9E4B", "#B5487B", "#8A6D3B"]
    for i, pc in enumerate(job.pieces):
        mesh = preview_mesh(pc.model.stl)
        v, f = mesh.vertices.copy(), mesh.faces
        v[:, 0] += x0 - v[:, 0].min()
        x0 = v[:, 0].max() + 10
        fig.add_trace(go_.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                                 color=colors[i % len(colors)], flatshading=True, name=pc.label,
                                 showlegend=len(job.pieces) > 1, hoverinfo="name",
                                 lighting=dict(ambient=0.45, diffuse=0.8)))
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=0, b=0),
                      scene=dict(aspectmode="data", xaxis_title="mm", yaxis_title="mm", zaxis_title="mm"))
    c1, c2 = st.columns([2, 1])
    c1.plotly_chart(fig, width="stretch")
    for pc in job.pieces:
        x, y, z = pc.model.size_mm
        c2.metric(f"{pc.count} × {pc.label}" if len(job.pieces) > 1 or pc.count > 1 else "Width × depth × height",
                  f"{x:.0f} × {y:.0f} × {z:.0f} mm")
    c2.markdown(f"**{ss.material}** · {ss.strength}")
    c2.caption("Drag the picture to turn it. Check the sizes match what the customer needs.")
    for e in ss.get("slice_errors") or []:
        st.error(e)

    st.divider()
    c1, c2 = st.columns(2)
    c1.button("← Change settings", on_click=go, args=(2,), width="stretch")
    if c2.button("Next: make print file →", type="primary", width="stretch"):
        with st.spinner("Preparing the print file… this can take a minute"):
            job = make_gcode(job)
        if job.ok and job.sliced:
            ss.slice_errors = None
            ss.step = 4
        else:
            ss.slice_errors = job.errors[:]
            job.errors.clear()          # so the worker can try again
            job.plates = []
        ss.job = job
        st.rerun()


def order_form(folder: Path, key: str, with_status: bool) -> None:
    """Customer name, phone, notes (and status) saved next to the job."""
    pj = load_job(folder)
    order = pj.order if pj else {}
    with st.form(f"order_{key}", border=False):
        a, b = st.columns(2)
        name = a.text_input("Customer name", order.get("customer", ""), key=f"{key}_name")
        phone = b.text_input("Phone", order.get("phone", ""), key=f"{key}_phone")
        status = None
        if with_status:
            cur = order.get("status") or DEFAULT_STATUS
            status = st.selectbox("Status", STATUSES, index=STATUSES.index(cur) if cur in STATUSES else 0,
                                  key=f"{key}_status")
        notes = st.text_area("Notes", order.get("notes", ""), height=68, key=f"{key}_notes",
                             placeholder="Example: fits well / too tight, try 0.3 mm bigger")
        if st.form_submit_button("Save details"):
            save_order(folder, customer=name, phone=phone, notes=notes, status=status)
            st.success("Saved.")


def step4(part: Part) -> None:
    job = fresh_job(part)
    step(4, f"Save and print — {part.name}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Print time", format_duration(job.print_time_s))
    m2.metric("Plastic", f"{job.filament_g:.0f} g" if job.filament_g else "—")
    m3.metric("Plastic cost", f"{job.cost:.0f} {S['currency']}" if job.cost is not None else "—")
    if job.print_time_s and job.print_time_s > 2 * 3600:
        st.warning("This is a long print. Make sure the generator is ready in case the power goes off.")
    gcodes = [pl.result.gcode for pl in job.plates]
    if len(job.plates) > 1:
        st.info(f"Everything doesn't fit on the printer bed at once, so this is **{len(job.plates)} prints**. "
                "Print them one after another.")
        for i, pl in enumerate(job.plates):
            counts: dict[str, int] = {}
            for lb in pl.labels:
                counts[lb] = counts.get(lb, 0) + 1
            st.caption(f"Print {i + 1}: " + ", ".join(f"{n} × {lb}" for lb, n in counts.items())
                       + f" · {format_duration(pl.result.print_time_s)}")

    st.markdown("#### Who is this for? (optional)")
    order_form(job.folder, f"new_{job.folder.name}", with_status=False)

    st.markdown("#### Send it to the printer")
    save_to_card(gcodes, f"new_{job.folder.name}")
    st.caption(f"Load **{ss.material}** filament before you start the print.")
    with st.expander("Other ways to get the file"):
        send_wifi(gcodes, f"new_{job.folder.name}")
        download_buttons(gcodes, f"new_{job.folder.name}")

    st.divider()
    c1, c2 = st.columns(2)
    c1.button("← Back to the preview", on_click=go, args=(3,), width="stretch")
    c2.button("✅ Start the next order", on_click=new_order, type="primary", width="stretch")
    st.caption("This print is saved under **Past prints**, so you can print it again later.")


def page_make_print() -> None:
    title_row("Make a print")
    part = get_part(parts, ss.part_id) if ss.part_id else None
    if not reachable(ss.step, part):
        ss.step = max(n for n in range(1, len(STEPS) + 1) if reachable(n, part))
    st.progress(ss.step / len(STEPS), text=f"Step {ss.step} of {len(STEPS)}: {STEPS[ss.step - 1]}")
    {1: lambda: step1(), 2: lambda: step2(part), 3: lambda: step3(part), 4: lambda: step4(part)}[ss.step]()


# ---------------------------------------------------------------- page: past prints

STATUS_ICON = {"Waiting to print": "⏳", "Printing": "🖨️", "Done": "✅", "Picked up": "📦", "Failed": "❌"}


def reopen(j: PastJob) -> None:
    choose(j.part_id, j.params, j.material, j.strength, files=j.files)
    ss.page = PAGES[0]


def page_past_prints() -> None:
    title_row("Past prints")
    jobs = list_jobs()
    if not jobs:
        st.info("No past prints yet. Every print file you make appears here.")
        return
    counts = {s: sum(j.status == s for j in jobs) for s in STATUSES}
    st.caption(" · ".join(f"{STATUS_ICON[s]} {n} {s.lower()}" for s, n in counts.items() if n))
    c1, c2 = st.columns([3, 2])
    q = c1.text_input("Search", placeholder="customer name, phone or part", label_visibility="collapsed")
    status = c2.selectbox("Status", ["All"] + STATUSES, label_visibility="collapsed")

    def name_of(j: PastJob) -> str:
        p = get_part(parts, j.part_id)
        return p.name if p else j.part_id

    ql = q.strip().lower()
    shown = [j for j in jobs if (status == "All" or j.status == status)
             and (not ql or ql in f"{name_of(j)} {j.customer} {j.phone} {j.notes}".lower())]
    if not shown:
        st.write("Nothing matches.")
    ss.setdefault("past_limit", 20)
    for j in shown[:ss.past_limit]:
        part = get_part(parts, j.part_id)
        who = j.customer or "no name"
        with st.expander(f"{STATUS_ICON.get(j.status, '')} **{name_of(j)}** · {who} · "
                         f"{j.saved_at:%d %b %Y, %H:%M}"):
            a, b = st.columns([3, 2])
            with a:
                st.markdown(settings_text(part, j.params))
                st.markdown(f"{j.material} · {j.strength} · {format_duration(j.print_time_s)}"
                            + (f" · {j.cost:.0f} {S['currency']} plastic" if j.cost is not None else ""))
                if j.customer_request:
                    st.caption(f"Customer asked for: “{j.customer_request}”")
                k = j.folder.name
                save_to_card(j.gcodes, f"past_{k}")
                c1, c2 = st.columns(2)
                c1.button("✏️ Change and print again", key=f"reopen_{k}", on_click=reopen, args=(j,),
                          disabled=not (part and part.printable), width="stretch",
                          help="Opens this order at step 2 with the same settings, so you can fix the size.")
                with c2.popover("More", width="stretch"):
                    send_wifi(j.gcodes, f"past_{k}")
                    download_buttons(j.gcodes, f"past_{k}")
            with b:
                order_form(j.folder, f"past_{j.folder.name}", with_status=True)
    if len(shown) > ss.past_limit:
        if st.button(f"Show more ({len(shown) - ss.past_limit} older)"):
            ss.past_limit += 20
            st.rerun()


# ---------------------------------------------------------------- page: library setup

def page_library_setup() -> None:
    st.title("Library setup")
    st.write("For maintainers. Add files for parts in the Google Sheet, and label parts that "
             "came with several files so shop workers see clear options.")
    c1, c2 = st.columns([1, 1])
    if c1.button("🔄 Check the Google Sheet now", width="stretch"):
        up = check_for_updates()
        if up.ok and up.has_changes:
            apply_update(up)
            reload_library()
            ss.sheet_update = up
            st.rerun()
        (st.success if up.ok else st.warning)(up.message)
    c2.link_button("Open the Google Sheet ↗", sheet_edit_url(), width="stretch")
    if st.button("🤖 Test AI connection"):
        import time as _t

        t0 = _t.monotonic()
        try:
            with st.spinner("Asking the AI a test question…"):
                agent = get_agent()
                sug = agent.suggest("Test: a hook to hang a bag on the wall", parts)
            st.success(f"The AI answered in {_t.monotonic() - t0:.0f} s"
                       + (f" (it picked: {get_part(parts, sug.part_id).name})" if sug.part_id else "") + ".")
        except AgentError as e:
            st.error(str(e))
            st.caption("Technical details are saved in jobs/ai_errors.log.")

    if ss.get("flash"):
        st.success(ss.pop("flash"))

    sellable = [p for p in parts if p.commercial_ok]
    need_files = [p for p in sellable if not p.files]
    need_setup = [p for p in sellable if p.needs_setup]
    groups = {
        f"Needs files ({len(need_files)})": need_files,
        f"Several files — choose options ({len(need_setup)})": need_setup,
        f"Ready ({sum(p.printable and not p.needs_setup for p in parts)})":
            [p for p in parts if p.printable and not p.needs_setup],
        f"All ({len(parts)})": parts,
    }
    pick = st.radio("Show", list(groups), horizontal=True, label_visibility="collapsed")
    shown = groups[pick]
    if not shown:
        st.success("Nothing here. 🎉")

    for p in shown:
        badge = ("🚫 not for sale" if not p.commercial_ok else "📥 needs files" if not p.files
                 else "🧩 choose options" if p.needs_setup else "✅ ready")
        with st.expander(f"{p.name} — {badge}", expanded=len(shown) <= 3):
            a, b = st.columns([2, 1])
            a.markdown(f"{p.category} · {p.license or 'license not filled in'} · by {p.author or '?'}")
            a.caption(f"Folder on this computer: {rel_folder(p)}")
            if p.link:
                b.link_button("Open download page ↗", p.link, width="stretch")
            if not p.commercial_ok:
                st.info("The license does not allow selling this part, so it is hidden from shop workers.")
                continue

            if p.sources and not p.files:
                st.warning(f"Found {', '.join(x.name for x in p.sources)} — the app can't print from "
                           f"{p.sources[0].suffix[1:].upper()} files. Download the STL from the page above, or "
                           "open the file in its CAD program and export an STL, then add it here.")
            upload_box(p, "lib")

            if p.files:
                st.markdown("**How shop workers see this part's files**")
                choose_mode = st.radio(
                    "The files are…", ["any", "one"], key=f"mode_{p.part_id}",
                    index=0 if p.choose == "any" else 1, horizontal=True,
                    format_func=lambda m: ("pieces of one product — worker ticks which to print"
                                           if m == "any" else "different versions — worker picks one"))
                df = pd.DataFrame([{"File": f.rel, "Name shown to workers": f.label,
                                    "Selected at start": f.default, "How many": f.count, "Hide": False}
                                   for f in p.files])
                edited = st.data_editor(
                    df, key=f"ed_{p.part_id}", hide_index=True, width="stretch",
                    disabled=["File"],
                    column_config={"How many": st.column_config.NumberColumn(min_value=1, max_value=50, step=1)})
                desc = st.text_area("Short description (helps the AI choose this part)", p.description,
                                    key=f"desc_{p.part_id}", height=70)
                if st.button("Save setup", key=f"setup_{p.part_id}", type="primary"):
                    rows = edited.to_dict("records")
                    save_setup(p.folder, choose_mode, {
                        r["File"]: {"label": r["Name shown to workers"], "default": bool(r["Selected at start"]),
                                    "count": int(r["How many"] or 1), "hide": bool(r["Hide"])} for r in rows
                    }, description=desc)
                    ss.flash = f"Saved setup for {p.name}."
                    reload_library()
                    st.rerun()
                if p.docs:
                    st.caption("Instructions/pictures found: " + ", ".join(d.path.name for d in p.docs))

    st.divider()
    st.caption("Files you add here are saved in the project's models folder on this computer. "
               "To share them with the team and the shop, open GitHub Desktop, write a short note, "
               "click Commit, then Push.")


# ---------------------------------------------------------------- sidebar + routing
with st.sidebar:
    st.subheader("Hope Arising Print Shop")
    if st.button("❓ How to use", key="help_side", width="stretch"):
        how_to_use()
    page = st.radio("Screen", PAGES, key="page", label_visibility="collapsed")
    if page == PAGES[0]:
        st.markdown("**Steps**")
        _part = get_part(parts, ss.part_id) if ss.part_id else None
        for _n, _title in enumerate(STEPS, 1):
            _ok = reachable(_n, _part)
            _done = _ok and _n < ss.step
            st.button(f"{'✅' if _done else _n}  {_title}", key=f"nav_{_n}", on_click=go, args=(_n,),
                      disabled=not _ok, type="primary" if _n == ss.step else "secondary",
                      width="stretch")
        st.button("Start a new order", on_click=new_order, width="stretch")
    st.divider()
    ready = sum(p.printable for p in parts)
    st.write(f"**{ready}** of {len(parts)} parts ready to print")
    waiting = sum(1 for p in parts if p.commercial_ok and (not p.files or p.needs_setup))
    if waiting:
        st.caption(f"{waiting} part(s) need files or setup → Library setup")
    st.caption(f"Printer: {S['printer']['name']}")
    if ss.get("sheet_msg"):
        st.caption(("" if ss.get("sheet_ok") else "⚠️ ") + ss.sheet_msg)

if page == "Library setup":
    page_library_setup()
elif page == "Past prints":
    page_past_prints()
else:
    page_make_print()
