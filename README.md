# Agajay VPS Panel

Professional VPS-style panel built with Flask. Upload files, run them (Python / Node / Bash), stream logs live, install modules — all from the browser.

## Default Owner
- **Username:** `Agajayofficial`
- **Password:** `agajay`

Override via env: `OWNER_USERNAME`, `OWNER_PASSWORD`, `SECRET_KEY`.

## Local Run
```bash
pip install -r requirements.txt
python app.py
# open http://localhost:8080
```

## Deploy to Railway
1. Push this folder to GitHub.
2. Railway → New Project → Deploy from GitHub repo → select repo.
3. Add variables (optional): `OWNER_USERNAME`, `OWNER_PASSWORD`, `SECRET_KEY`.
4. Done. Railway auto-detects `nixpacks.toml` + `Procfile`.

## Features
- Owner panel: create users with day-limit & file-upload-limit, extend, delete.
- User dashboard: upload many files, run / stop, live logs (SSE).
- Install modules: `pip`, `pkg` (apt fallback), `npm` — from UI.
- Pricing page editable by owner.
- Health endpoint `/healthz`.

## Notes
- Max upload: 500 MB per request (change `MAX_CONTENT_LENGTH`).
- Supported runtimes: `.py`, `.js`, `.ts`, `.sh`.
