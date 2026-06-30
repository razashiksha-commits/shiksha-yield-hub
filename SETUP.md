# Shiksha Yield% Hub — Setup (read this once)

## Files
- `streamlit_app.py` — the dashboard
- `yield_core.py`    — data logic (must sit next to streamlit_app.py)
- `requirements.txt` — PINNED versions (do not loosen; this stops the loading loop)

## 1. Push all three files to your GitHub repo root.

## 2. On Streamlit Cloud: ⋮ → Settings → Secrets, paste this and fill it in:

GUIDE_GEMINI_KEY = "your-real-gemini-key"

GOOGLE_APPLICATION_CREDENTIALS_JSON = '''
{
  "type": "service_account",
  "project_id": "shiksha-seo-automation",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "yield-tracker@shiksha-seo-automation.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/yield-tracker%40shiksha-seo-automation.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
'''

NOTE: the token_uri / auth_uri above are the REAL Google URLs. The summary you
pasted had broken placeholder URLs (e.g. "https://google.com") — those will fail.
Copy the JSON exactly as Google generated it when you created the service account.

## 3. THE #1 REASON LIVE MODE FAILS — grant the service account access:
   a) Search Console → Settings → Users and permissions → add
      yield-tracker@...gserviceaccount.com  as a user (Full/Restricted).
   b) GA4 → Admin → Property Access Management → add the SAME email
      with at least "Viewer".
   Without BOTH, you get 403 errors even though the code is correct.

## 4. Required GA4 setup:
   Your conversion event must exist in GA4 with the exact name you type in the
   sidebar (default: pdf_download_click). If your event is named differently,
   change it in the sidebar field.

## 5. Run it:
   - Open the app → it loads in **Demo data** mode instantly (no creds needed).
   - Switch the sidebar to **🔌 Live Google APIs** → press ⚡ Run Audit.
