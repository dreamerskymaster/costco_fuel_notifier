# CLAUDE.md - Project Context & Token-Efficient Developer Guide

## Project Overview
**Costco & Fuel Notifier** is an automated dual-pipeline fuel price tracker built with Python and GitHub Actions. It monitors live regular gas/petrol/diesel prices along US and India commute routes, applies credit card reward optimization (Citi Costco Visa 4%, Amex BCE 3%, Amazon Pay ICICI 1% Surcharge Waiver), logs US trends to Google Sheets, and dispatches responsive daily HTML email digests.

## Pipelines & Data Flow

### 1. US Fuel Notifier (`gas_tracker.py`)
- **Coverage**: ZIP codes `06460`, `06854`, `06901`, `10801` (Norwalk, CT commute).
- **Scraper**: GasBuddy GraphQL (`LocationBySearchTerm`).
- **Sheets Logging**: Appends daily cheapest station `[YYYY-MM-DD, Name, ZIP, Price]` to Google Sheet `Fuel Trends` (`service_account.json`).
- **Card Optimization**:
  - **Costco Gas**: Citi Costco Anywhere Visa (4% cash back, Visa only).
  - **Standalone Gas**: Citi Costco Visa (4%) / Amex Blue Cash Everyday (3%).
  - **Supermarket Gas (Stop & Shop)**: Excluded by MCC rules (1% base).
- **Workflow**: `.github/workflows/schedule.yml` (Cron: `0 */3 * * *`).

### 2. Mumbai Fuel Notifier (`mumbai_gas_tracker.py`)
- **Coverage**: Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli (Navi Mumbai).
- **Sorting**: Petrol Section (lowest to highest) followed by Diesel Section (lowest to highest).
- **Visuals**: Diesel prices highlighted in **Purple (`#6b21a8`)**; Nitrogen tyre inflation availability (`🎈 Nitrogen` vs `💨 Air Only`).
- **Vehicle Profile**: Hyundai Venue (2019) cold tyre recommendation (33 PSI) and full tank (45L) cost estimates.
- **Card Optimization**: Amazon Pay ICICI Card (1% fuel surcharge waiver on ₹400 – ₹4,000 spend).
- **Workflow**: `.github/workflows/mumbai_schedule.yml` (Cron: `30 1 * * *` = 7:00 AM IST).

## Environment Variables & Secrets

| Secret Name | Required By | Description | Default / Example |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | Both | Gmail address sending digests | `yourname@gmail.com` |
| `SENDER_PASSWORD` | Both | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Tracker | Recipient for US fuel digest | `ajithsri2000@gmail.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai | Recipient(s) for Mumbai digest (comma-separated supported) | `srikanthsund@gmail.com` |
| `GCP_SERVICE_ACCOUNT` | US Tracker | Raw JSON content of GCP Service Account | `{"type": "service_account"...}` |
| `SMTP_SERVER` | Optional | SMTP host | `smtp.gmail.com` |
| `SMTP_PORT` | Optional | SMTP port | `587` |

## Quick Commands
```bash
# Environment Setup
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run US Tracker locally
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" RECEIVER_EMAIL="me@gmail.com" python gas_tracker.py

# Run Mumbai Tracker locally
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" MUMBAI_RECEIVER_EMAIL="dad@gmail.com" python mumbai_gas_tracker.py
```


