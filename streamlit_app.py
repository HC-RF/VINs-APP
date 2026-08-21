"""Streamlit frontend for the VIN decoder.

Streamlit Community Cloud runs `streamlit run <script>`, not an ASGI server, so
the FastAPI app in `app/main.py` cannot be deployed there directly. It does not
need to be: the whole decoding stack - providers, merge engine, confidence
system, caching, exports - is an ordinary Python library. This module calls
`DecodeService` in-process, with no HTTP hop in between.

Both frontends therefore share one backend and behave identically:

    streamlit run streamlit_app.py          # this file
    uvicorn app.main:app                    # the FastAPI app + SPA
"""

from __future__ import annotations

import asyncio
import io
import os
import threading

import pandas as pd
import streamlit as st

# --- Secrets bridge ---------------------------------------------------------
# Streamlit Cloud injects configuration through st.secrets; the settings object
# reads the environment. Bridge one to the other BEFORE anything imports config.
# `setdefault` means a real environment variable always wins.
try:
    for _key, _value in st.secrets.items():
        if isinstance(_value, (str, int, float, bool)):
            os.environ.setdefault(_key.upper(), str(_value))
except Exception:  # noqa: BLE001 - no secrets.toml configured is the normal case
    pass

st.set_page_config(
    page_title="VIN Decoder",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"about": "VIN decoder with per-field sources and confidence."},
)

from app.config import get_settings  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.base import init_db, session_scope  # noqa: E402
from app.providers.registry import get_registry  # noqa: E402
from app.schemas.vehicle import VehicleRecord  # noqa: E402
from app.services.compare_service import build_comparison  # noqa: E402
from app.services.decode_service import DecodeService  # noqa: E402
from app.services.export_service import export_filename, to_csv, to_xlsx  # noqa: E402
from app.vin.validate import parse_vin_list  # noqa: E402

SAMPLE_VINS = [
    "WA1ANAFY5J2213924",
    "WBXHT3C38J5K23394",
    "5UXKR0C56JL070851",
    "WBA2J3C53JVA52449",
    "WBA5R7C59KAE82587",
    "WBA4J1C58JBG77203",
]

NOT_AVAILABLE = "Not available"

CONFIDENCE_COLOURS = {
    "HIGH": ("#d9f5e9", "#067a54"),
    "MEDIUM": ("#fdf0d5", "#9a6100"),
    "LOW": ("#fdeadb", "#b04a08"),
    "UNKNOWN": ("#eceff5", "#6b7488"),
}


