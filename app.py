"""GST Taxpayer Search - Streamlit dashboard.

Run locally:      streamlit run app.py
Deploy:           Streamlit Community Cloud -> New app -> this repo, main file: app.py
"""

import datetime as dt
import io
import logging
import os
import tempfile

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:  # project layout: gst_search/ package next to app.py (local runs, git push)
    from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
    from gst_captcha import build_solver
    from gst_gstin import extract_gstins
    from gst_io_utils import read_gstins_from_file, write_excel
    from gst_runner import RunOptions, load_history, run_batch
except ModuleNotFoundError as exc:  # flat layout: gst_*.py files next to app.py (GitHub web upload)
    if exc.name not in ("gst_search", "gst_search.analytics"):
        raise
    from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
    from gst_captcha import build_solver
    from gst_gstin import extract_gstins
    from gst_io_utils import read_gstins_from_file, write_excel
    from gst_runner import RunOptions, load_history, run_batch

st.set_page_config(page_title="GST Taxpayer Search", page_icon="🧾", layout="wide")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
for noisy in ("urllib3", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Validated palette (dataviz reference instance): one series hue + reserved status colours.
SERIES = "#2a78d6"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
          "neutral": "#898781"}
STATUS_COLORS = {"Active": STATUS["good"], "Suspended": STATUS["warning"], "Cancelled": STATUS["critical"],
                 "Other": STATUS["neutral"], "Unknown": STATUS["neutral"], "Not fetched": STATUS["neutral"]}
COMPLIANCE_COLORS = {"Up to date": STATUS["good"], "1–2 months behind": STATUS["warning"],
                     "3+ months behind": STATUS["critical"], "No GSTR-3B found": STATUS["neutral"]}


# ------------------------------------------------------------------ access control
def check_password():
    """Optional gate: set APP_PASSWORD in the app's Secrets on Streamlit Cloud."""
    try:
        expected = st.secrets.get("APP_PASSWORD")
    except Exception:
        expected = None
    if not expected or st.session_state.get("authed"):
        return True
    st.title("🧾 GST Taxpayer Search")
    pwd = st.text_input("Password", type="password")
    if pwd and pwd == expected:
        st.session_state.authed = True
        st.rerun()
    elif pwd:
        st.error("Wrong password")
    return False


@st.cache_resource(show_spinner="Loading captcha OCR models...")
def get_solver():
    return build_solver("ddddocr")


# ------------------------------------------------------------------ charts
def bar(labels, values, colors=None, title="", horizontal=True, fmt="{:,}", height=None):
    """Thin bars, 4px rounded ends, value labels in ink colour, hover per bar."""
    colors = colors or [SERIES] * len(labels)
    text = [fmt.format(v) for v in values]
    if horizontal:
        trace = go.Bar(y=labels, x=values, orientation="h", marker=dict(color=colors, cornerradius=4),
                       text=text, textposition="outside", cliponaxis=False,
                       hovertemplate="%{y}: %{x}<extra></extra>")
    else:
        trace = go.Bar(x=labels, y=values, marker=dict(color=colors, cornerradius=4), text=text,
                       textposition="outside", cliponaxis=False, hovertemplate="%{x}: %{y}<extra></extra>")
    fig = go.Figure(trace)
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)), bargap=0.45, showlegend=False,
        height=height or max(220, 46 * len(labels) + 90), margin=dict(l=10, r=40, t=50, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    grid = dict(showgrid=True, gridcolor="rgba(137,135,129,0.25)", zeroline=False)
    if horizontal:
        fig.update_xaxes(**grid, showticklabels=False)
        fig.update_yaxes(autorange="reversed", showgrid=False)
    else:
        fig.update_yaxes(**grid, showticklabels=False)
        fig.update_xaxes(showgrid=False)
    return fig


def line(x, y, title, y_fmt):
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers", line=dict(color=SERIES, width=2),
                               marker=dict(size=8), hovertemplate="%{x}<br>" + y_fmt + "<extra></extra>"))
    fig.update_layout(title=dict(text=title, font=dict(size=15)), height=300, showlegend=False,
                      margin=dict(l=10, r=10, t=50, b=10), hovermode="x unified",
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.25)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def counts(series, order=None):
    vc = series.value_counts()
    if order:
        vc = vc.reindex([o for o in order if o in vc.index])
    return list(vc.index), [int(v) for v in vc.values]


