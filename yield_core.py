"""
yield_core.py
Pure data functions for the Shiksha Yield% tracker.
No Streamlit / no network here on purpose -> fully unit-testable.
"""
import pandas as pd

# Correct, real Google endpoints & scopes
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def gsc_endpoint(site_url: str) -> str:
    """Build the Search Console searchAnalytics endpoint.
    Handles both URL-prefix ('https://shiksha.com/') and
    domain ('sc-domain:shiksha.com') properties."""
    from urllib.parse import quote
    encoded = quote(site_url, safe="")
    return f"https://www.googleapis.com/webmasters/v3/sites/{encoded}/searchAnalytics/query"


def ga4_endpoint(property_id: str) -> str:
    pid = str(property_id).strip().replace("properties/", "")
    return f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"


def gsc_payload(start_date: str, end_date: str, row_limit: int = 1000) -> dict:
    return {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["date", "page"],
        "rowLimit": row_limit,
    }


def ga4_payload(start_date: str, end_date: str, event_name: str) -> dict:
    return {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "date"}, {"name": "landingPagePlusQueryString"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {
            "filter": {
                "fieldName": "eventName",
                "stringFilter": {"value": event_name},
            }
        },
        "limit": 10000,
    }


def ga4_sessions_payload(start_date: str, end_date: str) -> dict:
    """Organic-search sessions per landing page (the GA4 denominator)."""
    return {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "date"}, {"name": "landingPagePlusQueryString"}],
        "metrics": [{"name": "sessions"}],
        "dimensionFilter": {
            "filter": {
                "fieldName": "sessionDefaultChannelGroup",
                "stringFilter": {"value": "Organic Search"},
            }
        },
        "limit": 10000,
    }


def gsc_query_payload(start_date: str, end_date: str, page_url: str,
                      row_limit: int = 25) -> dict:
    """Top search queries that drive traffic to ONE specific page."""
    return {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "dimensionFilterGroups": [{
            "filters": [{"dimension": "page", "operator": "equals",
                         "expression": page_url}]
        }],
        "rowLimit": row_limit,
    }


def _norm_url(u: str) -> str:
    """Normalise a URL for matching: drop query string + fragment, strip
    trailing slash. This improves GSC<->GA4 join match rates."""
    if not isinstance(u, str):
        return ""
    u = u.strip().split("?")[0].split("#")[0]
    return u.rstrip("/")


def parse_gsc(gsc_json: dict) -> pd.DataFrame:
    """Turn a raw GSC searchAnalytics response into a tidy DataFrame."""
    rows = []
    for row in gsc_json.get("rows", []):
        keys = row.get("keys", [])
        if len(keys) < 2:
            continue
        rows.append({
            "Date": keys[0],                       # 'YYYY-MM-DD'
            "URL": _norm_url(keys[1]),             # page
            "Impressions": int(row.get("impressions", 0)),
            "Clicks": int(row.get("clicks", 0)),
            "Position": round(float(row.get("position", 0)), 1),
        })
    return pd.DataFrame(rows)


def _parse_ga4(ga4_json: dict, site_url: str, out_col: str) -> pd.DataFrame:
    """Generic GA4 runReport parser: date + landing page + one metric."""
    rows = []
    base = site_url.replace("sc-domain:", "https://").rstrip("/")
    for row in ga4_json.get("rows", []):
        dims = row.get("dimensionValues", [])
        mets = row.get("metricValues", [])
        if len(dims) < 2 or len(mets) < 1:
            continue
        raw_date = dims[0]["value"]               # 'YYYYMMDD'
        date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        path = dims[1]["value"]
        full = path if path.startswith("http") else base + "/" + path.lstrip("/")
        rows.append({"Date": date, "URL": _norm_url(full),
                     out_col: int(mets[0]["value"])})
    return pd.DataFrame(rows)


def parse_ga4(ga4_json: dict, site_url: str) -> pd.DataFrame:
    """Conversion event counts per landing page per day."""
    return _parse_ga4(ga4_json, site_url, "Conversions")


def parse_ga4_sessions(ga4_json: dict, site_url: str) -> pd.DataFrame:
    """Organic sessions per landing page per day."""
    return _parse_ga4(ga4_json, site_url, "Sessions")


