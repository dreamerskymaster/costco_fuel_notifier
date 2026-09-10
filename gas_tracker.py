import os
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from py_gasbuddy import GasBuddy
import gspread

# --- CONFIGURATION ---
ZIP_CODES = ["06460", "06854", "06901", "10801"] 

# Pulling credentials from Environment Variables (GitHub Secrets)
SENDER_EMAIL = (os.environ.get("SENDER_EMAIL") or "").strip()
SENDER_PASSWORD = (os.environ.get("SENDER_PASSWORD") or "").strip()
RECEIVER_EMAIL = (os.environ.get("RECEIVER_EMAIL") or "").strip() or SENDER_EMAIL

raw_smtp_server = (os.environ.get("SMTP_SERVER") or "").strip()
SMTP_SERVER = raw_smtp_server if raw_smtp_server else "smtp.gmail.com"

raw_smtp_port = (os.environ.get("SMTP_PORT") or "").strip()
SMTP_PORT = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587

SHEET_NAME = os.environ.get("SHEET_NAME", "Fuel Trends")
SHEET_URL = os.environ.get("SHEET_URL")
SHEET_ID = os.environ.get("SHEET_ID")
SERVICE_ACCOUNT_FILE = "service_account.json"


import re

LOCATION_QUERY_PRICES = "query LocationBySearchTerm($brandId: Int, $cursor: String, $fuel: Int, $lat: Float, $lng: Float, $maxAge: Int, $search: String) { locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat lng: $lng maxAge: $maxAge) { results { address { line1 } id name prices { cash { nickname postedTime price } credit { nickname postedTime price } fuelProduct longName } priceUnit currency id latitude longitude } } trends { areaName country today todayLow trend } } }"

def get_card_optimization(station_name, listed_price):
    """
    Calculates the best credit card and net discounted price based on user's Obsidian Vault card portfolio:
    - Citi Costco Anywhere Visa: 4% cash back on gas worldwide (first $7,000/yr). Accepted at Costco Gas (Visa only).
    - Amex Blue Cash Everyday: 3% cash back on US gas (first $6,000/yr). Not accepted at Costco Gas.
    - Bank of America Visa Signature: 1% backup.
    """
    is_costco = "costco" in station_name.lower()
    
    if is_costco:
        card_name = "Citi Costco Visa (4%)"
        reward_pct = "4%"
        discount_rate = 0.04
        card_note = "Visa Only"
    else:
        card_name = "Citi Costco Visa (4%) [or Amex BCE 3%]"
        reward_pct = "4%"
        discount_rate = 0.04
        card_note = "Primary 4% / Secondary 3%"
        
    net_price = round(listed_price * (1 - discount_rate), 2)
    formatted_net = f"${net_price:.2f}"
    
    return {
        "best_card": card_name,
        "reward_pct": reward_pct,
        "discount_rate": discount_rate,
        "net_price": net_price,
        "formatted_net_price": formatted_net,
        "card_note": card_note
    }

