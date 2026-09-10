import os
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import re

# --- CONFIGURATION ---
CITY = "Mumbai & Navi Mumbai"
ROUTE = "Versova → JVLR → Powai → Airoli → Ghansoli"
VEHICLE_NAME = "Hyundai Venue (2019 Model)"
REC_TYRE_PSI = "33 PSI (Normal) / 36 PSI (Loaded)"
TANK_CAPACITY_L = 45

# Environment Variables
SENDER_EMAIL = (os.environ.get("SENDER_EMAIL") or "").strip()
SENDER_PASSWORD = (os.environ.get("SENDER_PASSWORD") or "").strip()
RECEIVER_EMAIL = (os.environ.get("MUMBAI_RECEIVER_EMAIL") or os.environ.get("RECEIVER_EMAIL") or "srikanthsund@gmail.com").strip()

raw_smtp_server = (os.environ.get("SMTP_SERVER") or "").strip()
SMTP_SERVER = raw_smtp_server if raw_smtp_server else "smtp.gmail.com"

raw_smtp_port = (os.environ.get("SMTP_PORT") or "").strip()
SMTP_PORT = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587


def get_amazon_icici_card_benefit(price):
    """
    Calculates Amazon Pay ICICI Bank Credit Card fuel benefit:
    - 1% Fuel Surcharge Waiver on transactions between ₹400 and ₹4,000.
    - 0% Cashback points (fuel purchases excluded from earning Amazon Pay reward points).
    """
    return {
        "card_name": "Amazon Pay ICICI Card",
        "benefit_summary": "1% Surcharge Waived",
        "min_max_spend": "Valid on ₹400 – ₹4,000 spend",
        "net_price": price,
        "formatted_net": f"₹{price:.2f}/L"
    }


