# CLAUDE.md - Project Context & Guidelines

## Project Overview
**Costco & Fuel Notifier** is an automated daily gas tracker. It queries real-time regular gas prices across configured ZIP codes (`06460`, `06854`, `06901`, `10801`), extracts price & timestamp info, logs the cheapest daily station into Google Sheets (`Fuel Trends`), and emails a formatted digest with Waze deep links to the user via FormSubmit.

## Key Architecture & Data Flow
1. **Gas Prices Retrieval (`gas_tracker.py` -> `fetch_gas_prices`)**:
   - Uses `requests.Session` with browser User-Agent headers to fetch CSRF token (`window.gbcsrf`) from `https://www.gasbuddy.com/home`.
   - Sends GraphQL queries (`LocationBySearchTerm`) to `https://www.gasbuddy.com/graphql`.
   - Captures all local gas station brands (Costco, Stop & Shop, CITGO, Shell, Mobil, Speedway, 7-Eleven, etc.).
   - Generates plus-encoded Waze navigation deep links: `https://waze.com/ul?q=<Station_Name>+<ZIP_or_Address>&navigate=yes`.
   - Flags prices updated > 12 hours ago with `⚠️ (Stale >12h)`.
   - Sorts stations ascending by price.

2. **Google Sheets Logging (`gas_tracker.py` -> `log_to_sheets`)**:
   - Authenticates via `service_account.json` using `gspread`.
   - Supports `SHEET_URL` and `SHEET_ID` environment variables for direct URL/ID opening, falling back to title search (`Fuel Trends`).
   - Appends a row `[YYYY-MM-DD, Station Name, ZIP, Price]` for the cheapest station of the day.

3. **Email Dispatch (`gas_tracker.py` -> `send_email`)**:
   - Posts form data to FormSubmit AJAX endpoint: `https://formsubmit.co/ajax/{RECEIVER_EMAIL}` with `Referer: https://formsubmit.co`.
   - Renders top 10 stations with price, distance, last updated status, and clickable Waze navigation link.

4. **GitHub Actions (`.github/workflows/schedule.yml`)**:
   - Triggers on schedule cron `30 10 * * 1-5` (Mon-Fri 10:30 AM UTC) and manual `workflow_dispatch`.
   - Injects `GCP_SERVICE_ACCOUNT` secret into `service_account.json` and runs `gas_tracker.py`.

## Environment & Dependencies
- Python Version: Python 3.10+ / 3.11
- Requirements: `py_gasbuddy`, `gspread`, `google-auth`, `requests`, `backoff`, `aiofiles`
- Environment Variables:
  - `RECEIVER_EMAIL`: Email destination for digests.
  - `GCP_SERVICE_ACCOUNT`: Service account JSON string.
  - `SHEET_URL` (Optional): Direct link to Google Sheet.
  - `SHEET_ID` (Optional): Spreadsheet ID.
  - `SHEET_NAME` (Optional, default `Fuel Trends`): Sheet title.

## Common Development Commands
```bash
# Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run local test
RECEIVER_EMAIL="ajithsri2000@gmail.com" python gas_tracker.py
```

## Maintenance & Gotchas
- **Git Ignore**: Secrets (`*.json`, `service_account.json`) and `venv/` are explicitly ignored in `.gitignore`. NEVER commit service account key files.
- **GCP APIs Required**: Both **Google Sheets API** and **Google Drive API** must be enabled on the GCP Project (`northeasternskymaster`).
- **Google Sheet Permission**: Service account email (`bmwnotifier@northeasternskymaster.iam.gserviceaccount.com`) must be added as an **Editor** on the `Fuel Trends` sheet.
- **FormSubmit Activation**: FormSubmit requires clicking an initial one-time "Activate Form" link sent to `RECEIVER_EMAIL`.