# --- Async plumbing ---------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _event_loop() -> asyncio.AbstractEventLoop:
    """One long-lived event loop for the whole process.

    `asyncio.run` would create and tear down a loop per call, which would
    invalidate the pooled httpx clients the providers hold open. Keeping a
    single background loop alive lets connection reuse actually work.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="vin-decoder-loop").start()
    return loop


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result()


@st.cache_resource(show_spinner=False)
def get_service() -> DecodeService:
    init_db()
    return DecodeService()


# --- Rendering helpers ------------------------------------------------------

def badge(text: str, bg: str, fg: str) -> str:
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:5px;"
        f"font-size:0.7rem;font-weight:700;letter-spacing:.04em;white-space:nowrap;'>"
        f"{text}</span>"
    )


def confidence_badge(level: str | None) -> str:
    bg, fg = CONFIDENCE_COLOURS.get(level or "UNKNOWN", CONFIDENCE_COLOURS["UNKNOWN"])
    return badge(level or "UNKNOWN", bg, fg)


def origin_badge(origin: str | None) -> str:
    """The distinction that matters most: read from the VIN, or looked up."""
    if origin == "VIN_DECODED":
        return badge("VIN", "#eae6fd", "#4c3ab8")
    if origin == "ENRICHED":
        return badge("DB", "#dcf1f7", "#0e6f8a")
    return ""


def field_value(record: VehicleRecord, name: str, suffix: str = "") -> str:
    field = record.fields.get(name)
    if field is None or field.value is None:
        return NOT_AVAILABLE
    return f"{field.value}{suffix}"


def vehicle_title(record: VehicleRecord) -> str:
    parts = [record.year, record.make, record.model]
    title = " ".join(str(p) for p in parts if p)
    return title or record.vin


# --- Session state ----------------------------------------------------------

def init_state() -> None:
    st.session_state.setdefault("records", {})       # vin -> VehicleRecord
    st.session_state.setdefault("last_batch", [])
    st.session_state.setdefault("summary", None)
    st.session_state.setdefault("vin_text", "")
    st.session_state.setdefault("compare_selection", [])


def all_records() -> list[VehicleRecord]:
    return list(st.session_state["records"].values())


# --- Sidebar ----------------------------------------------------------------

def render_sidebar() -> tuple[bool, bool]:
    settings = get_settings()
    registry = get_registry()

    st.sidebar.title("🚗 VIN Decoder")
    st.sidebar.caption("Every field carries its source and confidence.")

    st.sidebar.subheader("Options")
    refresh = st.sidebar.toggle(
        "Force refresh", value=False,
        help="Ignore cached results and query providers again.",
    )
    verify = st.sidebar.toggle(
        "Cross-verify", value=False,
        help="Query every configured provider to cross-check fields. "
             "Costs money only if a commercial key is configured.",
    )

    st.sidebar.subheader("Providers")
    for provider in registry.all:
        info = provider.info()
        icon = "🟢" if info.available else "⚪"
        cost = "free" if info.cost_per_call <= 0 else f"${info.cost_per_call:.3f}/call"
        st.sidebar.markdown(f"{icon} **{info.label}** · {cost}")
        if info.unavailable_reason:
            st.sidebar.caption(info.unavailable_reason)

    try:
        with session_scope() as session:
            usage = repo.usage_summary(session, days=30)
        st.sidebar.subheader("Usage (30 days)")
        col1, col2 = st.sidebar.columns(2)
        col1.metric("Lookups", usage["total_lookups"])
        col2.metric("Cache hits", f"{round(usage['cache_hit_rate'] * 100)}%")
        st.sidebar.metric("API spend", f"${usage['total_cost']:.2f}")
    except Exception:  # noqa: BLE001 - a stats panel must never break the app
        pass

    st.sidebar.caption(
        f"Database: {'SQLite' if settings.using_sqlite else 'PostgreSQL'} · "
        f"cache TTL {settings.cache_ttl_hours}h"
    )
    if settings.using_sqlite:
        st.sidebar.caption(
            "⚠️ On Streamlit Cloud the SQLite cache is ephemeral and resets when "
            "the app restarts. Set `DATABASE_URL` to a managed PostgreSQL "
            "instance to make it durable."
        )
    return refresh, verify


# --- Decode tab -------------------------------------------------------------

def _load_samples() -> None:
    st.session_state["vin_text"] = "\n".join(SAMPLE_VINS)


def _clear_input() -> None:
    st.session_state["vin_text"] = ""
    st.session_state["last_batch"] = []
    st.session_state["summary"] = None


def render_decode(refresh: bool, verify: bool) -> None:
    st.subheader("Decode a VIN")
    st.caption("Enter one VIN, or paste a list — one per line.")

    col_input, col_actions = st.columns([4, 1])
    with col_input:
        text = st.text_area(
            "Vehicle Identification Numbers",
            key="vin_text",
            height=130,
            placeholder="5UXKR0C56JL070851",
            label_visibility="collapsed",
        )
    with col_actions:
        # These must be on_click callbacks, not `if st.button(...)` bodies:
        # session state backing a widget cannot be reassigned after that widget
        # has been instantiated in the same run. Callbacks run before the rerun,
        # so the assignment is legal there.
        st.button("Load samples", on_click=_load_samples, use_container_width=True)
        st.button("Clear", on_click=_clear_input, use_container_width=True)

    settings = get_settings()
    vins, duplicates = parse_vin_list(text or "", limit=settings.max_vins_per_request + 1)

    # Live input feedback, before anything is sent anywhere.
    if vins:
        too_many = len(vins) > settings.max_vins_per_request
        bad = [v for v in vins if len(v) != 17 or any(c in v for c in "IOQ")]
        cols = st.columns(3)
        cols[0].metric("VINs entered", len(vins))
        cols[1].metric("Look valid", len(vins) - len(bad))
        cols[2].metric("Duplicates removed", len(duplicates))
        if bad:
            st.warning(
                "These will be rejected before any provider is called: "
                + ", ".join(f"`{v}`" for v in bad[:5])
                + ("…" if len(bad) > 5 else "")
            )
        if too_many:
            st.error(
                f"{len(vins)} VINs exceeds the limit of "
                f"{settings.max_vins_per_request} per request."
            )

    disabled = not vins or len(vins) > settings.max_vins_per_request
    label = f"Decode {len(vins)} VINs" if len(vins) > 1 else "Decode VIN"
    if st.button(label, type="primary", disabled=disabled):
        with st.spinner("Contacting providers — free sources first…"):
            response = run_async(
                get_service().decode_many(
                    vins, refresh=refresh, verify=verify, duplicates=duplicates
                )
            )
        for record in response.results:
            st.session_state["records"][record.vin] = record
        st.session_state["last_batch"] = [r.vin for r in response.results]
        st.session_state["summary"] = response.summary

    summary = st.session_state.get("summary")
    if summary:
        render_summary(summary)

    for vin in st.session_state.get("last_batch", []):
        record = st.session_state["records"].get(vin)
        if record is not None:
            render_vehicle(record)


def render_summary(summary) -> None:
    cols = st.columns(6)
    cols[0].metric("Requested", summary.requested)
    cols[1].metric("Decoded", summary.decoded)
    cols[2].metric("Invalid", summary.invalid)
    cols[3].metric("From cache", summary.from_cache)
    cols[4].metric("Discrepancies", summary.discrepancy_count)
    cols[5].metric("API cost", f"${summary.total_cost:.2f}")
    st.caption(
        f"{summary.provider_calls} provider call(s) in {summary.elapsed_ms} ms."
        + (f" Duplicates removed: {', '.join(summary.duplicates_removed)}."
           if summary.duplicates_removed else "")
    )


HEADLINE = [
    ("year", "Year", ""),
    ("make", "Make", ""),
    ("model", "Model", ""),
    ("trim", "Trim", ""),
    ("engine_displacement_l", "Engine", " L"),
    ("engine_cylinders", "Cylinders", ""),
    ("horsepower", "Horsepower", " hp"),
    ("fuel", "Fuel", ""),
    ("drivetrain", "Drivetrain", ""),
    ("transmission", "Transmission", ""),
    ("body_type", "Body", ""),
    ("plant_country", "Country", ""),
]


def render_vehicle(record: VehicleRecord) -> None:
    with st.container(border=True):
        overall = record.confidence.get("overall", "UNKNOWN")
        flags = [confidence_badge(overall) if record.valid else badge("INVALID", "#fee4e2", "#b42318")]
        if record.cached:
            flags.append(badge("CACHED", "#e6edfe", "#2563eb"))
        if record.status.value == "PARTIAL":
            flags.append(badge("PARTIAL", "#fdf0d5", "#9a6100"))
        if record.check_digit_valid is False:
            flags.append(badge("CHECK DIGIT FAILED", "#fdf0d5", "#9a6100"))

        st.markdown(
            f"### {vehicle_title(record)} &nbsp; {' '.join(flags)}",
            unsafe_allow_html=True,
        )
        st.caption(f"`{record.vin}`")

        if not record.valid:
            for err in record.errors:
                st.error(f"**{err.get('code', 'ERROR')}** — {err.get('message', '')}")
            st.caption("No provider was queried for this VIN.")
            return

        # Discrepancies get the most prominent treatment on the card.
        for d in record.discrepancies:
            rivals = " · ".join(
                f"**{c.value}** ({c.source}, {c.confidence.value})" for c in d.conflicting
            )
            body = (
                f"**Data discrepancy detected — {d.label}**\n\n"
                f"Showing **{d.selected_value}** from *{d.selected_source}*. "
                f"Also reported: {rivals}.\n\n"
                f"The higher-confidence value is shown; every value is retained."
            )
            (st.error if d.severity == "critical" else st.warning)(body)

        for warning in record.warnings:
            if not warning.startswith("Data discrepancy detected"):
                st.info(warning)

        # Headline grid.
        for row_start in range(0, len(HEADLINE), 6):
            cols = st.columns(6)
            for col, (name, label, suffix) in zip(cols, HEADLINE[row_start:row_start + 6]):
                field = record.fields.get(name)
                value = field_value(record, name, suffix)
                with col:
                    st.markdown(
                        f"<div style='font-size:.65rem;letter-spacing:.07em;"
                        f"text-transform:uppercase;opacity:.6'>{label}</div>"
                        f"<div style='font-size:1.05rem;font-weight:600;"
                        f"{'opacity:.45;font-style:italic' if value == NOT_AVAILABLE else ''}'>"
                        f"{value}</div>",
                        unsafe_allow_html=True,
                    )
                    if field is not None and field.value is not None:
                        chips = confidence_badge(field.confidence.value) + " " + origin_badge(
                            field.origin.value if field.origin else None
                        )
                        if field.disputed:
                            chips += " " + badge("CONFLICT", "#fdf0d5", "#9a6100")
                        st.markdown(chips, unsafe_allow_html=True)

        with st.expander("All specifications, sources and provider calls"):
            rows = []
            for name, field in sorted(record.fields.items()):
                if field.value is None:
                    continue
                alternatives = [a for a in field.alternatives if a.value != field.value]
                rows.append({
                    "Field": field.label or name,
                    "Value": field.value,
                    "Source": field.source,
                    "Confidence": field.confidence.value,
                    "Origin": "VIN" if field.origin and field.origin.value == "VIN_DECODED" else "DB",
                    "Disputed": "Yes" if field.disputed else "",
                    "Also reported": "; ".join(
                        f"{a.value} ({a.source})" for a in alternatives
                    ),
                    "Note": field.note or "",
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.markdown("**Provider calls**")
            calls = [{
                "Provider": c.provider,
                "Result": "ok" if c.success else "failed",
                "Fields": c.fields_returned,
                "Latency (ms)": c.latency_ms,
                "Cost": f"${c.cost:.3f}" if c.cost else "free",
                "Error": c.error or "",
            } for c in record.provider_calls]
            if calls:
                st.dataframe(pd.DataFrame(calls), use_container_width=True, hide_index=True)
            if record.cached:
                age = record.cache_age_seconds
                st.caption(
                    "Served from cache"
                    + (f" ({round(age / 60)} min old)" if age is not None else "")
                    + "; no provider was contacted. Enable **Force refresh** to re-query."
                )


# --- Bulk tab ---------------------------------------------------------------

def records_dataframe(records: list[VehicleRecord]) -> pd.DataFrame:
    return pd.DataFrame([{
        "VIN": r.vin,
        "Year": r.year,
        "Make": r.make,
        "Model": r.model,
        "Trim": r.trim,
        "Engine (L)": r.engine.displacement_l,
        "Cyl": r.engine.cylinders,
        "HP": r.horsepower,
        "Fuel": r.fuel,
        "Drive": r.drivetrain,
        "Transmission": r.transmission,
        "MPG": r.mpg_combined,
        "Body": r.body_type,
        "Confidence": r.confidence.get("overall", "UNKNOWN"),
        "Conflicts": len(r.discrepancies),
        "Cached": r.cached,
    } for r in records])


def render_bulk() -> None:
    records = all_records()
    st.subheader("Bulk results")
    if not records:
        st.info("Decode some VINs first — results accumulate here for sorting and export.")
        return

    frame = records_dataframe(records)

    # Filters, offered only where there is more than one value to choose between.
    filter_cols = st.columns(5)
    filters: dict[str, list] = {}
    for col, column_name in zip(filter_cols, ["Make", "Fuel", "Drive", "Cyl", "Confidence"]):
        options = sorted({v for v in frame[column_name].dropna().tolist()}, key=str)
        if len(options) > 1:
            chosen = col.multiselect(column_name, options, default=[])
            if chosen:
                filters[column_name] = chosen

    filtered = frame
    for column_name, chosen in filters.items():
        filtered = filtered[filtered[column_name].isin(chosen)]

    search = st.text_input("Search", placeholder="VIN, make, model, trim…")
    if search:
        mask = filtered.astype(str).apply(
            lambda row: search.lower() in " ".join(row).lower(), axis=1
        )
        filtered = filtered[mask]

    st.caption(
        f"{len(filtered)} of {len(frame)} vehicles"
        + (" — click a column header to sort." if len(filtered) > 1 else "")
    )
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # Exports carry provenance, not just values.
    visible = [r for r in records if r.vin in set(filtered["VIN"])]
    if visible:
        col_csv, col_xlsx = st.columns(2)
        col_csv.download_button(
            "⬇️ Download CSV",
            data=to_csv(visible).encode("utf-8-sig"),
            file_name=export_filename("csv"),
            mime="text/csv",
            use_container_width=True,
        )
        col_xlsx.download_button(
            "⬇️ Download Excel",
            data=io.BytesIO(to_xlsx(visible)),
            file_name=export_filename("xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.caption(
            "The Excel workbook includes separate sheets for field-level "
            "provenance and detected discrepancies."
        )


# --- Compare tab ------------------------------------------------------------

def render_compare() -> None:
    st.subheader("Compare vehicles")
    valid = [r for r in all_records() if r.valid]
    if len(valid) < 2:
        st.info("Decode at least two vehicles to compare them.")
        return

    labels = {f"{vehicle_title(r)} · {r.vin[-6:]}": r.vin for r in valid}
    chosen = st.multiselect(
        "Select two to six vehicles", list(labels), max_selections=6,
        default=list(labels)[:min(3, len(labels))],
    )
    if len(chosen) < 2:
        st.info("Select at least two vehicles.")
        return

    selected = [st.session_state["records"][labels[c]] for c in chosen]
    comparison = build_comparison(selected)

    st.caption(
        f"{comparison['difference_count']} of {len(comparison['rows'])} attributes "
        f"differ. Differing rows are highlighted; ▲ marks the leading value where "
        f"higher or lower is objectively better."
    )

    headers = [v["title"] for v in comparison["vehicles"]]
    rows, differing = [], []
    for row in comparison["rows"]:
        entry = {"Attribute": row["label"]}
        for i, header in enumerate(headers):
            value = row["values"][i]
            if value is None:
                shown = NOT_AVAILABLE
            elif row["field"] in {"engine_displacement_l", "zero_to_sixty_s"}:
                shown = f"{float(value):.1f}"
            else:
                shown = str(value)
            if row["best_index"] == i:
                shown += " ▲"
            # Disambiguate identical vehicle titles.
            entry[f"{header} ({comparison['vehicles'][i]['vin'][-6:]})"] = shown
        rows.append(entry)
        differing.append(row["differs"])

    frame = pd.DataFrame(rows)
    highlight = pd.Series(differing, index=frame.index)

    st.dataframe(
        frame.style.apply(
            lambda _row: [
                "background-color: rgba(247,198,90,.20)" if highlight.iloc[_row.name] else ""
            ] * len(frame.columns),
            axis=1,
        ),
        use_container_width=True,
        hide_index=True,
    )


# --- Main -------------------------------------------------------------------

def main() -> None:
    init_state()
    refresh, verify = render_sidebar()

    decode_tab, bulk_tab, compare_tab, about_tab = st.tabs(
        ["🔍 Decode", "📋 Bulk & Export", "⚖️ Compare", "ℹ️ How to read this"]
    )
    with decode_tab:
        render_decode(refresh, verify)
    with bulk_tab:
        render_bulk()
    with compare_tab:
        render_compare()
    with about_tab:
        st.markdown(
            f"""
