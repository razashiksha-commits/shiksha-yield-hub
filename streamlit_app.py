"""
streamlit_app.py — Shiksha.com Yield% & Registration Growth Dashboard
Fixes the infinite-loading-loop and rebuilds the UI around actionable growth.
"""
import json
import ast
import datetime as dt

import pandas as pd
import requests
import streamlit as st

from yield_core import (
    GSC_SCOPE, GA4_SCOPE,
    gsc_endpoint, ga4_endpoint, gsc_payload, ga4_payload,
    ga4_sessions_payload, gsc_query_payload,
    parse_gsc, parse_ga4, parse_ga4_sessions, parse_gsc_queries,
    build_yield_table, find_opportunities, filter_urls,
)

st.set_page_config(page_title="Shiksha Yield% Hub", page_icon="🎯", layout="wide")

# Latest free-tier Flash model (May 2026). Change here if Google updates it.
GEMINI_MODEL = "gemini-3.5-flash"

# ----------------------------------------------------------------------------
# LAZY, CACHED AUTH  — this is the fix for the loading loop.
# Nothing here runs at import time; it only runs when called, and is cached so
# token refresh happens once, not on every Streamlit rerun.
# ----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_credentials():
    raw = st.secrets["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    raw = raw.strip() if isinstance(raw, str) else dict(raw)
    if isinstance(raw, str):
        try:
            creds_dict = json.loads(raw)
        except Exception:
            creds_dict = ast.literal_eval(raw)
    else:
        creds_dict = raw
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(
        creds_dict, scopes=[GSC_SCOPE, GA4_SCOPE]
    )


def fresh_token():
    from google.auth.transport.requests import Request
    creds = get_credentials()
    creds.refresh(Request())
    return creds.token


@st.cache_resource(show_spinner=False)
def get_ai_client():
    from google import genai
    return genai.Client(api_key=st.secrets["GUIDE_GEMINI_KEY"])


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_live(site_url, prop_id, start, end, event_name):
    """One cached call per parameter set -> no repeated API hits, no token waste.
    Pulls GSC traffic, GA4 conversions, and GA4 organic sessions."""
    token = fresh_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r1 = requests.post(gsc_endpoint(site_url), headers=headers,
                       json=gsc_payload(start, end), timeout=30)
    r1.raise_for_status()
    gsc_df = parse_gsc(r1.json())

    r2 = requests.post(ga4_endpoint(prop_id), headers=headers,
                       json=ga4_payload(start, end, event_name), timeout=30)
    r2.raise_for_status()
    ga4_df = parse_ga4(r2.json(), site_url)

    # Organic sessions (second GA4 report) — the GA4 denominator
    r3 = requests.post(ga4_endpoint(prop_id), headers=headers,
                       json=ga4_sessions_payload(start, end), timeout=30)
    r3.raise_for_status()
    sessions_df = parse_ga4_sessions(r3.json(), site_url)

    return build_yield_table(gsc_df, ga4_df, sessions_df)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_page_queries(site_url, page_url, start, end):
    """Top search queries driving traffic to ONE page (intent diagnosis)."""
    token = fresh_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(gsc_endpoint(site_url), headers=headers,
                      json=gsc_query_payload(start, end, page_url), timeout=30)
    r.raise_for_status()
    return parse_gsc_queries(r.json())


# ----------------------------------------------------------------------------
# DEMO DATA — lets the dashboard render instantly without credentials.
# ----------------------------------------------------------------------------
def demo_table():
    pages = {
        "/news/neet-2026-answer-key-blogId-101": (24000, 0.4),
        "/news/cuet-result-2026-blogId-102": (31000, 0.6),
        "/science/articles/cuet-du-cutoff-blogId-103": (8200, 3.1),
        "/engineering/articles/jee-main-dates-blogId-104": (12000, 1.1),
        "/news/aibe-2026-result-blogId-105": (16000, 0.2),
        "/university/banaras-hindu-university": (9000, 0.0),  # not instrumented -> filtered out
    }
    rows = []
    base = dt.date.today()
    for d in range(14):
        day = (base - dt.timedelta(days=d)).isoformat()
        for path, (imp, y) in pages.items():
            imp_d = int(imp * (0.85 + 0.3 * ((d * 7) % 5) / 5))
            clicks = int(imp_d * 0.05)
            conv = int(imp_d * y / 1000)
            rows.append({"Date": day, "URL": "https://www.shiksha.com" + path,
                         "Impressions": imp_d, "Clicks": clicks,
                         "Sessions": int(clicks * 1.1),  # organic sessions ~ clicks
                         "Position": round(2 + (y < 2) * 5, 1), "Conversions": conv})
    df = pd.DataFrame(rows)
    df["Yield"] = (df["Conversions"] / (df["Impressions"] + 1) * 1000).round(3)
    return df.sort_values(["Date", "Impressions"], ascending=[False, False])


# ----------------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------------
st.sidebar.title("🎛️ Audit Controls")
mode = st.sidebar.radio("Data source", ["🧪 Demo data", "🔌 Live Google APIs"])

site_url = st.sidebar.text_input("GSC Property URL", "https://www.shiksha.com/")
prop_id = st.sidebar.text_input("GA4 Property ID", "352971661")
event_name = st.sidebar.text_input("Conversion event name", "pdf_button_click")
url_filter = st.sidebar.text_input(
    "Only include URLs containing", "/news/,/articles/",
    help="Scope the audit to pages that actually have the conversion event. "
         "Comma-separated. Leave blank to include every page.")
c1, c2 = st.sidebar.columns(2)
start = c1.date_input("Start", dt.date.today() - dt.timedelta(days=14))
end = c2.date_input("End", dt.date.today())
run = st.sidebar.button("⚡ Run Audit", use_container_width=True, type="primary")

# ----------------------------------------------------------------------------
# HEADER
# ----------------------------------------------------------------------------
st.title("🎯 Shiksha.com — Yield% & Registration Growth Hub")
st.caption("Yield% = (conversion events ÷ search impressions) × 1000. "
           "Find pages bleeding traffic without converting, then fix the biggest ones first.")

# ----------------------------------------------------------------------------
# LOAD DATA
# Live results are stored in session_state so the dashboard survives reruns
# (e.g. clicking "Generate AI roadmap" no longer wipes the screen).
# ----------------------------------------------------------------------------
if "live_df" not in st.session_state:
    st.session_state.live_df = None
    st.session_state.roadmap = None

df = None
if mode.startswith("🧪"):
    df = demo_table()
    st.info("Showing **demo data** so you can see the dashboard. "
            "Switch to *Live Google APIs* in the sidebar when your service "
            "account is connected.")
elif run:
    try:
        with st.spinner("Fetching live data from Google (cached for 1h)…"):
            df = fetch_live(site_url, prop_id, start.isoformat(), end.isoformat(), event_name)
        st.session_state.live_df = df
        st.session_state.roadmap = None  # new data -> clear old roadmap
        st.sidebar.success("🔑 Connected & data loaded")
    except KeyError as e:
        st.error(f"Missing secret: {e}. Add it under **⋮ → Settings → Secrets**.")
    except requests.HTTPError as e:
        st.error(f"Google API error {e.response.status_code}: {e.response.text[:400]}")
        st.caption("Most common cause: the service-account email isn't added as a "
                   "user in Search Console *and* GA4 with read access.")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
elif st.session_state.live_df is not None:
    df = st.session_state.live_df           # restore after a rerun
else:
    st.warning("Pick a data source and press **⚡ Run Audit** (or use Demo data).")

# Scope to pages that actually carry the conversion event
if df is not None and not df.empty:
    before = df["URL"].nunique()
    df = filter_urls(df, url_filter)
    after = df["URL"].nunique()
    if url_filter.strip() and after < before:
        st.caption(f"🔎 Scoped to **{after}** instrumented pages "
                   f"(filtered out {before - after} pages without the event), "
                   f"matching URLs that contain: `{url_filter}`")

# ----------------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------------
if df is not None and not df.empty:
    total_imp = int(df["Impressions"].sum())
    total_clk = int(df["Clicks"].sum())
    total_conv = int(df["Conversions"].sum())
    total_ses = int(df["Sessions"].sum()) if "Sessions" in df else 0
    site_yield = round(total_conv / (total_imp + 1) * 1000, 2)
    cr_clicks = round(total_conv / (total_clk + 1) * 100, 2)
    cr_sessions = round(total_conv / (total_ses + 1) * 100, 2) if total_ses else None

    opp = find_opportunities(df, min_impressions=50)
    recoverable = int(opp["Potential_Gain"].sum()) if not opp.empty else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Impressions", f"{total_imp:,}")
    k2.metric("Clicks", f"{total_clk:,}", help="Organic clicks from Search Console.")
    k3.metric("Conversions", f"{total_conv:,}")
    k4.metric("Recoverable", f"+{recoverable:,}",
              help="Extra conversions if weak pages converted clicks at the site "
                   "average. Based on clicks, so it's realistically achievable.")

    m1, m2, m3 = st.columns(3)
    m1.metric("Yield% (per 1k impressions)", site_yield)
    m2.metric("Conv. rate / clicks", f"{cr_clicks}%",
              help="Conversions ÷ organic clicks (GSC). On-page conversion health.")
    m3.metric("Conv. rate / sessions",
              f"{cr_sessions}%" if cr_sessions is not None else "—",
              help="Conversions ÷ organic sessions (GA4). '—' means GA4 returned "
                   "no organic-sessions data for these pages/dates.")

    st.subheader("📈 Conversion-rate trend (per organic click)")
    trend = (df.groupby("Date")
               .apply(lambda g: g["Conversions"].sum() / (g["Clicks"].sum() + 1) * 100)
               .round(2))
    st.line_chart(trend)

    st.subheader("🔧 Biggest fix-first opportunities")
    st.caption("Read the rates: **low CTR%** = ranking/snippet problem; "
               "**low Conv-rate** with healthy CTR = on-page/CTA problem. "
               "Recoverable is sorted highest-first.")
    if not opp.empty:
        show = opp.head(15).copy()
        show["URL"] = show["URL"].str.replace("https://www.shiksha.com", "", regex=False)
        st.dataframe(
            show.style.background_gradient(subset=["Potential_Gain"], cmap="Reds"),
            use_container_width=True,
        )
    else:
        st.write("No pages above the impression threshold yet.")

    # ---- Per-page query intent drill-down ----
    st.subheader("🔍 What searches bring traffic to a page (intent)")
    if mode.startswith("🔌"):
        if not opp.empty:
            choices = opp["URL"].head(20).tolist()
            picked = st.selectbox(
                "Pick a page to see its top search queries", choices,
                format_func=lambda u: u.replace("https://www.shiksha.com", ""))
            if picked:
                try:
                    with st.spinner("Fetching top queries from Search Console…"):
                        q_df = fetch_page_queries(
                            site_url, picked, start.isoformat(), end.isoformat())
                    if q_df.empty:
                        st.caption("No query data returned (Search Console hides "
                                   "low-volume queries).")
                    else:
                        st.caption("Tip: download/result intent should convert well — "
                                   "if a page ranks mostly for informational queries, "
                                   "the CTA needs to match that intent.")
                        st.dataframe(q_df, use_container_width=True)
                except Exception as e:
                    st.error(f"Query fetch error: {e}")
    else:
        st.caption("Switch to live mode to drill into per-page search queries.")

    with st.expander("📋 Full date-wise table"):
        st.dataframe(df, use_container_width=True)

    # ---- Single batched Gemini call (token-efficient) ----
    st.subheader("🤖 Gemini growth roadmap")
    if mode.startswith("🔌"):
        if st.button("Generate AI roadmap"):
            if opp.empty:
                st.warning("No opportunity pages to analyse yet — run an audit with data first.")
            else:
                try:
                    with st.spinner("Asking Gemini for a prioritised action plan…"):
                        top = opp.head(5)
                        lines = "\n".join(
                            f"- {row['URL']} | clicks {int(row['Clicks']):,} "
                            f"| CTR {row['CTR%']}% "
                            f"| conv-rate/clicks {row['ConvRate_Clk%']}% "
                            f"| avg position {row['Avg_Position']} "
                            f"| recoverable +{int(row['Potential_Gain'])}"
                            for _, row in top.iterrows()
                        )
                        prompt = (
                            "You are the Growth Director for Shiksha.com, an education marketplace. "
                            "Below are news/article pages with strong organic traffic but weak "
                            "conversion into PDF-button clicks (our registration proxy). "
                            "CTR% shows search-listing strength; conv-rate/clicks shows on-page "
                            "conversion of visitors who already arrived:\n\n" + lines +
                            "\n\nFor each page, first diagnose whether the bottleneck is the "
                            "search listing (low CTR) or the page itself (healthy CTR but low "
                            "conv-rate), then give ONE specific, punchy fix. Return a prioritised "
                            "5-point plan, highest-impact first."
                        )
                        resp = get_ai_client().models.generate_content(
                            model=GEMINI_MODEL, contents=prompt)
                        st.session_state.roadmap = resp.text.strip()
                except Exception as e:
                    st.session_state.roadmap = None
                    st.error(f"Gemini error: {e}")
        if st.session_state.get("roadmap"):
            st.info(st.session_state.roadmap)
    elif mode.startswith("🧪"):
        st.caption("Connect live APIs to generate a real AI roadmap from your data.")
