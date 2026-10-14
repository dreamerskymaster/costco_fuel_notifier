# ⛽ Fuel Notifier & Reward Optimizer (US, Mumbai & FX)

A multi-pipeline automation suite built with Python and GitHub Actions. It monitors fuel prices on the **US (Norwalk, CT ↔ Jersey City, NJ)** and **India (Mumbai)** commute corridors, tracks **USD→INR** and **INR→USD** transfer costs, picks the right credit card for each fill, and sends mobile-friendly HTML email digests. Every email carries an ML or rule-based outlook printed beside its own out-of-sample track record.

Everything runs on GitHub Actions with free, key-less data sources. There is no server and no local checkout to maintain.

---

## 🚀 Pipelines

| Pipeline | Coverage Corridor | Key Features | Primary Rewards Logic | Schedule |
| :--- | :--- | :--- | :--- | :--- |
| **US Tracker** (`gas_tracker.py`) | ZIPs `06854` Norwalk, `06901` Stamford, `10801` New Rochelle, `07608` Teterboro, `07306` Jersey City, `07002` Bayonne | GasBuddy scraping, Sheets "Price Log" + "Predictions" tabs, self-graded daily Norwalk price prediction, toll-free Friday route, Waze links. Emails only on the Fri/Sun weekend alerts or a 90-day Norwalk low (`gas_cadence.py`) | **Citi Costco Visa**: 4% at Costco & standalone gas<br>**Amex BCE**: 3% at standalone gas<br>*(Supermarket gas 1% base)* | 3-hour interval daily |
| **Mumbai Tracker** (`mumbai_gas_tracker.py`) | Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli | Sorted Petrol & Diesel, Purple Diesel highlights, Nitrogen tyre availability, Hyundai Venue 2019 care card | **Dad's cards** (`recommend_card()`): full tank → **HDFC Regalia** (1% waiver ₹400–₹5,000, ₹500/cycle cap); top-ups ≤ ₹4,000 → **Amazon Pay ICICI**; HSBC RuPay & Mastercard debit have no fuel waiver | Daily @ 7:00 AM IST (`30 1 * * *`) |
| **Road Trip Digest** (`road_trip_tracker.py`) | Mumbai ↔ Goa (NH48 vs NH66), Mumbai ↔ Pune (Expressway) | Route comparison cards, Google Maps + Waze links, highway tyre advisory | Same card picker as Mumbai | 5:30 AM IST on trip dates only (last trip **2026-10-17**) |
| **USD→INR Remittance** (`usd_inr_tracker.py`) | USD → India transfers, ~$3,000/month around the 15th | All-in provider cost ranking, trend+AR(1) stopping model, walk-forward backtest, markup-drift detection, threshold alerts | **Provider choice beats rate timing**: best-vs-worst spread ₹9–10k per transfer, vs a measured timing edge of +₹142/mo (not significant) | Daily @ ~08:11 ET + 3-hourly alert poll |
| **INR→USD Credila Brief** (`inr_usd_tracker.py`) | Credila education loan → student's Chase account, ~$1,000/month | Live HSBC India & IOB TT-selling card rates, Credila wire OUR vs SHA vs Global Pay cost to land an exact dollar amount, Global Pay break-even rate, GST on conversion, Wise yardstick, receipt calibration | **Route and fee handling beat timing**: waiting for a cheap dollar measured −₹108/mo vs sending on day one over 55 windows | Daily check @ ~09:23 ET; emails only on window open / 2 days before close / weekly |

---

## 🔑 Environment Variables & GitHub Secrets

Configure the following secrets in **Settings > Secrets and variables > Actions**:

| Secret Name | Required By | Description | Example / Default |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | All pipelines | Gmail address sending digest emails | `yourname@gmail.com` |
| `SENDER_PASSWORD` | All pipelines | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Pipeline | Recipient for US digest | `user@example.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai Pipeline | Recipient(s) for Mumbai digest (comma-separated supported) | `dad@example.com` |
| `FX_RECEIVER_EMAIL` | USD→INR Pipeline | Recipient(s) for remittance brief. Falls back to `RECEIVER_EMAIL` if unset | `user@example.com` |
| `FX_EXTRA_RECEIVERS` | Optional | Additional recipient(s), comma-separated, appended to the above and de-duplicated. Kept separate so no address ever appears in a workflow file — **this repo is public** | `friend@example.com` |
| `GCP_SERVICE_ACCOUNT` | US Pipeline | Raw JSON string of GCP Service Account key | `{"type": "service_account", ...}` |
| `INR_USD_RECEIVER_EMAIL` | INR→USD Pipeline | Recipient(s) for the Credila→Chase brief. **No fallback** to `RECEIVER_EMAIL` — the brief belongs to someone else | `student@example.com` |
| `SMTP_SERVER` | Optional | Custom SMTP host | `smtp.gmail.com` |
| `SMTP_PORT` | Optional | Custom SMTP port | `587` |

---

## 💻 Local Setup & Execution

```bash
# 1. Clone & create virtual environment
git clone https://github.com/dreamerskymaster/costco_fuel_notifier.git
cd costco_fuel_notifier
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Run US Fuel Tracker locally
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" RECEIVER_EMAIL="me@gmail.com" python gas_tracker.py

# 3. Run Mumbai Fuel Tracker locally
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" MUMBAI_RECEIVER_EMAIL="dad@gmail.com" python mumbai_gas_tracker.py

# 4. USD→INR remittance brief — render to preview.html without sending
python usd_inr_tracker.py --mode dry-run

