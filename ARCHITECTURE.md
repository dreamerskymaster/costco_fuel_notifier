# Architecture & System Maintenance Guide

## System Overview

The **Costco & Fuel Price Notifier** is an automated dual-pipeline system designed to monitor fuel prices across specified commute corridors in the US and India, log price trends to Google Sheets, optimize credit card cashback/waiver rules, and deliver daily HTML email digests.

```
                                  ┌──────────────────────────┐
                                  │   GitHub Actions Cron    │
                                  └────────────┬─────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
          ┌────────────────────────┐                      ┌────────────────────────┐
          │     gas_tracker.py     │                      │ mumbai_gas_tracker.py  │
          │      (US Pipeline)     │                      │   (Mumbai Pipeline)    │
          └────────────┬───────────┘                      └────────────┬───────────┘
                       │                                               │
       ┌───────────────┼───────────────┐                               │
       ▼               ▼               ▼                               ▼
 ┌───────────┐   ┌───────────┐   ┌───────────┐                  ┌─────────────┐
 │ GasBuddy  │   │  Google   │   │   SMTP    │                  │    SMTP     │
 │  GraphQL  │   │  Sheets   │   │ Mail Engine│                 │ Mail Engine │
 └───────────┘   └───────────┘   └─────┬─────┘                  └──────┬──────┘
                                       │                               │
                                       ▼                               ▼
                                📬 US Digest Email              📬 Mumbai Digest Email
```

---

## 1. US Pipeline Architecture (`gas_tracker.py`)

- **Target Route**: ZIP codes `06460`, `06854`, `06901`, `10801` (Norwalk/Stamford, CT corridor).
- **Scraper Engine**:
  - Fetches GasBuddy CSRF token (`window.gbcsrf`) from `https://www.gasbuddy.com/home` using browser User-Agent headers.
  - Executes GraphQL queries (`LocationBySearchTerm`) against `https://www.gasbuddy.com/graphql`.
  - Filters stale prices (> 12 hours) with warning flags (`⚠️ Stale >12h`).
  - Generates Waze 1-tap navigation deep links: `https://waze.com/ul?q=<Station_Name>+<ZIP>&navigate=yes`.
- **Google Sheets Trend Logger**:
  - Authenticates via GCP Service Account key (`GCP_SERVICE_ACCOUNT` secret or `service_account.json`).
  - Appends daily cheapest station row `[YYYY-MM-DD, Name, ZIP, Price]` to spreadsheet `Fuel Trends`.
- **Credit Card Reward Optimization Rules**:
  - **Costco Gas**: Citi Costco Anywhere Visa (4% cash back; Visa only accepted).
  - **Standalone Gas (CITGO, Shell, Mobil, Speedway, etc.)**: Citi Costco Visa (4%) or Amex Blue Cash Everyday (3%).
  - **Supermarket Gas (Stop & Shop)**: Excluded from 4%/3% bonus categories by MCC rules (1% base reward).

---

## 2. Mumbai Pipeline Architecture (`mumbai_gas_tracker.py`)

- **Target Corridor**: Versova → Andheri West → JVLR → Powai → Airoli → Ghansoli (Navi Mumbai commute).
- **Station Database & Route Coverage**:
  - 12 key fuel stations across IOCL, BPCL, HPCL, and Shell.
  - Supports Petrol variants (Regular 91, Speed 95, Power 95, Shell V-Power) and Diesel.
- **Sorting & Formatting Logic**:
  - **Dual Sorting**: Petrol section sorted lowest to highest price, followed by Diesel section sorted lowest to highest price.
  - **Visual Distinction**: Diesel prices highlighted in **Purple (`#6b21a8`)**.
  - **Tyre Care Indicators**: Distinguishes stations with Nitrogen inflation (`🎈 Nitrogen`) vs Air (`💨 Air Only`).
- **Vehicle Profile Care Card**:
  - Customized for **Hyundai Venue (2019 Model)**.
  - Recommended cold tyre pressure: **33 PSI** (front & rear).
  - Full tank cost calculation based on 45L fuel tank capacity.
- **Credit Card Surcharge Waiver Engine**:
  - **Amazon Pay ICICI Credit Card**: 1% Fuel Surcharge Waiver on transactions between ₹400 and ₹4,000.
  - Saves 1% surcharge fee + 18% GST on surcharge fee.
  - Explains 0% reward point accumulation on fuel MCC transactions per RBI / bank rules.

---

## 3. Email Engine & Dispatcher

- **Library**: Built-in Python `smtplib` and `email.mime` (no external API keys required).
- **Transport**: Standard SMTP over TLS via `smtp.gmail.com:587`.
- **Mobile & Web Responsive CSS**:
  - Container width constrained (`max-width: 600px`).
  - Action buttons styled with `display: inline-block; white-space: nowrap;` to prevent text-wrapping or emoji breaks on iOS/Android mail clients.
- **Recipient Routing**:
  - US pipeline sends to `RECEIVER_EMAIL`.
  - Mumbai pipeline sends to `MUMBAI_RECEIVER_EMAIL` (supports multiple comma-separated addresses).

---

## 4. Quotas, Safety Limits & Performance

| Service | Quota / Limit | Project Usage | Safety Status |
| :--- | :--- | :--- | :--- |
| **GasBuddy Scraper** | ~20 req/min (Cloudflare) | 4 req/run | 🟢 Safe |
| **Google Sheets API** | 60 write req/min | 1 write/run | 🟢 Safe |
| **SMTP Delivery** | 500 emails/day (Gmail limit) | 1-2 emails/day | 🟢 Safe |
| **GitHub Actions** | 2,000 free min/month | ~25 min/month | 🟢 Safe |

---

## 5. Maintenance & Troubleshooting

### 1. Handling Empty Environment Variables
GitHub Actions secrets evaluate to empty strings `""` when undefined, which breaks simple fallback statements like `int(os.environ.get("SMTP_PORT", 587))`.
- **Fix**: Both scripts strip strings and validate numeric strings using `.strip().isdigit()`.

### 2. GasBuddy CSRF Token Extraction Failures
If GasBuddy changes their HTML entrypoint:
- Inspect `gas_tracker.py` line fetching `window.gbcsrf`.
- Verify user-agent header string matches modern desktop browsers.

### 3. Adding New Stations / Corridors
- **US**: Update `ZIP_CODES` list in `gas_tracker.py`.
- **Mumbai**: Add station entries to `MUMBAI_STATIONS` dictionary in `mumbai_gas_tracker.py` with attributes (`name`, `area`, `brand`, `petrol_prices`, `diesel_price`, `nitrogen`).
