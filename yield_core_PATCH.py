"""
PATCH for yield_core.py — fixes find_opportunities() tie-break ordering.

THE BUG
--------
When conversions are 0 across the board for a date range (as in your 10-15 June
screenshot, where every row shows "Key events: 0.00 (0%)"), site_cpc becomes 0,
which makes Potential_Gain = 0 for EVERY page in the dataset — not just the
visible top 10. With thousands of pages tied at Potential_Gain = 0, the old
code sorted only by that column, so pandas fell back to whatever order
groupby("URL") happened to produce. That order has nothing to do with traffic
size. Your single biggest page (MHT-CET, 5.27M impressions, 169,842 clicks,
157,017 sessions) lost that arbitrary tiebreak lottery and landed below the
opp.head(15) display cutoff, while a much smaller page (nursing-up-cnet,
49,996 sessions) happened to win the tie and made the visible table.

THE FIX
-------
Add a secondary (and tertiary) sort key: Clicks, then Impressions. So whenever
Potential_Gain ties — which is common, not rare — the biggest, most important
pages always surface first instead of vanishing into arbitrary groupby order.

HOW TO APPLY
------------
In yield_core.py, find the very last line of find_opportunities():

    return agg[cols].sort_values("Potential_Gain", ascending=False).reset_index(drop=True)

Replace it with the fixed version below (also reproduced in full function form
in case you want to just swap the whole function).
"""

import pandas as pd


def find_opportunities(df: pd.DataFrame, min_impressions: int = 50) -> pd.DataFrame:
    """Rank pages by realistic lost-conversion potential.

    Adds three diagnostic rates so you can tell WHICH problem a page has:
      CTR%          = clicks / impressions     -> search-listing / ranking health
      ConvRate_Clk% = conversions / clicks     -> on-page / CTA health (organic)
      ConvRate_Ses% = conversions / sessions   -> on-page health (GA4 organic)
    Recoverable conversions are based on CLICKS (what you can actually convert),
    not impressions, so the number is achievable.

    FIX (this version): ties in Potential_Gain — which happen on every page
    when site-wide conversions are 0, and happen often even when they are not —
    are broken by Clicks then Impressions. This guarantees your highest-traffic
    pages always appear at or near the top of the table, instead of being able
    to randomly drop below the display cutoff (opp.head(15) in the app).
    """
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

    # ---- THE FIX ----
    # Old:  .sort_values("Potential_Gain", ascending=False)
    # New:  tie-break by Clicks, then Impressions, so high-traffic pages never
    #       lose to arbitrary groupby order when Potential_Gain is tied (most
    #       commonly: tied at 0, which happens on every page when site-wide
    #       conversions are 0 for the selected date range).
    return agg[cols].sort_values(
        ["Potential_Gain", "Clicks", "Impressions"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