async def fetch_gas_prices():
    """
    Fetches regular gas prices for configured ZIP codes via GasBuddy GraphQL API without brand restrictions.

    Establishes an HTTP session with browser headers to extract the CSRF token from GasBuddy,
    queries the GraphQL endpoint for all local gas stations, and constructs Waze deep links for navigation.

    Returns:
        list[dict]: Deduplicated list of all local fuel station dictionaries sorted by price.
    """
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    csrf_token = ""
    try:
        resp = session.get("https://www.gasbuddy.com/home", headers=headers, timeout=10)
        found = re.search(r"window\.gbcsrf\s*=\s*([\"])((?:\\\1|(?:(?!\1).))*)\1", resp.text)
        if found:
            csrf_token = found.group(2)
    except Exception as e:
        print(f"Warning: Could not extract CSRF token: {e}")

    gql_headers = {
        "Content-Type": "application/json",
        "User-Agent": headers["User-Agent"],
        "apollo-require-preflight": "true",
        "Origin": "https://www.gasbuddy.com",
        "Referer": "https://www.gasbuddy.com/home",
        "gbcsrf": csrf_token
    }

    stations_data = []
    for zip_code in ZIP_CODES:
        try:
            payload = {
                "operationName": "LocationBySearchTerm",
                "variables": {"maxAge": 0, "search": zip_code},
                "query": LOCATION_QUERY_PRICES
            }
            r = session.post("https://www.gasbuddy.com/graphql", json=payload, headers=gql_headers, timeout=10)
            if r.status_code == 200:
                res = r.json().get("data", {}).get("locationBySearchTerm", {}).get("stations", {}).get("results", [])
                for station in res:
                    name = station.get("name") or (station.get("address") or {}).get("line1", "Unknown Station")
                    prices = station.get("prices", [])
                    reg_gas = next((p for p in prices if p.get("fuelProduct") == "regular_gas"), None)
                    if not reg_gas:
                        continue
                    price_info = reg_gas.get("credit") or reg_gas.get("cash") or {}
                    price = price_info.get("price")
                    if not price:
                        continue
                    formatted_price = f"${price}"
                    last_updated_str = price_info.get("postedTime")
                    is_stale = False
                    readable_time = "Unknown"
                    if last_updated_str:
                        try:
                            updated_time = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                            readable_time = updated_time.astimezone().strftime("%b %d, %I:%M %p")
                            if datetime.now(timezone.utc) - updated_time > timedelta(hours=12):
                                is_stale = True
                        except Exception:
                            pass
                    
                    search_query = urllib.parse.quote_plus(f"{name} {zip_code}")
                    waze_link = f"https://waze.com/ul?q={search_query}&navigate=yes"
                    
                    card_opt = get_card_optimization(name, price)
                    
                    stations_data.append({
                        "name": name,
                        "zip": zip_code,
                        "distance": station.get("distance", "N/A"),
                        "price": price,
                        "formatted_price": formatted_price,
                        "best_card": card_opt["best_card"],
                        "net_price": card_opt["net_price"],
                        "formatted_net_price": card_opt["formatted_net_price"],
                        "card_note": card_opt["card_note"],
                        "stale": is_stale,
                        "last_updated": readable_time,
                        "waze_link": waze_link
                    })
        except Exception as e:
            print(f"Failed fetching data for {zip_code}: {e}")

    unique_stations = {s["name"] + "_" + str(s["price"]): s for s in stations_data}.values()
    sorted_stations = sorted(list(unique_stations), key=lambda x: x["price"])
    return sorted_stations

def log_to_sheets(stations):
    """
    Logs the lowest fuel price of the day to Google Sheets if it has changed since the last check.

    Args:
        stations (list[dict]): List of station objects sorted by price.

    Returns:
        bool: True if a new price/station was logged or if it's a new day; False if unchanged.
    """
    if not stations:
        return False
        
    best = stations[0]
    today_str = datetime.now().strftime("%Y-%m-%d")
    row = [
        today_str, 
        best["name"], 
        best["zip"], 
        best["formatted_price"]
    ]

    try:
        # Authenticate and open the sheet
        gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
        
        sheet = None
        if SHEET_URL:
            sheet = gc.open_by_url(SHEET_URL).sheet1
        elif SHEET_ID:
            sheet = gc.open_by_key(SHEET_ID).sheet1
        else:
            sheet = gc.open(SHEET_NAME).sheet1
        
        all_rows = sheet.get_all_values()
        
        # Check if the last recorded price/station/date matches current best
        if len(all_rows) > 1: # Header exists
            last_row = all_rows[-1]
            last_date = last_row[0] if len(last_row) > 0 else ""
            last_name = last_row[1] if len(last_row) > 1 else ""
            last_price = last_row[3] if len(last_row) > 3 else ""
            
            # If price and station match previous record on the same day, no change
            if last_date == today_str and last_price == best["formatted_price"] and last_name == best["name"]:
                print(f"Price unchanged ({best['formatted_price']} at {best['name']}). Skipping sheet append.")
                return False

        sheet.append_row(row)
        print("Successfully logged new price to Google Sheets.")
        return True
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Google Sheet '{SHEET_NAME}' not found via Drive search. If shared, provide SHEET_URL or SHEET_ID.")
        return True # Default to True so email delivers even if sheets logging is skipped
    except Exception as e:
        err_msg = e.__cause__ if hasattr(e, "__cause__") and e.__cause__ else e
        print(f"Could not write to Google Sheets: {err_msg}")
        return True


