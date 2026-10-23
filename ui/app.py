"""Operator screen. Run with:  streamlit run ui/app.py   (or double-click start.bat)

This file only handles the screen. All real work is in pipeline/ — the UI calls
library.*, agents.get_agent(), run_job.prepare() and run_job.make_gcode().
"""
from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.agents import AgentError, get_agent  # noqa: E402
from pipeline.cad import preview_mesh  # noqa: E402
from pipeline.config import settings  # noqa: E402
from pipeline.library import ensure_thumbnails, get_part, load_catalog, search, sync_from_sheet  # noqa: E402
from pipeline.run_job import JobRequest, format_duration, make_gcode, prepare  # noqa: E402

S = settings()
st.set_page_config(page_title="Hope Arising Print Shop", page_icon="🖨️", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1.5rem; max-width: 1200px;}
  .step {display:flex; align-items:center; gap:.6rem; margin:1.6rem 0 .4rem;}
  .step b {background:#E07A1F; color:#fff; border-radius:50%; width:2rem; height:2rem;
           display:inline-flex; align-items:center; justify-content:center; font-size:1.05rem;}
  .step span {font-size:1.35rem; font-weight:600;}
  .muted {color:#6b7280; font-size:.9rem;}
  div[data-testid="stMetricValue"] {font-size:1.6rem;}
</style>""", unsafe_allow_html=True)


def step(n: int, title: str) -> None:
    st.markdown(f'<div class="step"><b>{n}</b><span>{title}</span></div>', unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading parts library…")
def library(version: int):
    parts = load_catalog()
    ensure_thumbnails(parts)
    return load_catalog()


ss = st.session_state
ss.setdefault("lib_version", 0)
ss.setdefault("part_id", None)
ss.setdefault("job", None)
ss.setdefault("suggestion", None)
parts = library(ss.lib_version)


def choose(part_id: str, params: dict | None = None, material: str | None = None,
           strength: str | None = None) -> None:
    part = get_part(parts, part_id)
    ss.part_id = part_id
    ss.job = None
    vals = {**part.defaults(), **(params or {})}
    for p in part.params:
        ss[f"p_{part_id}_{p.name}"] = float(vals[p.name])
    if material in S["materials"]:
        ss.material = material
    if strength in S["strength"]:
        ss.strength = strength


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Hope Arising Print Shop")
    ready = sum(p.printable for p in parts)
    st.write(f"**{ready}** of {len(parts)} parts ready to print")
    st.caption(f"Printer: {S['printer']['name']}")
    if st.button("🔄 Update library", use_container_width=True):
        ok, msg = sync_from_sheet()
        (st.success if ok else st.warning)(msg)
        if ok:
            ss.lib_version += 1
            st.cache_resource.clear()
            st.rerun()
    if st.button("Start a new order", use_container_width=True):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()

st.title("Make a print")

# ---------------------------------------------------------------- 1. find
step(1, "Find the part")
tab_ai, tab_browse = st.tabs(["✍️ Describe what the customer needs", "📚 Browse parts"])

with tab_ai:
    text = st.text_area("What does the customer need?", height=90,
                        placeholder="Example: a hook for the wall that can hold a heavy bag")
    if st.button("Find part", type="primary"):
        if not text.strip():
            st.warning("Type what the customer needs first.")
        else:
            try:
                with st.spinner("Looking in the library…"):
                    sug = get_agent().suggest(text.strip(), parts)
                ss.suggestion = {"text": text.strip(), **sug.__dict__}
                if sug.part_id and get_part(parts, sug.part_id):
                    choose(sug.part_id, sug.params, sug.material, sug.strength)
            except AgentError as e:
                ss.suggestion = None
                st.warning(str(e))
    sug = ss.suggestion
    if sug:
        if sug["part_id"]:
            st.success(f"**{get_part(parts, sug['part_id']).name}** — {sug['explanation']}")
        else:
            st.info(f"Nothing in the library fits. {sug['explanation']}")
        if sug.get("question"):
            st.info(f"❓ Ask the customer: {sug['question']}")

with tab_browse:
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("Search", placeholder="hook, funnel, holder…", label_visibility="collapsed")
    cats = ["All"] + sorted({p.category for p in parts})
    cat = c2.selectbox("Category", cats, label_visibility="collapsed")
    found = search(parts, q, cat)
    if not found:
        st.write("No parts match.")
    cols = st.columns(4)
    for i, p in enumerate(found):
        with cols[i % 4].container(border=True):
            if p.thumbnail:
                st.image(str(p.thumbnail), use_container_width=True)
            else:
                st.markdown('<div style="height:110px;display:flex;align-items:center;justify-content:center;'
                            'background:#f3f4f6;border-radius:6px;color:#9ca3af">no picture</div>',
                            unsafe_allow_html=True)
            st.markdown(f"**{p.name}**")
            st.markdown(f'<span class="muted">{p.category} · {p.status}</span>', unsafe_allow_html=True)
            st.button("Choose", key=f"choose_{p.part_id}", disabled=not p.printable,
                      on_click=choose, args=(p.part_id,), use_container_width=True)

part = get_part(parts, ss.part_id) if ss.part_id else None
if not part:
    st.stop()

# ---------------------------------------------------------------- 2. size
step(2, f"Set the size — {part.name}")
left, right = st.columns([2, 1])
with left:
    params = {}
    for p in part.params:
        key = f"p_{part.part_id}_{p.name}"
        ss.setdefault(key, float(p.default))
        params[p.name] = st.number_input(
            f"{p.label} ({p.unit})", min_value=float(p.min), max_value=float(p.max),
            step=float(p.step), key=key, help=p.help or None)
    if part.kind == "mesh" and part.base_size_mm:
        k = params.get("scale_percent", 100) / 100
        x, y, z = (d * k for d in part.base_size_mm)
        st.markdown(f"Finished size: **{x:.0f} × {y:.0f} × {z:.0f} mm** (width × depth × height)")
with right:
    material = st.radio("Material", list(S["materials"]), key="material", horizontal=True)
    st.caption(S["materials"][material].get("note", ""))
    strength = st.radio("Strength", list(S["strength"]), key="strength", horizontal=True)
    st.caption(S["strength"][strength].get("note", ""))

# ---------------------------------------------------------------- 3. check
step(3, "Check the part")
if st.button("Show preview", type="primary"):
    sug = ss.suggestion if ss.suggestion and ss.suggestion.get("part_id") == part.part_id else None
    req = JobRequest(part.part_id, params, material, strength,
                     customer_request=sug["text"] if sug else "",
                     ai_suggestion=sug.get("raw") if sug else None)
    with st.spinner("Building the 3D model…"):
        ss.job = prepare(req, parts)

job = ss.job
if job is None or job.part is None or job.part.part_id != part.part_id:
    st.stop()
for e in job.errors:
    st.error(e)
for w in job.warnings:
    st.warning(w)
if not job.ok:
    st.stop()

mesh = preview_mesh(job.model.stl)
v, f = mesh.vertices, mesh.faces
fig = go.Figure(go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                          color="#E07A1F", flatshading=True,
                          lighting=dict(ambient=0.45, diffuse=0.8), hoverinfo="skip"))
fig.update_layout(height=380, margin=dict(l=0, r=0, t=0, b=0),
                  scene=dict(aspectmode="data", xaxis_title="mm", yaxis_title="mm", zaxis_title="mm"))
c1, c2 = st.columns([2, 1])
c1.plotly_chart(fig, use_container_width=True)
x, y, z = job.model.size_mm
c2.metric("Width × depth × height", f"{x:.0f} × {y:.0f} × {z:.0f} mm")
c2.caption("Drag the picture to turn it. If the size or shape looks wrong, change step 2 and press "
           "Show preview again.")

# ---------------------------------------------------------------- 4. G-code
step(4, "Prepare for the printer")
if job.slice is None:
    if st.button("Make print file", type="primary"):
        with st.spinner("Preparing the print file… this can take a minute"):
            ss.job = make_gcode(job)
        st.rerun()
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Print time", format_duration(job.slice.print_time_s))
m2.metric("Plastic", f"{job.slice.filament_g:.0f} g" if job.slice.filament_g else "—")
m3.metric("Plastic cost", f"{job.cost:.0f} {S['currency']}" if job.cost is not None else "—")

# ---------------------------------------------------------------- 5. save
step(5, "Save to the SD card")
st.download_button("⬇️ Download print file", data=job.slice.gcode.read_bytes(),
                   file_name=job.gcode_name, mime="text/plain", type="primary")
st.caption("Save the file onto the printer's SD card, put the card in the printer, and choose it on the "
           f"printer screen. Load {material} filament first.")
