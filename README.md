# ⛽ Costco & Fuel Price Notifier

An automated, daily gas price tracker built with Python and GitHub Actions. It monitors live regular gas prices across local commute routes, logs daily price trends to a Google Sheet, and emails a formatted top-10 digest equipped with **one-tap Waze navigation deep links**.

---

## 🌟 Key Features

- 📍 **Commute Route Coverage**: Queries real-time gas prices for configured ZIP codes (`06460`, `06854`, `06901`, `10801`).
- ⛽ **All Local Brands**: Captures pricing for all local stations (Costco, Stop & Shop, CITGO, Shell, Mobil, Speedway, 7-Eleven, Cumberland Farms, etc.).
- 🚗 **Waze Deep Links**: Generates URL-encoded Waze navigation links (`https://waze.com/ul?q=...&navigate=yes`) for instant one-tap navigation.
- 📊 **Google Sheets Trend Tracking**: Automatically logs the absolute cheapest daily gas price into a `Fuel Trends` Google Sheet via Service Account credentials.
- 📬 **FormSubmit Integration**: Delivers clean daily digest emails without requiring SMTP server configuration or Gmail App Passwords.
- ⏰ **Automated Schedule**: Executes automatically Monday through Friday at 10:30 AM UTC via GitHub Actions.

---

## 📋 System Requirements & Dependencies

- Python 3.10+
- Dependencies listed in `requirements.txt`:
  - `py_gasbuddy`
  - `gspread`
  - `google-auth`
  - `requests`
  - `backoff`
  - `aiofiles`

---

## 🚀 Quick Setup & Configuration

### 1. Google Cloud Service Account Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select your project (e.g. `northeasternskymaster`).
3. Enable both the **Google Sheets API** and **Google Drive API**.
4. Create a **Service Account** (e.g. `bmwnotifier@northeasternskymaster.iam.gserviceaccount.com`).
5. Generate and download a **JSON Key**.

### 2. Google Sheet Setup
1. Create a Google Sheet named **`Fuel Trends`**.
2. Click **Share** in the top-right corner.
3. Add your service account email (`bmwnotifier@northeasternskymaster.iam.gserviceaccount.com`) as an **Editor**.

### 3. GitHub Repository Secrets
In your GitHub repository, navigate to **Settings > Secrets and variables > Actions** and add these two secrets:

| Secret Name | Description | Example / Value |
| :--- | :--- | :--- |
| `RECEIVER_EMAIL` | Email address to receive daily fuel digests | `ajithsri2000@gmail.com` |
| `GCP_SERVICE_ACCOUNT` | Entire raw JSON content of your Service Account Key | `{"type": "service_account", ...}` |

*(Optional)*: Add `SHEET_URL` if you wish to link directly to a specific spreadsheet URL.

---

## 💻 Local Installation & Testing

```bash
# Clone the repository
git clone https://github.com/dreamerskymaster/costco_fuel_notifier.git
cd costco_fuel_notifier

# Set up Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Place your GCP key file locally (ignored by .gitignore)
cp /path/to/your/key.json service_account.json

# Run local test execution
RECEIVER_EMAIL="your_email@gmail.com" python gas_tracker.py
```

---

## ⚡ Rate Limits & Safety Quotas

| Component | Limit Threshold | Project Usage | Quota Status |
| :--- | :--- | :--- | :--- |
| **GasBuddy API** | ~20 req/min (Cloudflare) | 4 req/day | 🟢 100% Safe |
| **FormSubmit** | 50 submissions/day | 1 email/day | 🟢 100% Safe |
| **Google Sheets API** | 60 write req/min | 1 append/day | 🟢 100% Safe |
| **GitHub Actions** | 2,000 billable min/month | ~22 min/month | 🟢 100% Safe |
| **Waze Links** | Unlimited | Unlimited | 🟢 100% Safe |

For detailed system design and maintenance guidelines, see [ARCHITECTURE.md](file:///Users/skymaster/Library/CloudStorage/OneDrive-NortheasternUniversity/Projects/Costco_Fuel_Notifier/ARCHITECTURE.md).

---

## 📄 License

MIT License. Free to use and modify.
