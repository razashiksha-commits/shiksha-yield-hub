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


def _norm_url(u: str) -> str:
    """Normalise a URL for matching: strip trailing slash, lower host kept simple."""
    if not isinstance(u, str):
        return ""
    return u.strip().rstrip("/")


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


def parse_ga4(ga4_json: dict, site_url: str) -> pd.DataFrame:
    """Turn a raw GA4 runReport response into a tidy DataFrame."""
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
        rows.append({
            "Date": date,
            "URL": _norm_url(full),
            "Conversions": int(mets[0]["value"]),
        })
    return pd.DataFrame(rows)


def build_yield_table(gsc_df: pd.DataFrame, ga4_df: pd.DataFrame) -> pd.DataFrame:
    """Merge on Date+URL and compute Yield% = conversions/impressions*1000.
    Uses a LEFT join on GSC so pages with traffic but zero conversions
    are kept (those are the biggest opportunities)."""
    if gsc_df.empty:
        return pd.DataFrame()
    if ga4_df.empty:
        df = gsc_df.copy()
        df["Conversions"] = 0
    else:
        df = pd.merge(gsc_df, ga4_df, on=["Date", "URL"], how="left")
        df["Conversions"] = df["Conversions"].fillna(0).astype(int)
    df["Yield"] = (df["Conversions"] / (df["Impressions"] + 1)) * 1000
    df["Yield"] = df["Yield"].round(3)
    return df.sort_values(["Date", "Impressions"], ascending=[False, False]).reset_index(drop=True)


def find_opportunities(df: pd.DataFrame, min_impressions: int = 50) -> pd.DataFrame:
    """Rank pages by lost-conversion potential.
    Opportunity = high impressions + low yield. We aggregate across the
    date range so noisy single days don't dominate, then score."""
    if df.empty:
        return pd.DataFrame()
    agg = (df.groupby("URL")
             .agg(Impressions=("Impressions", "sum"),
                  Clicks=("Clicks", "sum"),
                  Conversions=("Conversions", "sum"),
                  Avg_Position=("Position", "mean") if "Position" in df else ("Impressions", "size"))
             .reset_index())
    agg = agg[agg["Impressions"] >= min_impressions].copy()
    if agg.empty:
        return agg
    agg["Yield"] = (agg["Conversions"] / (agg["Impressions"] + 1) * 1000).round(3)
    site_yield = agg["Conversions"].sum() / (agg["Impressions"].sum() + 1) * 1000
    # Potential extra conversions if this page hit the site-average yield
    agg["Potential_Gain"] = (((site_yield - agg["Yield"]) / 1000) * agg["Impressions"]).round(0)
    agg["Potential_Gain"] = agg["Potential_Gain"].clip(lower=0).astype(int)
    agg["Avg_Position"] = agg["Avg_Position"].round(1)
    return agg.sort_values("Potential_Gain", ascending=False).reset_index(drop=True)
