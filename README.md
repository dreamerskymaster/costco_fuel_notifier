# ⛽ Fuel Notifier & Reward Optimizer (US & Mumbai)

A production-grade, dual-pipeline automated fuel price tracker and credit card reward optimizer built with Python and GitHub Actions. It monitors live fuel prices across daily commute corridors in the **US (Connecticut)** and **India (Mumbai)**, optimizes credit card rewards, and delivers mobile-responsive HTML email digests.

---

## 🚀 Dual Pipeline Architecture

| Pipeline | Coverage Corridor | Key Features | Primary Rewards Logic | Schedule |
| :--- | :--- | :--- | :--- | :--- |
| **US Tracker** (`gas_tracker.py`) | ZIPs `06460`, `06854`, `06901`, `10801` (CT) | GasBuddy scraping, Google Sheets trend logging, Waze 1-tap navigation links | **Citi Costco Visa**: 4% at Costco & standalone gas<br>**Amex BCE**: 3% at standalone gas<br>*(Supermarket gas 1% base)* | 3-hour interval daily |
| **Mumbai Tracker** (`mumbai_gas_tracker.py`) | Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli | Sorted Petrol & Diesel, Purple Diesel highlights, Nitrogen tyre availability, Hyundai Venue 2019 care card | **Amazon Pay ICICI Card**: 1% fuel surcharge waiver on ₹400–₹4,000 spend (saves surcharge + 18% GST) | Daily @ 7:00 AM IST (`30 1 * * *`) |

---

## 🔑 Environment Variables & GitHub Secrets

Configure the following secrets in **Settings > Secrets and variables > Actions**:

| Secret Name | Required By | Description | Example / Default |
| :--- | :--- | :--- | :--- |
| `SENDER_EMAIL` | Both | Gmail address sending digest emails | `yourname@gmail.com` |
| `SENDER_PASSWORD` | Both | 16-character Gmail App Password | `abcd efgh ijkl mnop` |
| `RECEIVER_EMAIL` | US Pipeline | Recipient for US digest | `user@example.com` |
| `MUMBAI_RECEIVER_EMAIL` | Mumbai Pipeline | Recipient(s) for Mumbai digest (comma-separated supported) | `dad@example.com` |
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
```

---

## 📁 Repository Structure

```
.
├── .github/workflows/
│   ├── schedule.yml        # US fuel tracker workflow (Cron trigger)
│   └── mumbai_schedule.yml # Mumbai fuel tracker workflow (Daily 7:00 AM IST)
├── gas_tracker.py          # US fuel tracker, GasBuddy parser & Sheets logger
├── mumbai_gas_tracker.py   # Mumbai fuel tracker, station router & vehicle care engine
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
