"""Operator screen. Run with:  streamlit run ui/app.py   (or double-click start.bat)

This file only handles the screen. All real work is in pipeline/:
library / sheet / intake (parts and files), agents (AI), run_job (model -> G-code).

Two pages:
  Make a print     - shop workers: find part -> options & size -> preview -> G-code -> SD card
  Library setup    - maintainers: add files for new sheet rows, label multi-file parts
On start-up the Google Sheet is checked once; changes pop up with what to do.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.agents import AgentError, get_agent  # noqa: E402
from pipeline.cad import preview_mesh  # noqa: E402
from pipeline.config import ROOT, settings  # noqa: E402
from pipeline.intake import add_files, save_setup  # noqa: E402
from pipeline.library import (Part, ensure_thumbnails, get_part, load_catalog,  # noqa: E402
                              search, thumb_path)
from pipeline.run_job import JobRequest, format_duration, make_gcode, prepare  # noqa: E402
from pipeline.sheet import apply_update, check_for_updates, sheet_edit_url  # noqa: E402

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


# ---------------------------------------------------------------- selection state helpers

def sel_keys(part: Part):
    return ([f"sel_{part.part_id}_{i}" for i in range(len(part.files))],
            [f"cnt_{part.part_id}_{i}" for i in range(len(part.files))])


def choose(part_id: str, params: dict | None = None, material: str | None = None,
           strength: str | None = None, options: list[str] | None = None) -> None:
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
    sk, ck = sel_keys(part)
    for i, f in enumerate(part.files):
        ss[sk[i]] = f.rel in sel
        ss[ck[i]] = int(sel.get(f.rel, f.count))
    ss[f"one_{part_id}"] = next((f.label for f in part.files if f.rel in sel), part.files[0].label)
    if material in S["materials"]:
        ss.material = material
    if strength in S["strength"]:
        ss.strength = strength


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
                cols[i % 4].image(str(d.path), caption=d.title, use_container_width=True)
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
                cols[i % len(cols)].image(str(t), caption=f.label, use_container_width=True)
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
            c0.image(str(t), use_container_width=True)
        size = f" · {f.size_mm[0]:.0f}×{f.size_mm[1]:.0f}×{f.size_mm[2]:.0f} mm" if f.size_mm else ""
        on = c1.checkbox(f"{f.label}{size}", key=sk[i])
        n = c2.number_input("How many", 1, 50, key=ck[i], disabled=not on, label_visibility="collapsed")
        if on:
            chosen[f.rel] = int(n)
    if not chosen:
        st.warning("Tick at least one piece.")
    return chosen


# ---------------------------------------------------------------- page: make a print

def page_make_print() -> None:
    st.title("Make a print")
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
                        choose(sug.part_id, sug.params, sug.material, sug.strength, sug.options)
                except AgentError as e:
                    ss.suggestion = None
                    st.warning(str(e))
        sug = ss.suggestion
        if sug:
            hit = get_part(parts, sug["part_id"]) if sug["part_id"] else None
            if hit:
                st.success(f"**{hit.name}** — {sug['explanation']}")
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
                    st.markdown('<div class="nopic">no picture</div>', unsafe_allow_html=True)
                st.markdown(f"**{p.name}**")
                extra = f" · {len(p.files)} options" if len(p.files) > 1 else ""
                st.markdown(f'<span class="muted">{p.category} · {p.status}{extra}</span>',
                            unsafe_allow_html=True)
                st.button("Choose", key=f"choose_{p.part_id}", disabled=not p.printable,
                          on_click=choose, args=(p.part_id,), use_container_width=True)

    part = get_part(parts, ss.part_id) if ss.part_id else None
    if not part or not part.printable:
        return

    # ---------------------------------------------------------- 2. options & size
    step(2, f"Choose options and size — {part.name}")
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

    # ---------------------------------------------------------- 3. check
    step(3, "Check the part")
    if st.button("Show preview", type="primary", disabled=not files):
        sug = ss.suggestion if ss.suggestion and ss.suggestion.get("part_id") == part.part_id else None
        req = JobRequest(part.part_id, params, material, strength, files=files,
                         customer_request=sug["text"] if sug else "",
                         ai_suggestion=sug.get("raw") if sug else None)
        with st.spinner("Building the 3D model…"):
            ss.job = prepare(req, parts)

    job = ss.job
    if job is None or job.part is None or job.part.part_id != part.part_id:
        return
    for e in job.errors:
        st.error(e)
    for w in job.warnings:
        st.warning(w)
    if not job.ok:
        return

    fig, x0 = go.Figure(), 0.0
    colors = ["#E07A1F", "#2F7DBA", "#5A9E4B", "#B5487B", "#8A6D3B"]
    for i, pc in enumerate(job.pieces):
        mesh = preview_mesh(pc.model.stl)
        v, f = mesh.vertices.copy(), mesh.faces
        v[:, 0] += x0 - v[:, 0].min()
        x0 = v[:, 0].max() + 10
        fig.add_trace(go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2],
                                color=colors[i % len(colors)], flatshading=True, name=pc.label,
                                showlegend=len(job.pieces) > 1, hoverinfo="name",
                                lighting=dict(ambient=0.45, diffuse=0.8)))
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=0, b=0),
                      scene=dict(aspectmode="data", xaxis_title="mm", yaxis_title="mm", zaxis_title="mm"))
    c1, c2 = st.columns([2, 1])
    c1.plotly_chart(fig, use_container_width=True)
    for pc in job.pieces:
        x, y, z = pc.model.size_mm
        c2.metric(f"{pc.count} × {pc.label}" if len(job.pieces) > 1 or pc.count > 1 else "Width × depth × height",
                  f"{x:.0f} × {y:.0f} × {z:.0f} mm")
    c2.caption("Drag the picture to turn it. If something looks wrong, change step 2 and press "
               "Show preview again.")

    # ---------------------------------------------------------- 4. G-code
    step(4, "Prepare for the printer")
    if not job.sliced:
        if st.button("Make print file", type="primary"):
            with st.spinner("Preparing the print file… this can take a minute"):
                ss.job = make_gcode(job)
            st.rerun()
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Print time", format_duration(job.print_time_s))
    m2.metric("Plastic", f"{job.filament_g:.0f} g" if job.filament_g else "—")
    m3.metric("Plastic cost", f"{job.cost:.0f} {S['currency']}" if job.cost is not None else "—")
    if len(job.plates) > 1:
        st.info(f"Everything doesn't fit on the printer bed at once, so this is **{len(job.plates)} prints**. "
                "Print them one after another.")

    # ---------------------------------------------------------- 5. save
    step(5, "Save to the SD card")
    for i, pl in enumerate(job.plates):
        counts: dict[str, int] = {}
        for lb in pl.labels:
            counts[lb] = counts.get(lb, 0) + 1
        what = ", ".join(f"{n} × {lb}" for lb, n in counts.items())
        title = f"Print {i + 1} of {len(job.plates)}: " if len(job.plates) > 1 else ""
        st.download_button(f"⬇️ {title}Download print file", data=pl.result.gcode.read_bytes(),
                           file_name=pl.result.gcode.name, mime="text/plain", type="primary",
                           key=f"dl_{i}")
        st.caption(f"{what} · {format_duration(pl.result.print_time_s)}")
    if len(job.plates) > 1:
        z = job.zip_all()
        st.download_button("⬇️ Download all print files (zip)", data=z.read_bytes(), file_name=z.name,
                           mime="application/zip")
    st.caption("Save the file onto the printer's SD card, put the card in the printer, and choose it on the "
               f"printer screen. Load {material} filament first.")


# ---------------------------------------------------------------- page: library setup

def page_library_setup() -> None:
    st.title("Library setup")
    st.write("For maintainers. Add files for parts in the Google Sheet, and label parts that "
             "came with several files so shop workers see clear options.")
    c1, c2 = st.columns([1, 1])
    if c1.button("🔄 Check the Google Sheet now", use_container_width=True):
        up = check_for_updates()
        if up.ok and up.has_changes:
            apply_update(up)
            reload_library()
            ss.sheet_update = up
            st.rerun()
        (st.success if up.ok else st.warning)(up.message)
    c2.link_button("Open the Google Sheet ↗", sheet_edit_url(), use_container_width=True)

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
                b.link_button("Open download page ↗", p.link, use_container_width=True)
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
                    df, key=f"ed_{p.part_id}", hide_index=True, use_container_width=True,
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
    page = st.radio("Screen", ["Make a print", "Library setup"], label_visibility="collapsed")
    ready = sum(p.printable for p in parts)
    st.write(f"**{ready}** of {len(parts)} parts ready to print")
    waiting = sum(1 for p in parts if p.commercial_ok and (not p.files or p.needs_setup))
    if waiting:
        st.caption(f"{waiting} part(s) need files or setup → Library setup")
    st.caption(f"Printer: {S['printer']['name']}")
    if ss.get("sheet_msg"):
        st.caption(("" if ss.get("sheet_ok") else "⚠️ ") + ss.sheet_msg)
    if st.button("Start a new order", use_container_width=True):
        keep = {"sheet_checked", "sheet_msg", "sheet_ok", "lib_version"}
        for k in list(ss.keys()):
            if k not in keep:
                del ss[k]
        st.rerun()

if page == "Library setup":
    page_library_setup()
else:
    page_make_print()
