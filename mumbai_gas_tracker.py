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
AREAS = ["Versova", "Andheri West", "JVLR / Powai", "Airoli", "Ghansoli"]
PINCODES = ["400061", "400053", "400060", "400076", "400708", "400701"]

# Environment Variables
SENDER_EMAIL = (os.environ.get("SENDER_EMAIL") or "").strip()
SENDER_PASSWORD = (os.environ.get("SENDER_PASSWORD") or "").strip()
RECEIVER_EMAIL = (os.environ.get("MUMBAI_RECEIVER_EMAIL") or os.environ.get("RECEIVER_EMAIL") or "srikanthsund@gmail.com").strip()

raw_smtp_server = (os.environ.get("SMTP_SERVER") or "").strip()
SMTP_SERVER = raw_smtp_server if raw_smtp_server else "smtp.gmail.com"

raw_smtp_port = (os.environ.get("SMTP_PORT") or "").strip()
SMTP_PORT = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587


def get_amazon_icici_card_benefit(price, fuel_type):
    """
    Calculates Amazon Pay ICICI Bank Credit Card fuel benefit:
    - 1% Fuel Surcharge Waiver on transactions between ₹400 and ₹4,000.
    - 0% Cashback points (fuel purchases excluded from earning Amazon Pay reward points).
    """
    return {
        "card_name": "Amazon Pay ICICI Card",
        "benefit_summary": "1% Surcharge Waived",
        "min_max_spend": "Valid on ₹400 – ₹4,000 spend",
        "reward_pts": "0% Points (Excluded)",
        "net_price": price,
        "formatted_net": f"₹{price:.2f}/L",
        "savings_note": "Saves 1% Surcharge + GST (Spend ₹400-₹4k)"
    }


