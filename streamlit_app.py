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
    parse_gsc, parse_ga4, build_yield_table, find_opportunities,
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
    """One cached call per parameter set -> no repeated API hits, no token waste."""
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
    return build_yield_table(gsc_df, ga4_df)


# ----------------------------------------------------------------------------
# DEMO DATA — lets the dashboard render instantly without credentials.
# ----------------------------------------------------------------------------
def demo_table():
    pages = {
        "/law/aibe": (8200, 0.4), "/medicine/neet": (24000, 8.2),
        "/law/clat": (1600, 19.0), "/engineering/jee-main": (31000, 3.1),
        "/mba/cat": (12000, 1.1), "/law/aibe/syllabus": (5400, 0.6),
    }
    rows = []
    base = dt.date.today()
    for d in range(14):
        day = (base - dt.timedelta(days=d)).isoformat()
        for path, (imp, y) in pages.items():
            imp_d = int(imp * (0.85 + 0.3 * ((d * 7) % 5) / 5))
            conv = int(imp_d * y / 1000)
            rows.append({"Date": day, "URL": "https://www.shiksha.com" + path,
                         "Impressions": imp_d, "Clicks": int(imp_d * 0.05),
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
event_name = st.sidebar.text_input("Conversion event name", "pdf_download_click")
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
# ----------------------------------------------------------------------------
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
        st.sidebar.success("🔑 Connected & data loaded")
    except KeyError as e:
        st.error(f"Missing secret: {e}. Add it under **⋮ → Settings → Secrets**.")
    except requests.HTTPError as e:
        st.error(f"Google API error {e.response.status_code}: {e.response.text[:400]}")
        st.caption("Most common cause: the service-account email isn't added as a "
                   "user in Search Console *and* GA4 with read access.")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
else:
    st.warning("Pick a data source and press **⚡ Run Audit** (or use Demo data).")

# ----------------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------------
if df is not None and not df.empty:
    total_imp = int(df["Impressions"].sum())
    total_conv = int(df["Conversions"].sum())
    site_yield = round(total_conv / (total_imp + 1) * 1000, 2)

    opp = find_opportunities(df, min_impressions=50)
    recoverable = int(opp["Potential_Gain"].sum()) if not opp.empty else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Impressions", f"{total_imp:,}")
    k2.metric("Total Conversions", f"{total_conv:,}")
    k3.metric("Site Yield%", site_yield)
    k4.metric("Recoverable Conversions", f"+{recoverable:,}",
              help="Extra conversions if weak pages hit the site-average yield.")

    st.subheader("📈 Yield% trend")
    trend = (df.groupby("Date")
               .apply(lambda g: g["Conversions"].sum() / (g["Impressions"].sum() + 1) * 1000)
               .round(3))
    st.line_chart(trend)

    st.subheader("🔧 Biggest fix-first opportunities")
    st.caption("High traffic + low yield = where a layout/CTA fix wins the most registrations.")
    if not opp.empty:
        show = opp.head(15).copy()
        show["URL"] = show["URL"].str.replace("https://www.shiksha.com", "", regex=False)
        st.dataframe(
            show.style.background_gradient(subset=["Potential_Gain"], cmap="Reds"),
            use_container_width=True,
        )
    else:
        st.write("No pages above the impression threshold yet.")

    with st.expander("📋 Full date-wise table"):
        st.dataframe(df, use_container_width=True)

    # ---- Single batched Gemini call (token-efficient) ----
    st.subheader("🤖 Gemini growth roadmap")
    if mode.startswith("🔌") and st.button("Generate AI roadmap"):
        try:
            top = opp.head(5)
            lines = "\n".join(
                f"- {r.URL} | impressions {int(r.Impressions):,} | yield {r.Yield} "
                f"| avg position {r.Avg_Position} | recoverable +{int(r.Potential_Gain)}"
                for r in top.itertuples()
            )
            prompt = (
                "You are the Growth Director for Shiksha.com, an education marketplace. "
                "These exam landing pages get strong organic traffic but convert poorly "
                "into PDF downloads / registrations:\n\n" + lines +
                "\n\nGive a prioritised 5-point action plan. For each page name ONE specific "
                "layout/CTA/intent change likely to lift registrations, in one punchy line."
            )
            resp = get_ai_client().models.generate_content(
                model=GEMINI_MODEL, contents=prompt)
            st.info(resp.text.strip())
        except Exception as e:
            st.error(f"Gemini error: {e}")
    elif mode.startswith("🧪"):
        st.caption("Connect live APIs to generate a real AI roadmap from your data.")