# 4b. Send it, or stay silent unless a threshold is crossed
SENDER_EMAIL="me@gmail.com" SENDER_PASSWORD="pass" FX_RECEIVER_EMAIL="me@gmail.com" python usd_inr_tracker.py --mode digest
python usd_inr_tracker.py --mode alert
```

### Tunable variables (USD→INR pipeline)

Set as repository **Variables** (not secrets), or as env vars locally. All optional.

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `SEND_AMOUNT_USD` | `3000` | Transfer size driving every rupee figure |
| `PAYDAY_DAY` | `15` | Day of month the send window opens |
| `FLEX_DAYS` | `14` | Days you can wait before the forced send |
| `CURRENT_PROVIDER` | `Wise` | Yours, so the email can price switching |
| `RISK_AVERSION` | `3.0` | CRRA γ for the reservation calculation |
| `DRIFT_SHRINK` | `0.5` | Fraction of the fitted trend to believe |
| `ALERT_PERCENTILE` | `85` | Level that triggers send advice and alerts |
| `ALERT_COOLDOWN_DAYS` | `3` | Minimum gap between threshold alerts |

### Tunable variables (INR→USD Credila pipeline)

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `INR_USD_AMOUNT` | `1000` | Dollars that must land in Chase |
| `INR_USD_WINDOW_START` | `15` | Day of month the send window opens |
| `INR_USD_FLEX_DAYS` | `14` | Window length in days |
| `INR_USD_DIGEST_WEEKDAY` | `0` | Weekly brief day (0 = Monday) |
| `WIRE_MARKUP_PCT` | `2.0` | Wire markup used only if HSBC's rate page is unreadable |
| `WIRE_OUR_FEE_INR` | `1200` | HSBC charge (pre-GST) for OUR, where the sender pays every bank en route |
| `WIRE_CORRESPONDENT_USD` | `20` | Dollars shaved by correspondents on SHA |
| `CHASE_INCOMING_WIRE_USD` | `15` | Chase incoming international wire fee |
| `GLOBALPAY_MARKUP_PCT` | `1.0` | Guess for Credila Global Pay (WSFx) — unpublished |
| `GLOBALPAY_FEE_INR` / `GLOBALPAY_CORRESPONDENT_USD` | `0` / `0` | Extra Global Pay charges, if any |
| `WIRE_RECEIPT` / `GLOBALPAY_RECEIPT` | unset | `YYYY-MM-DD,inr_debited,usd_received` from a real transfer; calibrates the markup |

---

## 📁 Repository Structure

```
.
├── .github/workflows/
│   ├── schedule.yml            # US fuel tracker workflow (Cron trigger)
│   ├── mumbai_schedule.yml     # Mumbai fuel tracker workflow (Daily 7:00 AM IST)
│   ├── road_trip_schedule.yml  # Mumbai↔Goa/Pune trip digest (trip dates only)
│   ├── usd_inr_schedule.yml    # USD→INR daily brief (~08:11 ET)
│   ├── usd_inr_alert.yml       # USD→INR threshold alerts (3-hourly poll)
│   └── inr_usd_schedule.yml    # INR→USD Credila brief (daily check, sparse emails)
├── gas_tracker.py          # US fuel tracker, GasBuddy parser & Sheets logger
├── gas_cadence.py          # US email gate: weekend alerts + 90-day-low rule (state: data/gas_state.json)
├── price_predictor.py      # Self-graded next-day Norwalk price prediction
├── us_fuel_forecast.py     # EIA-based week-ahead fill-up outlook (77% vs 56% base rate)
├── mumbai_gas_tracker.py   # Mumbai fuel tracker, Dad's card picker & vehicle care engine
├── india_fuel.py           # India fuel prices (goodreturns → bankbazaar) + rule-based outlook
├── road_trip_tracker.py    # Mumbai↔Goa/Pune route & fuel digest
├── ml_core.py              # numpy-only logistic/ridge models + walk-forward validation
├── fx_outlook.py           # USD/INR direction outlook (base rate + calibrated 80% range)
├── email_thread.py         # Keeps each digest in one email thread
├── usd_inr_tracker.py      # USD→INR orchestrator, HTML email, alert gating
├── fx_data.py              # FX/provider fetching, disk cache, source fallbacks
├── fx_signals.py           # Trend+AR(1) model, reservation rate, send verdict
├── fx_providers.py         # All-in cost ranking, markup-drift warnings
├── fx_backtest.py          # Walk-forward validation of the send rules
├── inr_usd_tracker.py      # INR→USD Credila→Chase brief: routes, email, send gating
├── fx_outbound.py          # Cost-to-land model, GST slabs, HSBC/IOB card rates, Wise yardstick
├── fx_outbound_timing.py   # Walk-forward test of timing in the INR→USD direction
├── fx_config.py            # Env parsing for both FX pipelines (empty-string safe)
├── test_fx_signals.py      # 21 offline tests for the model & window logic
├── test_fx_outbound.py     # 16 offline tests for the INR→USD brief
├── test_ml_outlooks.py     # 19 offline tests for the ML outlooks
├── test_gas_cadence.py     # Offline tests for the US email gate
├── data/                   # Committed state + price history CSVs (updated by the workflows)
├── ARCHITECTURE.md         # Full system architecture, maintenance & troubleshooting memory
├── CLAUDE.md               # Token-efficient AI agent context, status & next steps
├── AGENTS.md               # Vehicles, routes, card rules & schedules (agent memory)
└── requirements.txt        # Minimal Python dependencies
```

---

## 📄 Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): Technical deep-dive, system memory, error handling & maintenance guide.
- [CLAUDE.md](CLAUDE.md): Low-token summary, current status and next steps.
- [AGENTS.md](AGENTS.md): Vehicles, routes, card rules and schedules.

Run all offline tests with `python -m unittest test_fx_signals test_fx_outbound test_ml_outlooks test_gas_cadence` (66 tests).

---

## 📄 License

MIT License. Free to use and adapt.