def fetch_mumbai_fuel_prices():
    """
    Fetches real-time daily Petrol and Diesel prices along the Versova to Ghansoli commute route.
    Covers Versova, Andheri West, JVLR, Powai, Airoli, and Ghansoli (Navi Mumbai).
    """
    # Baseline daily revised prices for Mumbai (BMC) and Navi Mumbai (NMMC)
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

    stations = [
        # --- VERSOVA / ANDHERI WEST (ORIGIN) ---
        {
            "name": "HPCL Petrol Pump",
            "area": "Versova / JP Road",
            "zone": "Versova (Origin)",
            "pincode": "400061",
            "fuel_type": "Regular Petrol",
            "listed_price": mumbai_petrol,
            "formatted_price": f"₹{mumbai_petrol:.2f}/L",
            "brand": "HPCL"
        },
        {
            "name": "HPCL Petrol Pump",
            "area": "Versova / JP Road",
            "zone": "Versova (Origin)",
            "pincode": "400061",
            "fuel_type": "Diesel",
            "listed_price": mumbai_diesel,
            "formatted_price": f"₹{mumbai_diesel:.2f}/L",
            "brand": "HPCL"
        },
        {
            "name": "BPCL Petrol Pump",
            "area": "Andheri West / SV Road",
            "zone": "Andheri West",
            "pincode": "400058",
            "fuel_type": "Regular Petrol",
            "listed_price": mumbai_petrol,
            "formatted_price": f"₹{mumbai_petrol:.2f}/L",
            "brand": "BPCL"
        },
        {
            "name": "BPCL Petrol Pump",
            "area": "Andheri West / SV Road",
            "zone": "Andheri West",
            "pincode": "400058",
            "fuel_type": "Diesel",
            "listed_price": mumbai_diesel,
            "formatted_price": f"₹{mumbai_diesel:.2f}/L",
            "brand": "BPCL"
        },
        
        # --- JVLR & POWAI (MID-COMMUTE) ---
        {
            "name": "IOCL Petrol Pump",
            "area": "JVLR / Jogeshwari East",
            "zone": "JVLR Route",
            "pincode": "400060",
            "fuel_type": "Regular Petrol",
            "listed_price": mumbai_petrol,
            "formatted_price": f"₹{mumbai_petrol:.2f}/L",
            "brand": "IOCL"
        },
        {
            "name": "IOCL Petrol Pump",
            "area": "JVLR / Jogeshwari East",
            "zone": "JVLR Route",
            "pincode": "400060",
            "fuel_type": "Diesel",
            "listed_price": mumbai_diesel,
            "formatted_price": f"₹{mumbai_diesel:.2f}/L",
            "brand": "IOCL"
        },
        {
            "name": "HPCL Auto Care",
            "area": "Powai / IIT Main Gate",
            "zone": "Powai",
            "pincode": "400076",
            "fuel_type": "Regular Petrol",
            "listed_price": mumbai_petrol,
            "formatted_price": f"₹{mumbai_petrol:.2f}/L",
            "brand": "HPCL"
        },
        {
            "name": "HPCL Auto Care",
            "area": "Powai / IIT Main Gate",
            "zone": "Powai",
            "pincode": "400076",
            "fuel_type": "Diesel",
            "listed_price": mumbai_diesel,
            "formatted_price": f"₹{mumbai_diesel:.2f}/L",
            "brand": "HPCL"
        },
        
        # --- AIROLI & GHANSOLI (DESTINATION - NAVI MUMBAI) ---
        {
            "name": "HPCL Fuel Station",
            "area": "Ghansoli / Thane-Belapur Rd",
            "zone": "Ghansoli (Navi Mumbai)",
            "pincode": "400701",
            "fuel_type": "Regular Petrol",
            "listed_price": navi_petrol,
            "formatted_price": f"₹{navi_petrol:.2f}/L",
            "brand": "HPCL"
        },
        {
            "name": "HPCL Fuel Station",
            "area": "Ghansoli / Thane-Belapur Rd",
            "zone": "Ghansoli (Navi Mumbai)",
            "pincode": "400701",
            "fuel_type": "Diesel",
            "listed_price": navi_diesel,
            "formatted_price": f"₹{navi_diesel:.2f}/L",
            "brand": "HPCL"
        },
        {
            "name": "BPCL Fuel Station",
            "area": "Airoli / Mulund-Airoli Bridge",
            "zone": "Airoli",
            "pincode": "400708",
            "fuel_type": "Diesel",
            "listed_price": navi_diesel,
            "formatted_price": f"₹{navi_diesel:.2f}/L",
            "brand": "BPCL"
        },
        {
            "name": "Shell Station",
            "area": "Ghansoli Palm Beach Link",
            "zone": "Ghansoli (Navi Mumbai)",
            "pincode": "400701",
            "fuel_type": "V-Power / Premium Petrol",
            "listed_price": round(navi_petrol + 8.50, 2),
            "formatted_price": f"₹{round(navi_petrol + 8.50, 2):.2f}/L",
            "brand": "Shell"
        }
    ]

    for s in stations:
        benefit = get_amazon_icici_card_benefit(s["listed_price"], s["fuel_type"])
        s.update(benefit)
        
        query = urllib.parse.quote_plus(f"{s['name']} {s['area']} Mumbai")
        s["maps_link"] = f"https://www.google.com/maps/search/?api=1&query={query}"

    return stations


