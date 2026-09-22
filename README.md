# ⛽ Fuel Notifier & Reward Optimizer (US, Mumbai & USD→INR)

A production-grade, triple-pipeline automation suite built with Python and GitHub Actions. It monitors live fuel prices across daily commute corridors in the **US (Connecticut)** and **India (Mumbai)**, tracks **USD→INR remittance costs** for monthly transfers to India, optimizes credit card rewards, and delivers mobile-responsive HTML email digests.

---

## 🚀 Triple Pipeline Architecture

| Pipeline | Coverage Corridor | Key Features | Primary Rewards Logic | Schedule |
| :--- | :--- | :--- | :--- | :--- |
| **US Tracker** (`gas_tracker.py`) | ZIPs `06460`, `06854`, `06901`, `10801` (CT) | GasBuddy scraping, Google Sheets trend logging, Waze 1-tap navigation links | **Citi Costco Visa**: 4% at Costco & standalone gas<br>**Amex BCE**: 3% at standalone gas<br>*(Supermarket gas 1% base)* | 3-hour interval daily |
| **Mumbai Tracker** (`mumbai_gas_tracker.py`) | Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli | Sorted Petrol & Diesel, Purple Diesel highlights, Nitrogen tyre availability, Hyundai Venue 2019 care card | **Amazon Pay ICICI Card**: 1% fuel surcharge waiver on ₹400–₹4,000 spend (saves surcharge + 18% GST) | Daily @ 7:00 AM IST (`30 1 * * *`) |
| **USD→INR Remittance** (`usd_inr_tracker.py`) | USD → India transfers, ~$3,000/month around the 15th | All-in provider cost ranking, trend+AR(1) stopping model, walk-forward backtest, markup-drift detection, threshold alerts | **Provider choice beats rate timing**: best-vs-worst spread ₹9–10k per transfer, vs a measured timing edge of +₹142/mo (not significant) | Daily @ ~08:11 ET + 3-hourly alert poll |

---

## 🔑 Environment Variables & GitHub Secrets

Configure the following secrets in **Settings > Secrets and variables > Actions**:

| Secret Name | Required By | Description | Example / Default |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | All three | Gmail address sending digest emails | `yourname@gmail.com` |
| `SENDER_PASSWORD` | All three | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Pipeline | Recipient for US digest | `user@example.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai Pipeline | Recipient(s) for Mumbai digest (comma-separated supported) | `dad@example.com` |
| `FX_RECEIVER_EMAIL` | USD→INR Pipeline | Recipient(s) for remittance brief. Falls back to `RECEIVER_EMAIL` if unset | `user@example.com` |
| `GCP_SERVICE_ACCOUNT` | US Pipeline | Raw JSON string of GCP Service Account key | `{"type": "service_account", ...}` |
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

---

## 📁 Repository Structure

```
.
├── .github/workflows/
│   ├── schedule.yml            # US fuel tracker workflow (Cron trigger)
│   ├── mumbai_schedule.yml     # Mumbai fuel tracker workflow (Daily 7:00 AM IST)
│   ├── usd_inr_schedule.yml    # USD→INR daily brief (~08:11 ET)
│   └── usd_inr_alert.yml       # USD→INR threshold alerts (3-hourly poll)
├── gas_tracker.py          # US fuel tracker, GasBuddy parser & Sheets logger
├── mumbai_gas_tracker.py   # Mumbai fuel tracker, station router & vehicle care engine
├── usd_inr_tracker.py      # USD→INR orchestrator, HTML email, alert gating
├── fx_data.py              # FX/provider fetching, disk cache, source fallbacks
├── fx_signals.py           # Trend+AR(1) model, reservation rate, send verdict
├── fx_providers.py         # All-in cost ranking, markup-drift warnings
├── fx_backtest.py          # Walk-forward validation of the send rules
├── fx_config.py            # USD→INR env parsing (empty-string safe)
├── test_fx_signals.py      # 21 offline tests for the model & window logic
├── data/events.json        # RBI / FOMC policy dates (manually refreshed)
├── ARCHITECTURE.md         # Full system architecture, maintenance & troubleshooting memory
├── CLAUDE.md               # Token-efficient AI agent context & quick reference guide
└── requirements.txt        # Minimal Python dependencies
```

---

## 📄 Documentation

- [ARCHITECTURE.md](file:///Users/skymaster/Library/CloudStorage/OneDrive-NortheasternUniversity/Projects/Costco_Fuel_Notifier/ARCHITECTURE.md): Technical deep-dive, system memory, error handling & maintenance guide.
- [CLAUDE.md](file:///Users/skymaster/Library/CloudStorage/OneDrive-NortheasternUniversity/Projects/Costco_Fuel_Notifier/CLAUDE.md): Low-token summary for LLM context windows.

---

## 📄 License

MIT License. Free to use and adapt.