def parse_gsc_queries(gsc_json: dict) -> pd.DataFrame:
    """Top queries for a page: term, clicks, impressions, CTR%, position."""
    rows = []
    for row in gsc_json.get("rows", []):
        keys = row.get("keys", [])
        if not keys:
            continue
        rows.append({
            "Query": keys[0],
            "Clicks": int(row.get("clicks", 0)),
            "Impressions": int(row.get("impressions", 0)),
            "CTR%": round(float(row.get("ctr", 0)) * 100, 1),
            "Position": round(float(row.get("position", 0)), 1),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Clicks", ascending=False).reset_index(drop=True)
    return df


def filter_urls(df: pd.DataFrame, patterns: str) -> pd.DataFrame:
    """Keep only rows whose URL contains ANY of the comma-separated patterns.
    Used to scope the audit to pages that actually carry the conversion event
    (e.g. '/news/,/articles/'). Blank patterns -> return everything unchanged."""
    if df.empty or not patterns or not patterns.strip():
        return df
    pats = [p.strip() for p in patterns.split(",") if p.strip()]
    if not pats:
        return df
    mask = df["URL"].apply(lambda u: any(p in u for p in pats))
    return df[mask].reset_index(drop=True)


def _collapse(df: pd.DataFrame, sum_cols, mean_cols=None) -> pd.DataFrame:
    """Collapse to ONE row per (Date, URL). Without this, query-string variants
    that normalise to the same URL create duplicate keys, and the GSC×GA4×sessions
    merge becomes a cartesian product that inflates every total."""
    if df is None or df.empty:
        return df
    agg = {c: "sum" for c in sum_cols if c in df.columns}
    for c in (mean_cols or []):
        if c in df.columns:
            agg[c] = "mean"
    return df.groupby(["Date", "URL"], as_index=False).agg(agg)


def build_yield_table(gsc_df: pd.DataFrame, ga4_df: pd.DataFrame,
                      sessions_df: pd.DataFrame = None) -> pd.DataFrame:
    """Merge GSC (impressions/clicks) with GA4 conversions and, optionally,
    GA4 organic sessions, on Date+URL. LEFT join on GSC so pages with traffic
    but zero conversions are kept (those are the biggest opportunities).

    Each input is first collapsed to one row per (Date, URL) so the joins are
    strictly 1-to-1 and metrics are never double-counted."""
    if gsc_df is None or gsc_df.empty:
        return pd.DataFrame()

    gsc_df = _collapse(gsc_df, ["Impressions", "Clicks"], ["Position"])
    ga4_df = _collapse(ga4_df, ["Conversions"])
    sessions_df = _collapse(sessions_df, ["Sessions"])

    if ga4_df is None or ga4_df.empty:
        df = gsc_df.copy()
        df["Conversions"] = 0
    else:
        df = pd.merge(gsc_df, ga4_df, on=["Date", "URL"], how="left")
        df["Conversions"] = df["Conversions"].fillna(0).astype(int)
    if sessions_df is not None and not sessions_df.empty:
        df = pd.merge(df, sessions_df, on=["Date", "URL"], how="left")
        df["Sessions"] = df["Sessions"].fillna(0).astype(int)
    else:
        df["Sessions"] = 0
    df["Yield"] = ((df["Conversions"] / (df["Impressions"] + 1)) * 1000).round(3)
    return df.sort_values(["Date", "Impressions"], ascending=[False, False]).reset_index(drop=True)


def find_opportunities(df: pd.DataFrame, min_impressions: int = 50) -> pd.DataFrame:
    """Rank pages by realistic lost-conversion potential.

    Adds three diagnostic rates so you can tell WHICH problem a page has:
      CTR%          = clicks / impressions     -> search-listing / ranking health
      ConvRate_Clk% = conversions / clicks     -> on-page / CTA health (organic)
      ConvRate_Ses% = conversions / sessions   -> on-page health (GA4 organic)
    Recoverable conversions are based on CLICKS (what you can actually convert),
    not impressions, so the number is achievable."""
    if df.empty:
        return pd.DataFrame()
    has_sessions = "Sessions" in df.columns
    agg_spec = dict(
        Impressions=("Impressions", "sum"),
        Clicks=("Clicks", "sum"),
        Conversions=("Conversions", "sum"),
        Avg_Position=("Position", "mean") if "Position" in df else ("Impressions", "size"),
    )
    if has_sessions:
        agg_spec["Sessions"] = ("Sessions", "sum")
    agg = df.groupby("URL").agg(**agg_spec).reset_index()
    agg = agg[agg["Impressions"] >= min_impressions].copy()
    if agg.empty:
        return agg

    agg["Yield"] = (agg["Conversions"] / (agg["Impressions"] + 1) * 1000).round(3)
    agg["CTR%"] = (agg["Clicks"] / (agg["Impressions"] + 1) * 100).round(2)
    agg["ConvRate_Clk%"] = (agg["Conversions"] / (agg["Clicks"] + 1) * 100).round(2)
    if has_sessions:
        agg["ConvRate_Ses%"] = (agg["Conversions"] / (agg["Sessions"] + 1) * 100).round(2)

    # Recoverable = clicks * (site-average conversions-per-click) - actual, >= 0.
    # Based on clicks, because you can only convert visitors who actually arrived.
    site_cpc = agg["Conversions"].sum() / (agg["Clicks"].sum() + 1)
    agg["Potential_Gain"] = (agg["Clicks"] * site_cpc - agg["Conversions"]).round(0)
    agg["Potential_Gain"] = agg["Potential_Gain"].clip(lower=0).astype(int)
    agg["Avg_Position"] = agg["Avg_Position"].round(1)

    cols = ["URL", "Impressions", "Clicks", "CTR%"]
    if has_sessions:
        cols.append("Sessions")
    cols += ["Conversions", "ConvRate_Clk%"]
    if has_sessions:
        cols.append("ConvRate_Ses%")
    cols += ["Yield", "Avg_Position", "Potential_Gain"]
    return agg[cols].sort_values("Potential_Gain", ascending=False).reset_index(drop=True)