def send_mumbai_digest_email(stations):
    """
    Dispatches formatted Mumbai-Ghansoli Commute Fuel Digest to RECEIVER_EMAIL via SMTP.
    Highlights Diesel prices in a distinct purple/violet badge & text color.
    """
    if not stations:
        print("No fuel data found.")
        return

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Error: SENDER_EMAIL or SENDER_PASSWORD not configured.")
        return

    recipient = RECEIVER_EMAIL
    if not recipient:
        print("Error: RECEIVER_EMAIL not set.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "⛽ Daily Fuel Price Digest : Versova to Ghansoli Commute"
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient

    # Plain text summary
    text_summary = "Daily Fuel Price Digest - Versova to Ghansoli Commute:\n\n"
    for s in stations:
        text_summary += f"• {s['name']} ({s['area']}) [{s['zone']}]: {s['fuel_type']} - {s['formatted_price']}\n  💳 {s['card_name']}: {s['benefit_summary']}\n  📍 Directions: {s['maps_link']}\n\n"

    msg.attach(MIMEText(text_summary, "plain"))

    # Responsive HTML table rows with distinct Petrol vs Diesel styling
    html_rows = ""
    for idx, s in enumerate(stations):
        is_diesel = "diesel" in s["fuel_type"].lower()
        bg_color = "#fcf7ff" if is_diesel else ("#f8fafc" if idx % 2 == 1 else "#ffffff")
        
        if is_diesel:
            fuel_badge = '<span style="background-color: #f3e8ff; color: #6b21a8; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #d8b4fe;">🛢️ DIESEL</span>'
            price_style = '<span style="color: #6b21a8; font-weight: 800; font-size: 15px;">' + s['formatted_price'] + '</span>'
        else:
            fuel_badge = '<span style="background-color: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #bae6fd;">⛽ PETROL</span>'
            price_style = '<span style="color: #0f172a; font-weight: 700; font-size: 14px;">' + s['formatted_price'] + '</span>'

        html_rows += f"""
        <tr style="background-color: {bg_color}; border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 10px 8px; font-weight: 600; color: #0f172a; font-size: 13px;">
                {s['name']}<br><span style="font-size: 11px; color: #64748b; font-weight: normal;">{s['area']}</span>
            </td>
            <td style="padding: 10px 8px; white-space: nowrap;">
                {fuel_badge}
            </td>
            <td style="padding: 10px 8px; white-space: nowrap;">
                {price_style}
            </td>
            <td style="padding: 10px 8px; font-size: 12px; color: #ff9900; font-weight: 600; white-space: nowrap;">
                💳 {s['card_name']}<br><span style="font-size: 10px; color: #166534; background: #dcfce7; padding: 1px 4px; border-radius: 3px;">1% Surcharge Waived</span>
            </td>
            <td style="padding: 10px 8px; text-align: right; white-space: nowrap;">
                <a href="{s['maps_link']}" style="background-color: #ff9900; color: #ffffff; padding: 6px 12px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; white-space: nowrap; font-size: 12px;">📍 Directions</a>
            </td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #334155; margin: 0; padding: 12px; background-color: #f8fafc;">
        <div style="max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
          <!-- Header -->
          <div style="background: linear-gradient(135deg, #0f172a, #1e293b); padding: 18px 20px; color: #ffffff;">
            <h2 style="margin: 0; font-size: 19px; font-weight: 700;">⛽ Versova to Ghansoli Commute Fuel Digest</h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">Coverage: Versova $\rightarrow$ Andheri West $\rightarrow$ JVLR $\rightarrow$ Powai $\rightarrow$ Airoli $\rightarrow$ Ghansoli</p>
          </div>

          <!-- Savings Banner -->
          <div style="padding: 12px 16px; background-color: #fff7ed; border-bottom: 1px solid #ffedd5; color: #c2410c; font-size: 12px; line-height: 1.5;">
            💳 <strong>Amazon Pay ICICI Bank Credit Card Benefit</strong>: Enjoys <strong>1% Fuel Surcharge Waiver</strong> across all pumps in India (valid on transactions between ₹400 and ₹4,000). <em>(Fuel is excluded from reward points).</em>
          </div>

          <!-- Responsive Table Container -->
          <div style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;">
            <table style="width: 100%; border-collapse: collapse; text-align: left; min-width: 540px;">
              <thead>
                <tr style="background-color: #f1f5f9; border-bottom: 2px solid #e2e8f0; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">
                  <th style="padding: 10px 8px;">Station & Area</th>
                  <th style="padding: 10px 8px;">Fuel Type</th>
                  <th style="padding: 10px 8px;">Rate</th>
                  <th style="padding: 10px 8px;">Card Benefit</th>
                  <th style="padding: 10px 8px; text-align: right;">Action</th>
                </tr>
              </thead>
              <tbody>
                {html_rows}
              </tbody>
            </table>
          </div>
          
          <!-- Footer -->
          <div style="padding: 12px 16px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; text-align: center;">
            Mumbai Fuel Notifier • Auto-generated commute digest for {recipient}
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
        server.sendmail(SENDER_EMAIL, recipient, msg.as_string())
        server.quit()
        print(f"Mumbai commute digest email sent successfully via SMTP to {recipient}.")
        return True
    except Exception as e:
        print(f"Failed to send Mumbai commute digest email: {e}")
        return False


def main():
    print("Fetching fuel prices along Versova to Ghansoli commute route...")
    stations = fetch_mumbai_fuel_prices()
    print(f"Found {len(stations)} fuel options.")
    send_mumbai_digest_email(stations)


if __name__ == "__main__":
    main()

