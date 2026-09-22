# CLAUDE.md - Project Context & Token-Efficient Developer Guide

## Project Overview
**Costco & Fuel Notifier** is an automated triple-pipeline monitoring suite built with Python and GitHub Actions. It monitors live regular gas/petrol/diesel prices along US and India commute routes, applies credit card reward optimization (Citi Costco Visa 4%, Amex BCE 3%, Amazon Pay ICICI 1% Surcharge Waiver), logs US trends to Google Sheets, tracks USD→INR remittance costs for monthly transfers to India, and dispatches responsive daily HTML email digests.

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

### 3. USD→INR Remittance Notifier (`usd_inr_tracker.py`)
- **Purpose**: ~$3,000/month USD→India transfers, sent around the 15th with ~14 days of flexibility.
- **Core finding (do not undo)**: timing the rate is near worthless at this size; provider choice is worth ~8× more and is certain. Walk-forward over 45 monthly windows (5y ECB data): percentile ≥85 rule **+₹142/mo**, optimal-stopping model **−₹49/mo**, perfect hindsight **+₹1,163/mo**, best-vs-worst provider spread **₹9–10k per transfer**. The email therefore leads with the provider decision and prints the timing rule's track record beside its verdict.
- **Model**: `log S = a + b·t + x`, `x` an AR(1) residual; optimal stopping by backward induction under CRRA risk aversion. Computed and displayed as context, but does **not** drive the verdict — its drift term always argues for waiting in a depreciating currency.
- **Data**: Frankfurter/ECB (history, primary), Yahoo Finance (intraday spot + DXY/Brent/Nifty), open.er-api.com (spot fallback), Wise public comparison API (provider rates, incl. competitors).
- **Modes**: `--mode digest` | `--mode alert` (silent unless triggered) | `--mode dry-run` (writes `preview.html`).
- **Workflows**: `.github/workflows/usd_inr_schedule.yml` (`11 12 * * *`), `usd_inr_alert.yml` (`37 */3 * * *`).
- **Landmines**: forecasts must anchor on today's close (invariant: horizon 0 == spot, reservation at `days_left=0` == spot); don't replace the elementwise weighted sums in `fx_signals.py` with `@` (macOS Accelerate BLAS raises spurious FP warnings); Yahoo 429s are normal and expected; `.gitignore`'s blanket `*.json` is negated for `data/events.json` and `data/state.json`.

## Environment Variables & Secrets

| Secret Name | Required By | Description | Default / Example |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | All three | Gmail address sending digests | `yourname@gmail.com` |
| `SENDER_PASSWORD` | All three | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Tracker | Recipient for US fuel digest | `ajithsri2000@gmail.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai | Recipient(s) for Mumbai digest (comma-separated supported) | `srikanthsund@gmail.com` |
| `FX_RECEIVER_EMAIL` | USD→INR | Recipient(s) for remittance brief; falls back to `RECEIVER_EMAIL` | `ajithsri2000@gmail.com` |
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

# Run USD→INR remittance brief locally
python usd_inr_tracker.py --mode dry-run        # render preview.html, send nothing
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" FX_RECEIVER_EMAIL="me@gmail.com" python usd_inr_tracker.py --mode digest
python -m unittest test_fx_signals              # 21 offline tests
```