### Reading the results

| Badge | Meaning |
|---|---|
| {confidence_badge('HIGH')} | Corroborated, or read straight from the VIN |
| {confidence_badge('MEDIUM')} | Single source, or varies within the VIN pattern |
| {confidence_badge('LOW')} | Inferred or weakly matched |
| {origin_badge('VIN_DECODED')} | Decoded from the 17 characters — ground truth |
| {origin_badge('ENRICHED')} | Enriched from a specification database |
| {badge('CONFLICT', '#fdf0d5', '#9a6100')} | Sources disagree; both values are kept |

### Three rules this tool follows

1. **Every value knows where it came from.** No field is stored as a bare value —
   each carries its source, confidence, timestamp, and whether it came from the
   VIN itself or from a database lookup.
2. **Disagreements are surfaced, never resolved silently.** When two sources
   conflict, both are kept and the record is flagged. A contested value is also
   downgraded from HIGH, because a disputed value is not a high-confidence one.
3. **Missing data stays missing.** A field no source supplied shows as
   *"{NOT_AVAILABLE}"*. Nothing is estimated or invented to look complete.

### Cost

Free sources run first. Commercial providers are called only when a required
field is still missing afterwards, or when **Cross-verify** is switched on.
With no API key configured the app runs at **$0.00**, permanently.
""",
            unsafe_allow_html=True,
        )


main()
