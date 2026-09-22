# Architecture & System Maintenance Guide

## System Overview

The **Costco & Fuel Price Notifier** is an automated dual-pipeline system designed to monitor fuel prices across specified commute corridors in the US and India, log price trends to Google Sheets, optimize credit card cashback/waiver rules, and deliver daily HTML email digests.

```
                          ┌──────────────────────────┐
                          │    GitHub Actions Cron   │
                          └────────────┬─────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
┌────────────────┐           ┌────────────────────┐        ┌────────────────────┐
│ gas_tracker.py │           │mumbai_gas_tracker  │        │ usd_inr_tracker.py │
│  (US Pipeline) │           │  (Mumbai Pipeline) │        │ (USD→INR Pipeline) │
└───────┬────────┘           └─────────┬──────────┘        └─────────┬──────────┘
        │                              │                             │
  ┌─────┼─────┐                        │            ┌────────────────┼────────────────┐
  ▼     ▼     ▼                        ▼            ▼                ▼                ▼
┌──────┐┌──────┐┌──────┐          ┌────────┐  ┌───────────┐   ┌───────────┐   ┌──────────────┐
│GasBud││Google││ SMTP │          │  SMTP  │  │Frankfurter│   │   Wise    │   │ fx_signals + │
│GraphQL││Sheets││Mailer│         │ Mailer │  │ /Yahoo/ER │   │Comparison │   │ fx_backtest  │
└──────┘└──────┘└──┬───┘          └───┬────┘  └─────┬─────┘   └─────┬─────┘   └──────┬───────┘
                   │                  │             └─────────┬─────┴────────────────┘
                   ▼                  ▼                       ▼
            📬 US Digest      📬 Mumbai Digest          ┌────────────┐
                                                        │SMTP Mailer │
                                                        └─────┬──────┘
                                                              ▼
                                                   📬 Remittance Brief / ⚡ Alert
                                                              │
                                                              ▼
                                              data/history.csv + data/state.json
                                                    (committed by CI)
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

## 3. USD→INR Remittance Pipeline (`usd_inr_tracker.py`)

- **Purpose**: decide how to send ~$3,000/month to India around the 15th, with ~14 days of flexibility before a forced send.
- **Modules**: `fx_data.py` (network I/O, disk cache, source fallbacks), `fx_signals.py` (model + verdict), `fx_providers.py` (all-in cost ranking, markup drift), `fx_backtest.py` (walk-forward validation), `fx_config.py` (env parsing).
- **Modes**: `--mode digest` (daily email), `--mode alert` (silent unless triggered), `--mode dry-run` (writes `preview.html`, sends nothing).

### 3.1 What the backtest settled

Walk-forward over **45 monthly send windows** (5y ECB daily data, no lookahead, forced send at the deadline), on a $3,000 transfer:

| Strategy | vs payday-send | INR/month | Window capture |
| :--- | ---: | ---: | ---: |
| Send on payday (baseline) | — | — | 51% |
| Optimal-stopping model rule | −0.019% | −49 | 48% |
| **Percentile ≥85 rule (deployed)** | **+0.056%** | **+142** | **58%** |
| Perfect hindsight (upper bound) | +0.439% | +1,163 | 100% |

Timing is near worthless at this size — even a crystal ball is worth ~₹1,163/month, and the deployed rule's edge is not statistically significant (t ≈ 1.25). Provider selection, by contrast, is a **certain ₹9,000–10,000 spread** between best and worst on a single transfer. The email is ordered accordingly: provider first, timing second with its own track record printed beside the verdict.

### 3.2 The model

`log S_t = a + b·t + x_t`, with `x_t = φ·x_(t−1) + e_t`.

- `b·t` carries structural depreciation; `x_t` carries the reverting part, which is what makes a *level* informative at all.
- Forecasts anchor on today's close and apply increments: `log S_(T+h) = log S_T + b·h + (φ^h − 1)·x_T`. **Invariant: horizon 0 must reproduce spot exactly**, and the reservation rate at `days_left = 0` must equal spot. Rebuilding the trend line from `intercept + slope·t` instead silently misplaces the level by ~7%, because the residual was fitted against the unshrunk slope.
- The reservation rate is backward induction over the remaining window under CRRA utility. It is **reported, not obeyed**: its drift term always argues for waiting in a depreciating currency, which would recommend holding a rate already beating 90% of the year.
- `DRIFT_SHRINK` (default 0.5) halves the fitted trend. Without it, years of depreciation extrapolate into "wait forever".

### 3.3 Data sources and degradation

| Source | Used for | Notes |
| :--- | :--- | :--- |
| Frankfurter (ECB) | Daily history | **Primary**. Official reference rates, no key, no throttling, 7+ years |
| Yahoo Finance | Intraday spot, DXY/Brent/Nifty | Fresher but rate-limits by IP; 429s are routine |
| open.er-api.com | Spot fallback | Key-less, daily |
| Wise Comparison API | Provider rates and fees | Public; returns competitors' quotes too |

History has three independent paths (Frankfurter → Yahoo → on-disk cache) and spot falls back to the daily reference, so only a total failure of everything aborts a run. Context markers (DXY/Brent/Nifty) are Yahoo-only and make a **single** attempt — they explain the level rather than feeding the model, and retrying them three times at 6s backoff cost ~2 minutes per run.

### 3.4 Alerting

Fires on: level above `ALERT_PERCENTILE` (default 85th), a new 52-week high, the deadline arriving, or a provider's promotional spread visibly expiring. `ALERT_COOLDOWN_DAYS` (default 3) stops a sustained good level from mailing every three hours; the deadline overrides the cooldown.

---

## 4. Email Engine & Dispatcher

- **Library**: Built-in Python `smtplib` and `email.mime` (no external API keys required).
- **Transport**: Standard SMTP over TLS via `smtp.gmail.com:587`.
- **Mobile & Web Responsive CSS**:
  - Container width constrained (`max-width: 600px`).
  - Action buttons styled with `display: inline-block; white-space: nowrap;` to prevent text-wrapping or emoji breaks on iOS/Android mail clients.
- **Recipient Routing**:
  - US pipeline sends to `RECEIVER_EMAIL`.
  - Mumbai pipeline sends to `MUMBAI_RECEIVER_EMAIL` (supports multiple comma-separated addresses).

---

## 5. Quotas, Safety Limits & Performance

| Service | Quota / Limit | Project Usage | Safety Status |
| :--- | :--- | :--- | :--- |
| **GasBuddy Scraper** | ~20 req/min (Cloudflare) | 4 req/run | 🟢 Safe |
| **Google Sheets API** | 60 write req/min | 1 write/run | 🟢 Safe |
| **SMTP Delivery** | 500 emails/day (Gmail limit) | 1-2 emails/day | 🟢 Safe |
| **GitHub Actions** | 2,000 free min/month | ~65 min/month | 🟢 Safe |
| **Frankfurter (ECB)** | no published limit | 1 req/run | 🟢 Safe |
| **Yahoo Finance** | throttles by IP, undocumented | up to 4 req/run | 🟡 429s routine, handled |
| **Wise Comparison API** | no published limit | 1 req/run | 🟢 Safe |

---

## 6. Maintenance & Troubleshooting

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

### 4. USD→INR: Yahoo 429s Are Expected

Not a defect. History falls back to Frankfurter, spot to open.er-api, and `actions/cache` carries `data/cache/` between runs. Symptom: the "what is driving the pair" card disappears from the email, because DXY/Brent/Nifty have no non-Yahoo source. Cosmetic — do not "fix" it by adding retries.

### 5. USD→INR: `.gitignore` Blanket `*.json`

The repo-wide `*.json` rule exists to protect `service_account.json`, but it also shadows `data/events.json` and `data/state.json`. Both are explicitly negated with `!data/...`. If you add another tracked JSON under `data/`, negate it too or it will vanish silently.

### 6. USD→INR: numpy matmul on macOS

Weighted sums in `fx_signals.py` are written as elementwise product plus reduction rather than `@` **on purpose**: the Accelerate BLAS backend raises spurious divide-by-zero and overflow flags on finite, well-scaled inputs, which buries real warnings. Do not simplify them back. Relatedly, the CRRA certainty equivalent normalises by a reference level before exponentiating — raising rates near 90 to the −11th power underflows otherwise. A CE is scale-equivariant, so this is exact, not an approximation.

### 7. USD→INR: Re-validating the Rules

`fx_backtest.run_backtest(..., rule="model")` vs `rule="percentile"` compares the two. Re-run after any change to `fx_signals.py`. If the percentile rule stops beating the baseline, change the email's framing to match rather than quietly ignoring the finding. `python -m unittest test_fx_signals` covers the model invariants, window rollover and deadline enforcement (21 tests).
