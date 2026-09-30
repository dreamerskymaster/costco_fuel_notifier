import os
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone(timedelta(hours=-4))
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import gspread
import re

# --- CONFIGURATION ---
# Commute corridor: Norwalk, CT (Home) -> Stamford, CT -> New Rochelle, NY -> Jersey City & Bayonne, NJ
# Removed Milford, CT (06460) as requested.
ZIP_CODES = ["06854", "06901", "10801", "07608", "07306", "07002"]

ZIP_META = {
    "06854": {"city": "Norwalk", "state": "CT", "label": "Norwalk (Home)"},
    "06901": {"city": "Stamford", "state": "CT", "label": "Stamford (I-95)"},
    "10801": {"city": "New Rochelle", "state": "NY", "label": "New Rochelle (Costco)"},
    "07608": {"city": "Teterboro", "state": "NJ", "label": "Teterboro (Costco / GWB Route)"},
    "07306": {"city": "Jersey City", "state": "NJ", "label": "Jersey City (Westside Ave)"},
    "07002": {"city": "Bayonne", "state": "NJ", "label": "Bayonne (Costco / ~4 mi South)"},
}

JERSEY_CITY_DEST = "850 Westside Ave, Jersey City, NJ"
NORWALK_ORIGIN = "Norwalk, CT"

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

LOCATION_QUERY_PRICES = (
    "query LocationBySearchTerm($brandId: Int, $cursor: String, $fuel: Int, $lat: Float, $lng: Float, $maxAge: Int, $search: String) { "
    "locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { "
    "stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat lng: $lng maxAge: $maxAge) { "
    "results { address { line1 } id name prices { cash { nickname postedTime price } credit { nickname postedTime price } fuelProduct longName } priceUnit currency id latitude longitude } "
    "} trends { areaName country today todayLow trend } } }"
)


def get_card_optimization(station_name, listed_price):
    """
    Calculates the best credit card and net discounted price based on user's card portfolio:
    1. Costco Gas:
       - Citi Costco Anywhere Visa: 4% cash back (Costco fuel pumps ONLY accept Visa).
       - Amex & Mastercard: NOT accepted at US Costco fuel pumps.
    2. Standalone Gas Stations (Mobil, Shell, CITGO, Speedway, 7-Eleven, Cumberland Farms, Global, BP, Exxon, Lukoil, QuickChek):
       - Citi Costco Anywhere Visa: 4% cash back (Primary, up to $7,000/yr).
       - Amex Blue Cash Everyday: 3% cash back (Secondary, up to $6,000/yr).
    3. Supermarket / Superstore Gas Pumps (Stop & Shop, Kroger, Sam's Club, BJ's, Walmart):
       - Excluded from 4% Citi and 3% Amex bonus categories by issuer MCC rules. Earns base 1%.
    """
    name_lower = station_name.lower()
    
    is_costco = "costco" in name_lower
    is_grocery_superstore = any(brand in name_lower for brand in ["stop & shop", "stop and shop", "kroger", "sam's", "sams", "bj's", "bjs", "walmart", "target", "shoprite", "giant"])
    
    if is_costco:
        card_name = "Citi Costco (4%)"
        reward_pct = "4%"
        discount_rate = 0.04
        card_note = "Visa Only"
    elif is_grocery_superstore:
        card_name = "Base Rate (1%)"
        reward_pct = "1%"
        discount_rate = 0.01
        card_note = "Supermarket Gas Excluded from 4%/3%"
    else:
        card_name = "Citi 4% / Amex 3%"
        reward_pct = "4%"
        discount_rate = 0.04
        card_note = "Standalone Gas Station"
        
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