# ------------------------------------------------------------------ input
def sidebar_inputs():
    st.sidebar.header("1 · GST numbers")
    mode = st.sidebar.radio("Input", ["Paste text", "Upload file"], horizontal=True, label_visibility="collapsed")
    gstins = []
    if mode == "Paste text":
        text = st.sidebar.text_area("One per line, or separated by spaces / commas", height=180,
                                    placeholder="03AAFPW4345P1Z7\n03BKQPG7275L1ZN")
        gstins = extract_gstins(text)
    else:
        up = st.sidebar.file_uploader("Excel, CSV or TXT", type=["xlsx", "xls", "csv", "txt"])
        column = st.sidebar.text_input("GSTIN column (optional – auto-detected)")
        if up:
            suffix = os.path.splitext(up.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(up.getvalue())
            try:
                gstins = read_gstins_from_file(tmp.name, column or None)
            except Exception as exc:
                st.sidebar.error(f"Could not read file: {exc}")
            finally:
                os.unlink(tmp.name)
    gstins = list(dict.fromkeys(gstins))
    st.sidebar.caption(f"**{len(gstins)}** unique GSTIN(s) detected")

    st.sidebar.header("2 · Settings")
    opts = RunOptions(
        workers=st.sidebar.slider("Parallel workers", 1, 8, 4,
                                  help="More is faster; lower it if the portal starts failing or slowing down."),
        max_age=st.sidebar.number_input("Reuse results fetched within (days)", 0.0, 90.0, 7.0, 1.0,
                                        help="0 = always fetch fresh"),
        refresh=st.sidebar.checkbox("Force fresh fetch (ignore saved results)"),
        goods=st.sidebar.checkbox("Goods & services (HSN)", value=True),
        returns=st.sidebar.checkbox("Return filing history", value=True),
    )
    return gstins, opts


# ------------------------------------------------------------------ run
def run(gstins, opts):
    progress = st.progress(0.0, text="Starting...")
    live = st.empty()
    tally = {"portal": 0, "cache": 0, "retry": 0, "invalid": 0, "failed": 0}

    def on_progress(done, total, record, source):
        tally[source] += 1
        if not record.get("ok"):
            tally["failed"] += 1
        name = (record.get("details") or {}).get("lgnm") or record.get("error", "")
        progress.progress(done / total, text=f"{done}/{total} · {record['gstin']} · {name}")
        live.caption(f"From portal: {tally['portal']} · Reused: {tally['cache']} · Retried: {tally['retry']} · "
                     f"Invalid: {tally['invalid']} · Failed so far: {tally['failed']}")

    solver = get_solver()
    result = run_batch(gstins, opts, solver=solver, progress=on_progress)
    progress.progress(1.0, text=f"Done in {result['stats']['duration_s']:.0f}s")
    st.session_state.result = result
    st.session_state.ran_at = dt.datetime.now()


def excel_bytes(records):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        path = tmp.name
    try:
        write_excel(records, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


# ------------------------------------------------------------------ dashboard
def performance_tiles(stats):
    sub, wrong = stats["captcha_submitted"], stats["captcha_wrong"]
    searched = max(stats["searched"], 1)
    c = st.columns(6)
    c[0].metric("GSTINs", stats["total"])
    c[1].metric("Success rate", f"{stats['ok'] / max(stats['total'], 1):.0%}",
                help=f"{stats['ok']} OK · {stats['failed']} failed ({stats['invalid']} invalid)")
    c[2].metric("Time taken", f"{stats['duration_s']:.0f} s")
    c[3].metric("Avg per searched GSTIN", f"{stats['duration_s'] / searched:.2f} s" if stats["searched"] else "–",
                help=f"{stats['workers']} parallel workers")
    c[4].metric("Captcha accuracy", f"{(sub - wrong) / sub:.0%}" if sub else "–",
                help=f"{sub} submitted · {wrong} rejected (each retried automatically)")
    c[5].metric("Reused from saved", stats["reused"], help="Fetched within the reuse window – no portal call")


def portfolio_tiles(k):
    c = st.columns(6)
    c[0].metric("Active", f"{k['active']}", help=f"{k['active_share']:.0%} of fetched GSTINs")
    c[1].metric("Cancelled", k["cancelled"])
    c[2].metric("Suspended", k["suspended"])
    c[3].metric("GSTR-3B up to date", f"{k['up_to_date_share']:.0%}",
                help="Latest due monthly GSTR-3B has been filed")
    c[4].metric("E-invoicing enabled", f"{k['einvoice_share']:.0%}")
    c[5].metric("Aadhaar authenticated", f"{k['aadhaar_share']:.0%}")


def dashboard(result):
    stats, records = result["stats"], result["records"]
    df = build_frame(records)
    ok = df[df["Fetched"] == "OK"]

    st.subheader("Run performance")
    performance_tiles(stats)
    st.subheader("Taxpayer portfolio")
    portfolio_tiles(kpis(df))

    left, right = st.columns([3, 1])
    with right:
        st.download_button("⬇ Download Excel", excel_bytes(records), width="stretch",
                           file_name=f"gst_details_{st.session_state.ran_at:%Y%m%d_%H%M%S}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    tabs = st.tabs(["Overview", "Compliance", "Results table", "Performance", "Run history"])

    with tabs[0]:
        a, b = st.columns(2)
        labels, values = counts(df["Status Group"], ["Active", "Suspended", "Cancelled", "Other", "Unknown",
                                                     "Not fetched"])
        a.plotly_chart(bar(labels, values, [STATUS_COLORS[l] for l in labels], "GSTIN status"),
                       width="stretch")
        labels, values = counts(ok["Constitution"].replace("", "Unknown"))
        b.plotly_chart(bar(labels[:8], values[:8], title="Constitution of business"), width="stretch")
        a, b = st.columns(2)
        labels, values = counts(df["State"])
        a.plotly_chart(bar(labels[:10], values[:10], title="State (from GSTIN)"), width="stretch")
        nature = ok["Nature of Business"].str.split(", ").explode().replace("", pd.NA).dropna()
        labels, values = counts(nature)
        b.plotly_chart(bar(labels[:8], values[:8], title="Nature of business (a GSTIN can have several)"),
                       width="stretch")

    with tabs[1]:
        labels, values = counts(ok["Filing Compliance"], COMPLIANCE_ORDER)
        st.plotly_chart(bar(labels, values, [COMPLIANCE_COLORS[l] for l in labels],
                            "GSTR-3B filing compliance (monthly filers)"), width="stretch")
        st.caption("Up to date = the latest GSTR-3B that is already due (20th of the following month) has been "
                   "filed. Quarterly (QRMP) filers may show as behind.")
        risky = ok[(ok["Status Group"] != "Active") | (ok["Filing Compliance"].isin(
            ["3+ months behind", "No GSTR-3B found"]))]
        st.markdown(f"**Needs attention: {len(risky)}** (not active, or GSTR-3B 3+ months behind / missing)")
        st.dataframe(risky[["GSTIN", "Legal Name", "Trade Name", "GSTIN Status", "Filing Compliance",
                            "Last GSTR-3B Filed", "Last GSTR-1 Filed"]], hide_index=True, width="stretch")

    with tabs[2]:
        f1, f2, f3 = st.columns(3)
        status_f = f1.multiselect("Status", sorted(df["Status Group"].unique()))
        comp_f = f2.multiselect("Filing compliance", [c for c in COMPLIANCE_ORDER if c in set(df["Filing Compliance"])])
        search = f3.text_input("Search name / GSTIN")
        view = df
        if status_f:
            view = view[view["Status Group"].isin(status_f)]
        if comp_f:
            view = view[view["Filing Compliance"].isin(comp_f)]
        if search:
            s = search.lower()
            view = view[view["GSTIN"].str.lower().str.contains(s) | view["Legal Name"].str.lower().str.contains(s)
                        | view["Trade Name"].fillna("").str.lower().str.contains(s)]
        st.caption(f"{len(view)} of {len(df)} rows")
        st.dataframe(view, hide_index=True, width="stretch", height=520)

    with tabs[3]:
        t = stats.get("timings") or {}
        if t:
            order = ["captcha download", "captcha OCR", "captcha fetch+read", "waiting for prefetched captcha",
                     "search request", "goods request", "returns request", "total per GSTIN"]
            labels = [k for k in order if k in t] + [k for k in t if k not in order]
            st.plotly_chart(bar(labels, [t[k] for k in labels], title="Average seconds per step",
                                fmt="{:.2f}s"), width="stretch")
            st.caption("Goods/returns run in parallel with the captcha search, and the next captcha is prepared "
                       "in the background, so steps overlap – they don't add up to the total.")
        else:
            st.info("All GSTINs came from saved results – no portal calls in this run.")
        c = st.columns(4)
        c[0].metric("Captcha images fetched", stats["captcha_fetched"])
        c[1].metric("Captchas submitted", stats["captcha_submitted"])
        c[2].metric("Rejected (auto-retried)", stats["captcha_wrong"])
        c[3].metric("GSTINs retried", stats["retried"])

    with tabs[4]:
        history_tab()


def history_tab():
    hist = load_history()
    if not hist:
        st.info("No runs recorded yet.")
        return
    h = pd.DataFrame(hist)
    h["Run"] = pd.to_datetime(h["started_at"]).dt.strftime("%d %b %H:%M")
    h["Sec / searched GSTIN"] = (h["duration_s"] / h["searched"].where(h["searched"] > 0)).round(2)
    h["Captcha accuracy"] = ((h["captcha_submitted"] - h["captcha_wrong"])
                             / h["captcha_submitted"].where(h["captcha_submitted"] > 0))
    c = st.columns(4)
    c[0].metric("Runs", len(h))
    c[1].metric("GSTINs processed", int(h["total"].sum()))
    c[2].metric("Overall success", f"{h['ok'].sum() / max(h['total'].sum(), 1):.1%}")
    sub = h["captcha_submitted"].sum()
    c[3].metric("Overall captcha accuracy", f"{(sub - h['captcha_wrong'].sum()) / sub:.1%}" if sub else "–")
    searched = h[h["searched"] > 0].tail(30)
    if len(searched) >= 2:
        a, b = st.columns(2)
        a.plotly_chart(line(searched["Run"], searched["Sec / searched GSTIN"], "Seconds per searched GSTIN",
                            "%{y:.2f}s"), width="stretch")
        b.plotly_chart(line(searched["Run"], searched["Captcha accuracy"] * 100, "Captcha accuracy (%)",
                            "%{y:.0f}%"), width="stretch")
    show = h[["Run", "total", "ok", "failed", "reused", "searched", "workers", "duration_s",
              "Sec / searched GSTIN", "captcha_submitted", "captcha_wrong"]].iloc[::-1]
    st.dataframe(show.rename(columns={"total": "GSTINs", "ok": "OK", "failed": "Failed", "reused": "Reused",
                                      "searched": "Searched", "workers": "Workers", "duration_s": "Seconds",
                                      "captcha_submitted": "Captchas", "captcha_wrong": "Rejected"}),
                 hide_index=True, width="stretch")


# ------------------------------------------------------------------ page
def main():
    if not check_password():
        return
    st.title("🧾 GST Taxpayer Search")
    st.caption("Public taxpayer details from services.gst.gov.in · captcha solved automatically · "
               "use responsibly")
    gstins, opts = sidebar_inputs()
    if st.sidebar.button("🔍 Fetch details", type="primary", width="stretch", disabled=not gstins):
        run(gstins, opts)

    if "result" in st.session_state:
        dashboard(st.session_state.result)
    else:
        st.info("Paste GST numbers or upload a file in the sidebar, then press **Fetch details**.")
        with st.expander("Run history on this server", expanded=False):
            history_tab()


main()
