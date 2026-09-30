# CLAUDE.md - Project Context & Token-Efficient Developer Guide

## Project Overview
**Costco & Fuel Notifier** is an automated multi-pipeline monitoring suite built with Python and GitHub Actions. It monitors live regular gas/petrol/diesel prices along US and India commute routes, applies credit card reward optimization (Citi Costco Visa 4%, Amex BCE 3%, Amazon Pay ICICI 1% Surcharge Waiver), logs US trends to Google Sheets, tracks USD→INR remittance costs for monthly transfers to India, and dispatches responsive daily HTML email digests.

## Pipelines & Data Flow

### 1. US Fuel Notifier (`gas_tracker.py`)
- **Coverage**: Norwalk, CT (`06854`) → Stamford, CT (`06901`) → New Rochelle, NY (`10801`) → Teterboro, NJ (`07608`) → Jersey City, NJ (`07306` / 850 Westside Ave) → Bayonne, NJ (`07002` / Costco). *Milford, CT (`06460`) removed.*
- **Scraper**: GasBuddy GraphQL (`LocationBySearchTerm`).
- **Sheets Logging**: Appends daily cheapest station `[YYYY-MM-DD, Name, ZIP, Price]` to Google Sheet `Fuel Trends` (`service_account.json`).
- **Arbitrage Strategy**: Automatically detects Friday pre-departure (Norwalk → Jersey City) and Sunday pre-return (Jersey City → Norwalk), calculating NJ vs CT gas price differentials (~$0.40–$0.60/gal cheaper in NJ) and recommending optimal fill-up timing.
- **Card Optimization**:
  - **Costco Gas**: Citi Costco Anywhere Visa (4% cash back, Visa only).
  - **Standalone Gas**: Citi Costco Visa (4%) / Amex Blue Cash Everyday (3%).
  - **Supermarket Gas (Stop & Shop)**: Excluded by MCC rules (1% base).
- **Workflow**: `.github/workflows/schedule.yml` (Cron: `0 */3 * * *`, plus Friday 4:00 PM EDT `0 20 * * 5` and Sunday 3:00 PM EDT `0 19 * * 0`).


### 2. Mumbai Fuel Notifier (`mumbai_gas_tracker.py`)
- **Coverage**: Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli (Navi Mumbai).
- **Sorting**: Petrol Section (lowest to highest) followed by Diesel Section (lowest to highest).
- **Visuals**: Diesel prices highlighted in **Purple (`#6b21a8`)**; Nitrogen tyre inflation availability (`🎈 Nitrogen` vs `💨 Air Only`).
- **Vehicle Profile**: Hyundai Venue (2019) cold tyre recommendation (33 PSI normal / 36 PSI highway) and full tank (45L) cost estimates.
- **Card Optimization**: Dad's card rules via `recommend_card(amount)`:
  - **HDFC Regalia**: 1% fuel surcharge waiver on ₹400 – ₹5,000 (max ₹500/cycle); recommended for full tanks (~₹4,600+).
  - **Amazon Pay ICICI**: 1% fuel surcharge waiver on ₹400 – ₹4,000 (uncapped); recommended for top-ups ≤ ₹4,000.
  - **HSBC RuPay & Mastercard Debit**: Excluded (no fuel waiver; 0% reward points on fuel across all cards).
- **Workflow**: `.github/workflows/mumbai_schedule.yml` (Cron: `30 1 * * *` = 7:00 AM IST; auto-skips on road trip dates).

### 3. One-Time Road Trip Fuel & Route Digest (`road_trip_tracker.py`)
- **Coverage**: Mumbai ↔ Goa (choice of NH48 via Kolhapur vs NH66 Coastal) & Mumbai ↔ Pune (Mumbai-Pune Expressway).
- **Dates (IST)**: Sep 30, Oct 4, Oct 9, Oct 10, Oct 15, Oct 17.
- **Navigation**: Visual route comparison cards with 1-tap Google Maps Driving Directions and Waze links.
- **Card & Vehicle**: Dad's credit card swipe recommendations & Venue 36 PSI highway tyre advisory.
- **Workflow**: `.github/workflows/road_trip_schedule.yml` (Cron: `0 0 * * *` = 5:30 AM IST).