def get_commute_mode() -> str:
    """
    Determines current commute context based on US Eastern time or explicit override:
    - Friday afternoon (12:00 PM - 7:00 PM): FRIDAY_DEPARTURE (Norwalk -> Jersey City)
    - Sunday afternoon/evening (12:00 PM - 8:00 PM): SUNDAY_RETURN (Jersey City -> Norwalk)
    - Otherwise: REGULAR
    """
    env_mode = (os.environ.get("COMMUTE_MODE") or "").strip().lower()
    if env_mode in ["friday", "friday_departure"]:
        return "FRIDAY_DEPARTURE"
    if env_mode in ["sunday", "sunday_return"]:
        return "SUNDAY_RETURN"

    now_et = datetime.now(ET)
    weekday = now_et.weekday()  # Monday is 0, Friday is 4, Sunday is 6
    hour = now_et.hour

    if weekday == 4 and 12 <= hour < 19:
        return "FRIDAY_DEPARTURE"
    if weekday == 6 and 12 <= hour < 20:
        return "SUNDAY_RETURN"

    return "REGULAR"


async def fetch_gas_prices():
    """
    Fetches regular gas prices across the Norwalk -> Jersey City corridor via GasBuddy GraphQL API.
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
        meta = ZIP_META.get(zip_code, {"city": "Unknown", "state": "", "label": zip_code})
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
                    if not price or price <= 0:
                        continue
                    formatted_price = f"${price:.2f}"
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
                    maps_link = f"https://www.google.com/maps/search/?api=1&query={search_query}"
                    
                    card_opt = get_card_optimization(name, price)
                    
                    stations_data.append({
                        "name": name,
                        "zip": zip_code,
                        "city": meta["city"],
                        "state": meta["state"],
                        "area_label": meta["label"],
                        "distance": station.get("distance", "N/A"),
                        "price": price,
                        "formatted_price": formatted_price,
                        "best_card": card_opt["best_card"],
                        "net_price": card_opt["net_price"],
                        "formatted_net_price": card_opt["formatted_net_price"],
                        "card_note": card_opt["card_note"],
                        "stale": is_stale,
                        "last_updated": readable_time,
                        "waze_link": waze_link,
                        "maps_link": maps_link,
                    })
        except Exception as e:
            print(f"Failed fetching data for {zip_code}: {e}")

    unique_stations = {s["name"] + "_" + s["zip"] + "_" + str(s["price"]): s for s in stations_data}.values()
    sorted_stations = sorted(list(unique_stations), key=lambda x: x["net_price"])
    return sorted_stations


def log_to_sheets(stations):
    """
    Logs the lowest fuel price of the day to Google Sheets if it has changed since the last check.
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
        gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
        
        sheet = None
        if SHEET_URL:
            sheet = gc.open_by_url(SHEET_URL).sheet1
        elif SHEET_ID:
            sheet = gc.open_by_key(SHEET_ID).sheet1
        else:
            sheet = gc.open(SHEET_NAME).sheet1
        
        all_rows = sheet.get_all_values()
        
        if len(all_rows) > 1:
            last_row = all_rows[-1]
            last_date = last_row[0] if len(last_row) > 0 else ""
            last_name = last_row[1] if len(last_row) > 1 else ""
            last_price = last_row[3] if len(last_row) > 3 else ""
            
            if last_date == today_str and last_price == best["formatted_price"] and last_name == best["name"]:
                print(f"Price unchanged ({best['formatted_price']} at {best['name']}). Skipping sheet append.")
                return False

        sheet.append_row(row)
        print("Successfully logged new price to Google Sheets.")
        return True
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Google Sheet '{SHEET_NAME}' not found via Drive search. If shared, provide SHEET_URL or SHEET_ID.")
        return True
    except Exception as e:
        err_msg = e.__cause__ if hasattr(e, "__cause__") and e.__cause__ else e
        print(f"Could not write to Google Sheets: {err_msg}")
        return True