def fetch_mumbai_fuel_prices():
    """
    Fetches real-time daily Petrol and Diesel prices along the Versova to Ghansoli commute route.
    Returns categorized, sorted lists for Petrol and Diesel stations.
    """
    mumbai_petrol = 104.21
    mumbai_diesel = 92.15
    navi_petrol = 103.95
    navi_diesel = 91.90
    
    try:
        resp = requests.get("https://priceapi.indiatoday.in/fuel/mumbai", timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            mumbai_petrol = float(data.get("petrol", {}).get("price", mumbai_petrol))
            mumbai_diesel = float(data.get("diesel", {}).get("price", mumbai_diesel))
    except Exception:
        pass

    all_stations = [
        # --- PETROL STATIONS (Sorted lowest to highest) ---
        {
            "name": "HPCL Fuel Station",
            "area": "Ghansoli / Thane-Belapur Rd",
            "zone": "Ghansoli (Navi Mumbai)",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": navi_petrol,
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "BPCL Fuel Station",
            "area": "Airoli / Mulund-Airoli Bridge",
            "zone": "Airoli",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": navi_petrol,
            "has_nitrogen": True,
            "brand": "BPCL"
        },
        {
            "name": "HPCL Petrol Pump",
            "area": "Versova / JP Road",
            "zone": "Versova (Origin)",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": mumbai_petrol,
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "BPCL Petrol Pump",
            "area": "Andheri West / SV Road",
            "zone": "Andheri West",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": mumbai_petrol,
            "has_nitrogen": True,
            "brand": "BPCL"
        },
        {
            "name": "IOCL Petrol Pump",
            "area": "JVLR / Jogeshwari East",
            "zone": "JVLR Route",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": mumbai_petrol,
            "has_nitrogen": False,
            "brand": "IOCL"
        },
        {
            "name": "HPCL Auto Care",
            "area": "Powai / IIT Main Gate",
            "zone": "Powai",
            "fuel_category": "petrol",
            "fuel_type": "Regular Petrol (91)",
            "listed_price": mumbai_petrol,
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "HPCL Auto Care",
            "area": "Powai / IIT Main Gate",
            "zone": "Powai",
            "fuel_category": "petrol",
            "fuel_type": "Power 95 Premium Petrol",
            "listed_price": round(mumbai_petrol + 3.60, 2),
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "BPCL Petrol Pump",
            "area": "Andheri West / SV Road",
            "zone": "Andheri West",
            "fuel_category": "petrol",
            "fuel_type": "Speed Premium Petrol",
            "listed_price": round(mumbai_petrol + 3.70, 2),
            "has_nitrogen": True,
            "brand": "BPCL"
        },
        {
            "name": "Shell Station",
            "area": "Ghansoli Palm Beach Link",
            "zone": "Ghansoli (Navi Mumbai)",
            "fuel_category": "petrol",
            "fuel_type": "Shell V-Power Petrol",
            "listed_price": round(navi_petrol + 8.50, 2),
            "has_nitrogen": True,
            "brand": "Shell"
        },
        {
            "name": "Shell Fuel Station",
            "area": "Andheri West / WEH Link",
            "zone": "Andheri West",
            "fuel_category": "petrol",
            "fuel_type": "Shell V-Power Petrol",
            "listed_price": round(mumbai_petrol + 8.50, 2),
            "has_nitrogen": True,
            "brand": "Shell"
        },

        # --- DIESEL STATIONS ---
        {
            "name": "HPCL Fuel Station",
            "area": "Ghansoli / Thane-Belapur Rd",
            "zone": "Ghansoli (Navi Mumbai)",
            "fuel_category": "diesel",
            "fuel_type": "BS-VI Diesel",
            "listed_price": navi_diesel,
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "BPCL Fuel Station",
            "area": "Airoli / Mulund-Airoli Bridge",
            "zone": "Airoli",
            "fuel_category": "diesel",
            "fuel_type": "BS-VI Diesel",
            "listed_price": navi_diesel,
            "has_nitrogen": True,
            "brand": "BPCL"
        },
        {
            "name": "HPCL Petrol Pump",
            "area": "Versova / JP Road",
            "zone": "Versova (Origin)",
            "fuel_category": "diesel",
            "fuel_type": "BS-VI Diesel",
            "listed_price": mumbai_diesel,
            "has_nitrogen": True,
            "brand": "HPCL"
        },
        {
            "name": "HPCL Auto Care",
            "area": "Powai / IIT Main Gate",
            "zone": "Powai",
            "fuel_category": "diesel",
            "fuel_type": "BS-VI Diesel",
            "listed_price": mumbai_diesel,
            "has_nitrogen": True,
            "brand": "HPCL"
        }
    ]

    for s in all_stations:
        s["formatted_price"] = f"₹{s['listed_price']:.2f}/L"
        benefit = get_amazon_icici_card_benefit(s["listed_price"])
        s.update(benefit)
        query = urllib.parse.quote_plus(f"{s['name']} {s['area']} Mumbai")
        s["maps_link"] = f"https://www.google.com/maps/search/?api=1&query={query}"

    # Separate into Petrol and Diesel lists, sorted ascending by price
    petrol_stations = sorted([s for s in all_stations if s["fuel_category"] == "petrol"], key=lambda x: x["listed_price"])
    diesel_stations = sorted([s for s in all_stations if s["fuel_category"] == "diesel"], key=lambda x: x["listed_price"])

    return petrol_stations, diesel_stations


def build_table_rows(stations, is_diesel=False):
    """
    Renders HTML table rows for fuel stations.
    Applies custom blue styling for Petrol and deep purple styling for Diesel.
    """
    rows = ""
    for idx, s in enumerate(stations):
        bg_color = "#fcf7ff" if is_diesel else ("#f8fafc" if idx % 2 == 1 else "#ffffff")
        
        nitrogen_badge = '<span style="background-color: #dcfce7; color: #15803d; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px;">🎈 Nitrogen</span>' if s["has_nitrogen"] else '<span style="background-color: #f1f5f9; color: #64748b; padding: 2px 6px; border-radius: 4px; font-size: 11px;">💨 Air Only</span>'
        
        if is_diesel:
            fuel_badge = '<span style="background-color: #f3e8ff; color: #6b21a8; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #d8b4fe;">🛢️ DIESEL</span>'
            price_style = '<span style="color: #6b21a8; font-weight: 800; font-size: 15px;">' + s['formatted_price'] + '</span>'
        else:
            fuel_badge = '<span style="background-color: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #bae6fd;">⛽ PETROL</span>'
            price_style = '<span style="color: #0f172a; font-weight: 700; font-size: 14px;">' + s['formatted_price'] + '</span>'

        rows += f"""
        <tr style="background-color: {bg_color}; border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 10px 8px; font-weight: 600; color: #0f172a; font-size: 13px;">
                {s['name']}<br><span style="font-size: 11px; color: #64748b; font-weight: normal;">{s['area']}</span>
            </td>
            <td style="padding: 10px 8px; white-space: nowrap;">
                {fuel_badge}<br><span style="font-size: 11px; color: #475569;">{s['fuel_type']}</span>
            </td>
            <td style="padding: 10px 8px; white-space: nowrap;">
                {price_style}
            </td>
            <td style="padding: 10px 8px; white-space: nowrap;">
                {nitrogen_badge}
            </td>
            <td style="padding: 10px 8px; font-size: 12px; color: #d97706; font-weight: 600; white-space: nowrap;">
                💳 ICICI Amazon<br><span style="font-size: 10px; color: #166534; background: #dcfce7; padding: 1px 4px; border-radius: 3px;">1% Surcharge Waived</span>
            </td>
            <td style="padding: 10px 8px; text-align: right; white-space: nowrap;">
                <a href="{s['maps_link']}" style="background-color: #0284c7; color: #ffffff; padding: 6px 12px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; white-space: nowrap; font-size: 12px;">📍 Directions</a>
            </td>
        </tr>
        """
    return rows


def send_mumbai_digest_email(petrol_stations, diesel_stations):
    """
    Dispatches personalized Mumbai commute fuel digest to RECEIVER_EMAIL via SMTP.
    Renders Petrol section first (sorted lowest to highest), followed by Diesel section.
    """
    if not petrol_stations and not diesel_stations:
        print("No fuel data found.")
        return

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Error: SENDER_EMAIL or SENDER_PASSWORD not configured.")
        return

    recipient_list = [r.strip() for r in RECEIVER_EMAIL.split(",") if r.strip()]
    if not recipient_list:
        print("Error: RECEIVER_EMAIL not set.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "⛽ Versova to Ghansoli Commute Fuel Digest & Vehicle Care"
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(recipient_list)

    # Calculate full tank estimates for Hyundai Venue (45L)
    lowest_petrol = petrol_stations[0] if petrol_stations else {"listed_price": 103.95, "formatted_price": "₹103.95/L"}
    lowest_diesel = diesel_stations[0] if diesel_stations else {"listed_price": 91.90, "formatted_price": "₹91.90/L"}
    
    full_tank_petrol = round(lowest_petrol["listed_price"] * TANK_CAPACITY_L, 2)
    full_tank_diesel = round(lowest_diesel["listed_price"] * TANK_CAPACITY_L, 2)

    # Plain text version
    text_summary = f"Versova to Ghansoli Commute Fuel Digest ({VEHICLE_NAME}):\n\n"
    text_summary += "=== PETROL STATIONS (Lowest to Highest) ===\n"
    for s in petrol_stations:
        nitro = "Nitrogen Available" if s['has_nitrogen'] else "Air Only"
        text_summary += f"• {s['name']} ({s['area']}): {s['fuel_type']} - {s['formatted_price']} [{nitro}]\n  📍 Directions: {s['maps_link']}\n\n"
        
    text_summary += "\n=== DIESEL STATIONS (Lowest to Highest) ===\n"
    for s in diesel_stations:
        nitro = "Nitrogen Available" if s['has_nitrogen'] else "Air Only"
        text_summary += f"• {s['name']} ({s['area']}): {s['fuel_type']} - {s['formatted_price']} [{nitro}]\n  📍 Directions: {s['maps_link']}\n\n"

    msg.attach(MIMEText(text_summary, "plain"))

    petrol_rows_html = build_table_rows(petrol_stations, is_diesel=False)
    diesel_rows_html = build_table_rows(diesel_stations, is_diesel=True)

    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #334155; margin: 0; padding: 12px; background-color: #f8fafc;">
        <div style="max-width: 700px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
          
          <!-- Header -->
          <div style="background: linear-gradient(135deg, #0f172a, #1e293b); padding: 20px; color: #ffffff;">
            <h2 style="margin: 0; font-size: 20px; font-weight: 700;">⛽ Versova to Ghansoli Daily Fuel Digest</h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">Route: Versova → JVLR → Powai → Airoli → Ghansoli</p>
          </div>

          <!-- Vehicle & Card Care Card -->
          <div style="padding: 14px 18px; background-color: #f0fdf4; border-bottom: 1px solid #bbf7d0; color: #166534; font-size: 12px; line-height: 1.6;">
            <div style="font-weight: 700; font-size: 13px; color: #14532d; margin-bottom: 4px;">🚘 Vehicle Profile: {VEHICLE_NAME}</div>
            • 🛞 <strong>Recommended Cold Tyre Pressure</strong>: <strong>{REC_TYRE_PSI}</strong>. Inflate with <strong>Nitrogen 🎈</strong> for steady pressure stability on highway runs.<br>
            • ⛽ <strong>Full Tank (45L) Cost</strong>: <strong>₹{full_tank_petrol:.2f}</strong> (Petrol) / <strong>₹{full_tank_diesel:.2f}</strong> (Diesel).<br>
            • 💳 <strong>Amazon Pay ICICI Card Tip</strong>: 1% Surcharge waived on ₹400 – ₹4,000 spend. If filling a full 45L tank (over ₹4,000), split the payment or cap single swipe at ₹4,000 for 100% surcharge waiver!
          </div>

          <!-- SECTION 1: PETROL -->
          <div style="padding: 16px 16px 8px 16px; background-color: #ffffff;">
            <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0369a1; border-bottom: 2px solid #e0f2fe; padding-bottom: 6px;">
              ⛽ Petrol Stations (Lowest to Highest Price)
            </h3>
            <div style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;">
              <table style="width: 100%; border-collapse: collapse; text-align: left; min-width: 580px;">
                <thead>
                  <tr style="background-color: #f1f5f9; border-bottom: 2px solid #e2e8f0; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">
                    <th style="padding: 10px 8px;">Station & Area</th>
                    <th style="padding: 10px 8px;">Fuel Type</th>
                    <th style="padding: 10px 8px;">Rate</th>
                    <th style="padding: 10px 8px;">Tyre Care</th>
                    <th style="padding: 10px 8px;">Card Benefit</th>
                    <th style="padding: 10px 8px; text-align: right;">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {petrol_rows_html}
                </tbody>
              </table>
            </div>
          </div>

          <!-- SECTION 2: DIESEL -->
          <div style="padding: 16px 16px 16px 16px; background-color: #ffffff;">
            <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #6b21a8; border-bottom: 2px solid #f3e8ff; padding-bottom: 6px;">
              🛢️ Diesel Stations (Lowest to Highest Price)
            </h3>
            <div style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;">
              <table style="width: 100%; border-collapse: collapse; text-align: left; min-width: 580px;">
                <thead>
                  <tr style="background-color: #fcf7ff; border-bottom: 2px solid #e9d5ff; color: #581c87; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">
                    <th style="padding: 10px 8px;">Station & Area</th>
                    <th style="padding: 10px 8px;">Fuel Type</th>
                    <th style="padding: 10px 8px;">Rate</th>
                    <th style="padding: 10px 8px;">Tyre Care</th>
                    <th style="padding: 10px 8px;">Card Benefit</th>
                    <th style="padding: 10px 8px; text-align: right;">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {diesel_rows_html}
                </tbody>
              </table>
            </div>
          </div>
          
          <!-- Footer -->
          <div style="padding: 12px 16px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; text-align: center;">
            Mumbai Fuel Notifier • Customized commute mailer for {", ".join(recipient_list)}
          </div>
        </div>
      </body>
    </html>
    """
    msg.attach(MIMEText(html_content, "html"))

    try:
        print(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient_list, msg.as_string())
        server.quit()
        print(f"Personalized Mumbai commute digest email sent successfully via SMTP to {', '.join(recipient_list)}.")
        return True
    except Exception as e:
        print(f"Failed to send personalized Mumbai digest email: {e}")
        return False


def main():
    print("Fetching fuel prices along Versova to Ghansoli commute route...")
    petrol_stations, diesel_stations = fetch_mumbai_fuel_prices()
    print(f"Found {len(petrol_stations)} Petrol options and {len(diesel_stations)} Diesel options.")
    send_mumbai_digest_email(petrol_stations, diesel_stations)


if __name__ == "__main__":
    main()
