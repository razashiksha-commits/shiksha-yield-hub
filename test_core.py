"""
test_core.py — runs the real logic against true-to-shape mock API data.
"""
import pandas as pd
from yield_core import (
    parse_gsc, parse_ga4, build_yield_table, find_opportunities,
    gsc_endpoint, ga4_endpoint, gsc_payload, ga4_payload,
)

# ---- 1. Mock GSC response (exact shape returned by searchAnalytics.query) ----
mock_gsc = {
    "rows": [
        {"keys": ["2026-06-28", "https://www.shiksha.com/law/aibe/"],   "clicks": 120, "impressions": 8000, "position": 4.2},
        {"keys": ["2026-06-28", "https://www.shiksha.com/medicine/neet/"], "clicks": 300, "impressions": 25000, "position": 2.1},
        {"keys": ["2026-06-28", "https://www.shiksha.com/law/clat/"],    "clicks": 45,  "impressions": 1500, "position": 7.8},
        {"keys": ["2026-06-27", "https://www.shiksha.com/law/aibe/"],    "clicks": 110, "impressions": 7600, "position": 4.5},
        {"keys": ["2026-06-27", "https://www.shiksha.com/medicine/neet/"], "clicks": 280, "impressions": 24000, "position": 2.3},
        {"keys": ["bad-row"]},  # malformed -> must be skipped, not crash
    ]
}

# ---- 2. Mock GA4 response (exact shape returned by runReport) ----
mock_ga4 = {
    "rows": [
        {"dimensionValues": [{"value": "20260628"}, {"value": "/law/aibe/"}],     "metricValues": [{"value": "5"}]},
        {"dimensionValues": [{"value": "20260628"}, {"value": "/medicine/neet/"}], "metricValues": [{"value": "210"}]},
        {"dimensionValues": [{"value": "20260628"}, {"value": "/law/clat/"}],      "metricValues": [{"value": "30"}]},
        {"dimensionValues": [{"value": "20260627"}, {"value": "/medicine/neet/"}], "metricValues": [{"value": "190"}]},
        # note: aibe on 2026-06-27 has NO conversions -> must appear as 0 (left join)
    ]
}

print("=" * 60)
print("ENDPOINT / PAYLOAD CHECK")
print("=" * 60)
print("GSC endpoint:", gsc_endpoint("https://www.shiksha.com/"))
print("GA4 endpoint:", ga4_endpoint("352971661"))
print("GA4 endpoint (with prefix):", ga4_endpoint("properties/352971661"))
print("GSC payload:", gsc_payload("2026-06-27", "2026-06-28"))

print("\n" + "=" * 60)
print("STEP 1 — parse_gsc")
print("=" * 60)
gsc_df = parse_gsc(mock_gsc)
print(gsc_df.to_string())
assert len(gsc_df) == 5, "malformed row should be dropped"
assert gsc_df.iloc[0]["Date"] == "2026-06-28"
assert "shiksha.com" in gsc_df.iloc[0]["URL"]
assert gsc_df.iloc[0]["Impressions"] == 8000
print("PASS: malformed row skipped, date+url+impressions parsed")

print("\n" + "=" * 60)
print("STEP 2 — parse_ga4 (YYYYMMDD->YYYY-MM-DD, list indexing)")
print("=" * 60)
ga4_df = parse_ga4(mock_ga4, "https://www.shiksha.com/")
print(ga4_df.to_string())
assert ga4_df.iloc[0]["Date"] == "2026-06-28", "date must be reformatted"
assert ga4_df.iloc[0]["URL"].endswith("/law/aibe"), "path joined to base url"
assert ga4_df.iloc[0]["Conversions"] == 5
print("PASS: GA4 date reformatted, relative path -> full url, conversions int")

print("\n" + "=" * 60)
print("STEP 3 — build_yield_table (left join keeps zero-conversion pages)")
print("=" * 60)
final = build_yield_table(gsc_df, ga4_df)
print(final.to_string())
# aibe on 27th had impressions but no GA4 conversion row -> must be 0, not dropped
aibe27 = final[(final["URL"].str.contains("aibe")) & (final["Date"] == "2026-06-27")]
assert len(aibe27) == 1, "zero-conversion page must survive the join"
assert aibe27.iloc[0]["Conversions"] == 0
# yield math: neet 28th = 210/(25000+1)*1000 = 8.399...
neet28 = final[(final["URL"].str.contains("neet")) & (final["Date"] == "2026-06-28")].iloc[0]
expected = round(210 / (25000 + 1) * 1000, 3)
assert neet28["Yield"] == expected, f"{neet28['Yield']} != {expected}"
print(f"PASS: zero-conv page kept; yield math correct (neet 28th = {neet28['Yield']})")

print("\n" + "=" * 60)
print("STEP 4 — find_opportunities (potential lost conversions)")
print("=" * 60)
opp = find_opportunities(final, min_impressions=50)
print(opp.to_string())
assert "Potential_Gain" in opp.columns
assert (opp["Potential_Gain"] >= 0).all(), "no negative gains"
# aibe: huge impressions (15600), tiny conversions (5) -> should be a top opportunity
top_url = opp.iloc[0]["URL"]
print(f"PASS: top opportunity surfaced = {top_url} "
      f"(gain={opp.iloc[0]['Potential_Gain']} conv)")

print("\n" + "=" * 60)
print("EDGE CASES")
print("=" * 60)
empty = build_yield_table(pd.DataFrame(), ga4_df)
assert empty.empty
print("PASS: empty GSC -> empty table (no crash)")
no_ga4 = build_yield_table(gsc_df, pd.DataFrame())
assert (no_ga4["Conversions"] == 0).all()
print("PASS: GA4 empty -> all conversions 0 (no crash)")
print("\nALL TESTS PASSED ✅")