def send_email_smtp(summary, stations):
    """
    Sends email via standard SMTP (e.g. Gmail SMTP with App Password).
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        return False

    recipient = RECEIVER_EMAIL or SENDER_EMAIL
    if not recipient:
        print("SMTP Error: No recipient email specified.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Fuel Update : Norwalk (Optimized Card Discounts)"
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient

    # Plain text version
    text_content = f"Fuel Price Digest & Card Optimization:\n\n{summary}"
    msg.attach(MIMEText(text_content, "plain"))

    # Optional HTML version for rich styling
    html_rows = ""
    for s in stations[:10]:
        stale = " ⚠️ (Stale >12h)" if s["stale"] else ""
        html_rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px; font-weight: bold;">{s['name']} ({s['zip']})</td>
            <td style="padding: 10px; color: #777; text-decoration: line-through;">{s['formatted_price']}</td>
            <td style="padding: 10px; font-size: 13px; color: #1976d2; font-weight: bold;">{s['best_card']}</td>
            <td style="padding: 10px; color: #2e7d32; font-weight: bold; font-size: 15px;">{s['formatted_net_price']} <span style="font-size: 11px; color: #388e3c;">(-4%)</span></td>
            <td style="padding: 10px;"><a href="{s['waze_link']}" style="background-color: #33ccff; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-weight: bold;">🚗 Navigate</a></td>
        </tr>
        """
    
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <h2 style="color: #1976d2; margin-bottom: 5px;">⛽ Daily Fuel Price Digest</h2>
        <p style="font-size: 13px; color: #555; background-color: #e3f2fd; padding: 10px; border-radius: 6px; border-left: 4px solid #1976d2;">
          💳 <strong>Credit Card Savings Applied</strong>: Net prices reflect your highest reward back using <strong>Citi Costco Anywhere Visa (4%)</strong> [or <strong>Amex Blue Cash Everyday 3%</strong>].
        </p>
        <table style="width: 100%; border-collapse: collapse; max-width: 680px; margin-top: 15px;">
          <thead>
            <tr style="background-color: #f5f5f5; text-align: left;">
              <th style="padding: 10px;">Station</th>
              <th style="padding: 10px;">Listed</th>
              <th style="padding: 10px;">Best Card</th>
              <th style="padding: 10px;">Net Price</th>
              <th style="padding: 10px;">Action</th>
            </tr>
          </thead>
          <tbody>
            {html_rows}
          </tbody>
        </table>
      </body>
    </html>
    """
    msg.attach(MIMEText(html_content, "html"))

    try:
        print(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient, msg.as_string())
        server.quit()
        print(f"Digest email sent successfully via SMTP to {recipient}.")
        return True
    except Exception as e:
        print(f"Failed to send email via SMTP: {e}")
        return False


def send_email(stations):
    """
    Sends a digest of top fuel prices to RECEIVER_EMAIL via SMTP or FormSubmit fallback.

    Args:
        stations (list[dict]): List of station objects sorted by price.
    """
    if not stations:
        print("No stations found.")
        return

    summary = ""
    for s in stations[:10]:
        stale = " ⚠️ (Stale >12h)" if s["stale"] else ""
        summary += f"• {s['name']} ({s['zip']}): Listed {s['formatted_price']} | 💳 Net {s['formatted_net_price']} ({s['best_card']})\n  Updated: {s['last_updated']}{stale}\n  🚗 Navigate: {s['waze_link']}\n\n"

    # Try SMTP first if credentials are set
    if SENDER_EMAIL and SENDER_PASSWORD:
        if send_email_smtp(summary, stations):
            return

    # Fallback to FormSubmit AJAX API
    recipient = RECEIVER_EMAIL
    if not recipient:
        print("Error: RECEIVER_EMAIL environment variable is not set.")
        return

    print(f"Attempting email dispatch via FormSubmit to {recipient}...")
    url = f"https://formsubmit.co/ajax/{recipient}"
    payload = {
        "_subject": "Fuel Update : Norwalk (Optimized Card Discounts)",
        "Top_10_Cheapest_Stations": summary,
        "_template": "box"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://formsubmit.co",
        "Origin": "https://formsubmit.co"
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=15)
        res_data = response.json()
        if str(res_data.get("success")).lower() == "true":
            print("Digest email sent successfully via FormSubmit.")
        else:
            print(f"FormSubmit Notice ({response.status_code}): {res_data.get('message')}")
    except Exception as e:
        print(f"Failed to send email via FormSubmit: {e}")


async def main():
    print("Fetching gas prices...")
    stations = await fetch_gas_prices()
    if not stations:
        print("No stations found.")
        return

    price_changed = log_to_sheets(stations)

    # Allow email dispatch on every run by default or via FORCE_EMAIL / ALWAYS_SEND_EMAIL
    always_send = os.environ.get("ALWAYS_SEND_EMAIL", "true").lower() == "true"
    force_email = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

    if price_changed or force_email or always_send:
        print("Sending daily fuel price digest email...")
        send_email(stations)
    else:
        print("Fuel price unchanged since last check. Email notification skipped.")

if __name__ == "__main__":
    asyncio.run(main())


