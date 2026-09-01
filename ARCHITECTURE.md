# Architecture & System Maintenance Guide

## Overview

The **Costco & Fuel Notifier** is an automated pipeline designed to monitor local regular gas prices along an active commute route, log price trends over time to Google Sheets, and deliver a daily digest email with Waze navigation deep links.

---

## Component Architecture

```
                               ┌───────────────────────────┐
                               │   GitHub Actions Cron     │
                               │   (Mon-Fri @ 10:30 UTC)   │
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                                  ┌────────────────────┐
                                  │   gas_tracker.py   │
                                  └──────────┬─────────┘
                                             │
      ┌──────────────────────────────────────┼──────────────────────────────────────┐
      │                                      │                                      │
      ▼                                      ▼                                      ▼
┌───────────┐                        ┌──────────────┐                       ┌──────────────┐
│ GasBuddy  │                        │ Google Sheets│                       │  FormSubmit  │
│  GraphQL  │                        │  API (v4)    │                       │  Web-to-Email│
└─────┬─────┘                        └──────┬───────┘                       └──────┬───────┘
      │                                     │                                      │
      ▼                                     ▼                                      ▼
 Fetch & Sort                           Append Row to                           Deliver Digest
  Gas Prices                            'Fuel Trends'                           Email to User
```

---

## 1. Data Fetcher & Scraper Engine
- **Target ZIP Codes**: `06460`, `06854`, `06901`, `10801`
- **Session Handling**:
  - Connects to `https://www.gasbuddy.com/home` with browser headers to extract the session CSRF token (`window.gbcsrf`).
  - Submits GraphQL `LocationBySearchTerm` requests to `https://www.gasbuddy.com/graphql`.
- **Waze Deep Links**:
  Constructs deep links for one-tap navigation in the Waze mobile app:
  `https://waze.com/ul?q=<Station_Name>+<ZIP_or_Address>&navigate=yes`
- **Data Integrity**:
  - Parses regular gas credit/cash prices.
  - Flags prices updated > 12 hours ago with `⚠️ (Stale >12h)`.
  - Removes duplicate entries per station and sorts ascending by lowest price.

---

## 2. Google Sheets Integration (`gspread`)
- **Sheet Name**: `Fuel Trends`
- **Logged Data**: Daily cheapest station entry `[YYYY-MM-DD, Station Name, ZIP, Price]`.
- **Authentication**: GCP Service Account JSON key (`service_account.json`).
- **Access Methods**: Tries `SHEET_URL` or `SHEET_ID` if provided, falling back to title search (`Fuel Trends`).

---

## 3. Email Delivery System (FormSubmit)
- **Endpoint**: `https://formsubmit.co/ajax/{RECEIVER_EMAIL}`
- **Security**: Dispatched as form POST data with `Referer: https://formsubmit.co` header.
- **Content**: Formatted top 10 cheapest stations digest with Waze navigation links.

---

## Rate Limits & Quotas

| Service | Quota / Limit | Project Usage | Status |
| :--- | :--- | :--- | :--- |
| **GasBuddy Scraping** | ~20 req/min (Cloudflare) | 4 req/day | 🟢 Safe |
| **FormSubmit API** | 50 submissions/day | 1 email/day | 🟢 Safe |
| **Google Sheets API** | 60 write req/min | 1 append/day | 🟢 Safe |
| **GitHub Actions** | 2,000 billable min/month | ~22 min/month | 🟢 Safe |
| **Waze Links** | Unlimited | Unlimited | 🟢 Safe |

---

## Troubleshooting & Maintenance Guide

### Problem 1: `SpreadsheetNotFound` or `APIError: 403`
- **Cause**: Google Sheets API or Drive API disabled, or service account email not shared on the sheet.
- **Fix**:
  1. Ensure both **Google Sheets API** and **Google Drive API** are enabled on GCP Project `northeasternskymaster`.
  2. Share the `Fuel Trends` sheet with `bmwnotifier@northeasternskymaster.iam.gserviceaccount.com` as **Editor**.

### Problem 2: FormSubmit `This form needs Activation`
- **Cause**: FormSubmit requires a one-time email confirmation for new receiver addresses.
- **Fix**: Check `RECEIVER_EMAIL` inbox for the FormSubmit activation link and click **Activate Form**.

### Problem 3: GasBuddy `Warning: Could not extract CSRF token`
- **Cause**: GasBuddy updated their HTML response or user-agent fingerprint.
- **Fix**: Update the `User-Agent` header string in `gas_tracker.py`.