def build_email_content(stations, mode: str):
    """
    Builds customized plain text and responsive HTML content with NJ vs CT arbitrage tips.
    """
    ct_stations = [s for s in stations if s["state"] == "CT"]
    nj_stations = [s for s in stations if s["state"] == "NJ"]

    lowest_ct = min(ct_stations, key=lambda x: x["net_price"]) if ct_stations else None
    lowest_nj = min(nj_stations, key=lambda x: x["net_price"]) if nj_stations else None

    price_diff = 0.0
    tank_savings = 0.0
    if lowest_ct and lowest_nj:
        price_diff = round(lowest_ct["net_price"] - lowest_nj["net_price"], 2)
        tank_savings = round(price_diff * 14.0, 2)  # Standard 14 gal fill

    # Commute Mode specifics
    route_gmaps = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote_plus(NORWALK_ORIGIN)}&destination={urllib.parse.quote_plus(JERSEY_CITY_DEST)}&travelmode=driving"
    route_waze = f"https://waze.com/ul?q={urllib.parse.quote_plus(JERSEY_CITY_DEST)}&navigate=yes"

    if mode == "FRIDAY_DEPARTURE":
        subject = f"⛽ Friday Commute: Norwalk → Jersey City (Save ~${tank_savings:.0f} in NJ!)"
        banner_title = "🚗 Friday Pre-Departure Alert: Norwalk → 850 Westside Ave, Jersey City"
        banner_bg = "#ecfdf5"
        banner_border = "#a7f3d0"
        banner_text_color = "#065f46"
        banner_body = f"""
        • 💡 <strong>State Arbitrage Strategy</strong>: Gas in New Jersey is <strong>${price_diff:.2f}/gal cheaper</strong> than Norwalk/CT!<br>
        • ⛽ <strong>Recommendation</strong>: If you have enough gas for the ~60-mile drive (~3–4 gallons), <strong>WAIT to fill up in NJ</strong>! A 14-gallon full fill in NJ saves you <strong>~${tank_savings:.2f}</strong>.<br>
        • 🏆 <strong>Best NJ Stops</strong>: <strong>Costco Bayonne (07002)</strong> is just 4 miles south of 850 Westside Ave (or <strong>Costco Teterboro 07608</strong> right off Route 46/I-80).<br>
        • ⚠️ Running on empty in Norwalk? Splash only 2–3 gallons locally to reach NJ, then top off!
        """
    elif mode == "SUNDAY_RETURN":
        subject = f"⛽ Sunday Return Alert: Fill Up in NJ Before Heading Back to Norwalk!"
        banner_title = "🚗 Sunday Pre-Return Alert: Jersey City → Norwalk, CT"
        banner_bg = "#eff6ff"
        banner_border = "#bfdbfe"
        banner_text_color = "#1e40af"
        banner_body = f"""
        • 🚨 <strong>Action Item</strong>: <strong>DO NOT cross back into NY/CT on an empty tank!</strong><br>
        • 💰 <strong>Lock In NJ Rates</strong>: Refueling at <strong>Costco Bayonne (07002)</strong> or in Jersey City before departure saves you <strong>${price_diff:.2f}/gal</strong> (~<strong>${tank_savings:.2f}</strong> on a full tank) versus filling up in Norwalk or Stamford.<br>
        • 📍 <strong>Closest Station</strong>: Costco Bayonne (21 E 71st St) is just ~4.5 miles south of 850 Westside Ave down Route 440.
        """
    else:
        subject = f"⛽ Fuel Digest: Norwalk ↔ Jersey City Corridor (Costco & Card Optimizer)"
        banner_title = "📊 Corridor Fuel Arbitrage: Connecticut vs New Jersey"
        banner_bg = "#f0f9ff"
        banner_border = "#bae6fd"
        banner_text_color = "#0369a1"
        banner_body = f"""
        • 📍 <strong>Commute Route</strong>: Norwalk, CT ↔ 850 Westside Ave, Jersey City, NJ.<br>
        • 💰 <strong>Price Benchmark</strong>: Lowest CT Net: <strong>${lowest_ct['formatted_net_price'] if lowest_ct else 'N/A'}</strong> ({lowest_ct['name'] if lowest_ct else ''}) vs Lowest NJ Net: <strong>${lowest_nj['formatted_net_price'] if lowest_nj else 'N/A'}</strong> ({lowest_nj['name'] if lowest_nj else ''}).<br>
        • 💡 <strong>Savings</strong>: Filling up in NJ saves you <strong>${price_diff:.2f}/gal</strong> (~<strong>${tank_savings:.2f}</strong> per 14-gal fill).
        """

    # Plain text summary
    text_summary = f"{banner_title}\n\n"
    if lowest_ct and lowest_nj:
        text_summary += f"Lowest CT Price: {lowest_ct['formatted_net_price']} ({lowest_ct['name']})\n"
        text_summary += f"Lowest NJ Price: {lowest_nj['formatted_net_price']} ({lowest_nj['name']})\n"
        text_summary += f"NJ Arbitrage Savings: ${price_diff:.2f}/gal (~${tank_savings:.2f} on a 14-gal tank)\n\n"

    text_summary += "TOP FUEL STATIONS (Sorted by Net Discounted Price):\n"
    for s in stations[:12]:
        text_summary += (
            f"• {s['name']} - {s['area_label']} [{s['state']}]: Listed {s['formatted_price']} | "
            f"Net {s['formatted_net_price']} ({s['best_card']})\n"
            f"  Maps: {s['maps_link']} | Waze: {s['waze_link']}\n\n"
        )

    # HTML table rows
    html_rows = ""
    for idx, s in enumerate(stations[:14]):
        bg_color = "#f8fafc" if idx % 2 == 1 else "#ffffff"
        
        state_badge = (
            '<span style="background-color:#dcfce7;color:#15803d;padding:2px 5px;border-radius:3px;font-size:10px;font-weight:700;">NJ</span>'
            if s["state"] == "NJ"
            else (
                '<span style="background-color:#e0f2fe;color:#0369a1;padding:2px 5px;border-radius:3px;font-size:10px;font-weight:700;">CT</span>'
                if s["state"] == "CT"
                else '<span style="background-color:#fef3c7;color:#b45309;padding:2px 5px;border-radius:3px;font-size:10px;font-weight:700;">NY</span>'
            )
        )
        is_costco = "costco" in s["name"].lower()
        costco_star = " 🌟" if is_costco else ""

        html_rows += f"""
        <tr style="background-color:{bg_color};border-bottom:1px solid #e2e8f0;">
          <td style="padding:10px 8px;font-weight:600;color:#0f172a;font-size:13px;">
            {s['name']}{costco_star}<br>
            <span style="font-size:11px;color:#64748b;font-weight:normal;">{state_badge} {s['area_label']}</span>
          </td>
          <td style="padding:10px 8px;color:#94a3b8;text-decoration:line-through;font-size:12px;white-space:nowrap;">
            {s['formatted_price']}
          </td>
          <td style="padding:10px 8px;font-size:12px;color:#0284c7;font-weight:600;white-space:nowrap;">
            {s['best_card']}
          </td>
          <td style="padding:10px 8px;white-space:nowrap;">
            <span style="color:#15803d;font-weight:700;font-size:15px;">{s['formatted_net_price']}</span>
          </td>
          <td style="padding:10px 8px;text-align:right;white-space:nowrap;">
            <a href="{s['maps_link']}" style="background-color:#0284c7;color:#ffffff;padding:5px 9px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;white-space:nowrap;font-size:11px;margin-right:4px;">📍 Maps</a>
            <a href="{s['waze_link']}" style="background-color:#059669;color:#ffffff;padding:5px 9px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;white-space:nowrap;font-size:11px;">🚗 Waze</a>
          </td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
  </head>
  <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#334155;margin:0;padding:12px;background-color:#f8fafc;">
    <div style="max-width:700px;margin:0 auto;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">

      <!-- Header -->
      <div style="background:linear-gradient(135deg, #0284c7, #1e3a8a);padding:20px;color:#ffffff;">
        <span style="background:rgba(255,255,255,0.2);color:#ffffff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">
          Norwalk ↔ Jersey City Fuel Tracker
        </span>
        <h2 style="margin:8px 0 4px 0;font-size:20px;font-weight:800;">
          ⛽ Fuel Digest &amp; Weekend Arbitrage
        </h2>
        <p style="margin:0;font-size:12px;opacity:0.9;">
          Norwalk (06854) → Stamford (06901) → New Rochelle (10801) → Teterboro (07608) → Jersey City (07306) → Bayonne (07002)
        </p>
      </div>

      <!-- Strategy Callout Banner -->
      <div style="padding:14px 18px;background-color:{banner_bg};border-bottom:1px solid {banner_border};color:{banner_text_color};font-size:12px;line-height:1.6;">
        <div style="font-weight:800;font-size:13px;margin-bottom:6px;">
          {banner_title}
        </div>
        {banner_body}
        <div style="margin-top:10px;">
          <a href="{route_gmaps}" style="background:#0284c7;color:#ffffff;padding:6px 12px;text-decoration:none;border-radius:5px;font-size:11px;font-weight:700;display:inline-block;margin-right:6px;">
            🗺️ Norwalk ↔ Jersey City Route
          </a>
          <a href="{route_waze}" style="background:#059669;color:#ffffff;padding:6px 12px;text-decoration:none;border-radius:5px;font-size:11px;font-weight:700;display:inline-block;">
            🧭 Waze to 850 Westside Ave
          </a>
        </div>
      </div>

      <!-- Card Savings Banner -->
      <div style="padding:10px 18px;background-color:#f1f5f9;border-bottom:1px solid #e2e8f0;font-size:11px;color:#475569;">
        💳 <strong>Card Optimization Applied</strong>: Net prices include <strong>4% Citi Costco Anywhere Visa</strong> (at Costco &amp; standalone pumps) or <strong>3% Amex Blue Cash Everyday</strong>. Supermarket gas earns 1%.
      </div>

      <!-- Table Container -->
      <div style="width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;">
        <table style="width:100%;border-collapse:collapse;text-align:left;min-width:540px;">
          <thead>
            <tr style="background-color:#f8fafc;border-bottom:2px solid #e2e8f0;color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">
              <th style="padding:10px 8px;">Station &amp; Area</th>
              <th style="padding:10px 8px;">Listed</th>
              <th style="padding:10px 8px;">Best Card</th>
              <th style="padding:10px 8px;">Net Price</th>
              <th style="padding:10px 8px;text-align:right;">Navigate</th>
            </tr>
          </thead>
          <tbody>
            {html_rows}
          </tbody>
        </table>
      </div>

      <!-- Footer -->
      <div style="padding:12px 18px;background-color:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;text-align:center;">
        Costco &amp; Fuel Notifier • Commute Edition for {RECEIVER_EMAIL}
      </div>
    </div>
  </body>
</html>"""

    return subject, text_summary, html_content