### 3. USD→INR Remittance Notifier (`usd_inr_tracker.py`)
- **Purpose**: ~$3,000/month USD→India transfers, sent around the 15th with ~14 days of flexibility.
- **Core finding (do not undo)**: timing the rate is near worthless at this size; provider choice is worth ~8× more and is certain. Walk-forward over 45 monthly windows (5y ECB data): percentile ≥85 rule **+₹142/mo**, optimal-stopping model **−₹49/mo**, perfect hindsight **+₹1,163/mo**, best-vs-worst provider spread **₹9–10k per transfer**. The email therefore leads with the provider decision and prints the timing rule's track record beside its verdict.
- **Model**: `log S = a + b·t + x`, `x` an AR(1) residual; optimal stopping by backward induction under CRRA risk aversion. Computed and displayed as context, but does **not** drive the verdict — its drift term always argues for waiting in a depreciating currency.
- **Data**: Frankfurter/ECB (history, primary), Yahoo Finance (intraday spot + DXY/Brent/Nifty), open.er-api.com (spot fallback), Wise public comparison API (provider rates, incl. competitors).
- **Modes**: `--mode digest` | `--mode alert` (silent unless triggered) | `--mode dry-run` (writes `preview.html`).
- **Workflows**: `.github/workflows/usd_inr_schedule.yml` (`11 12 * * *`), `usd_inr_alert.yml` (`37 */3 * * *`).
- **Landmines**: forecasts must anchor on today's close (invariant: horizon 0 == spot, reservation at `days_left=0` == spot); don't replace the elementwise weighted sums in `fx_signals.py` with `@` (macOS Accelerate BLAS raises spurious FP warnings); Yahoo 429s are normal and expected; `.gitignore`'s blanket `*.json` is negated for `data/events.json` and `data/state.json`.

### 4. INR→USD Credila Brief (`inr_usd_tracker.py`)
- **Purpose**: a student in the US moves ~$1,000/month from a **Credila** education loan to her **Chase** account, choosing between a **Credila wire** (funds sit in the HSBC India account opened at loan signing and leave by SWIFT at HSBC's TT-selling card rate) and **Credila Global Pay** (WSFx GlobalPay; markup unpublished).
- **Core finding (do not undo)**: timing loses in this direction too — waiting for a cheap dollar within a 14-day window cost **₹108/mo** vs sending on day one over 55 windows (hindsight ceiling ₹312/mo), because the rupee drifts weaker. The brief says "send on day one" and leads with route choice: the Global Pay **break-even rate** vs a wire with charges set to **OUR**.
- **Data**: HSBC India + IOB card-rate HTML pages (parsed by cell offset; a layout change returns None and falls back to `WIRE_MARKUP_PCT`), Frankfurter/Yahoo mid, Wise `v3/quotes` as a yardstick (not usable by a US-resident sender).
- **Facts baked in** (checked Sept 2026): Chase $15 incoming international wire (College Checking too); HSBC OUR ₹1,200 + GST, app remittance otherwise free; GST on conversion per CGST Rule 32(2)(b); loan-funded education remittance = 0% TCS. Convera GlobalPay for Students pays institutions only — not a route to a personal Chase account.
- **Modes**: `--mode digest` (emails only on window open, 2 days before close, weekly day) | `force` | `dry-run` (writes `inr_usd_preview.html`).
- **Workflow**: `.github/workflows/inr_usd_schedule.yml` (`23 13 * * *`), shares the `usd-inr-notifier` concurrency group. State in `data/inr_usd_state.json` (negated in `.gitignore`), history in `data/inr_usd_history.csv`.

## Environment Variables & Secrets

| Secret Name | Required By | Description | Default / Example |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | All three | Gmail address sending digests | `yourname@gmail.com` |
| `SENDER_PASSWORD` | All three | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Tracker | Recipient for US fuel digest | `you@example.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai | Recipient(s) for Mumbai digest (comma-separated supported) | `family@example.com` |
| `FX_RECEIVER_EMAIL` | USD→INR | Recipient(s) for remittance brief; falls back to `RECEIVER_EMAIL` | `you@example.com` |
| `FX_EXTRA_RECEIVERS` | Optional | Extra recipient(s) appended & de-duplicated. Separate secret because **the repo is public** — never put an address in a workflow file | `friend@example.com` |
| `INR_USD_RECEIVER_EMAIL` | INR→USD | Recipient(s) for the Credila→Chase brief. No fallback to `RECEIVER_EMAIL` by design | `student@example.com` |
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

# Run INR→USD Credila brief locally
python inr_usd_tracker.py --mode dry-run        # render inr_usd_preview.html
python -m unittest test_fx_outbound             # 16 offline tests
```