def send_email_smtp(subject, text_content, html_content):
    """
    Sends email via standard SMTP over TLS.
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("SMTP Error: SENDER_EMAIL or SENDER_PASSWORD not configured.")
        return False

    recipient = RECEIVER_EMAIL or SENDER_EMAIL
    if not recipient:
        print("SMTP Error: No recipient email specified.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient
    msg.attach(MIMEText(text_content, "plain"))
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


async def main():
    print("Fetching gas prices across Norwalk -> Jersey City corridor...")
    stations = await fetch_gas_prices()
    if not stations:
        print("No stations found.")
        return 1

    price_changed = log_to_sheets(stations)

    mode = get_commute_mode()
    print(f"Detected commute mode: {mode}")

    subject, text_summary, html_content = build_email_content(stations, mode)

    # Force email on Friday departure or Sunday return alerts
    is_weekend_alert = mode in ["FRIDAY_DEPARTURE", "SUNDAY_RETURN"]
    always_send = os.environ.get("ALWAYS_SEND_EMAIL", "true").lower() == "true"
    force_email = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

    if price_changed or force_email or always_send or is_weekend_alert:
        print("Sending fuel price digest email...")
        if not send_email_smtp(subject, text_summary, html_content):
            print("Email dispatch FAILED.")
            return 1
    else:
        print("Fuel price unchanged since last check. Email notification skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
