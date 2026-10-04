import os
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

import india_fuel
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
RECEIVER_EMAIL = (os.environ.get("MUMBAI_RECEIVER_EMAIL") or os.environ.get("RECEIVER_EMAIL") or "").strip() or SENDER_EMAIL

raw_smtp_server = (os.environ.get("SMTP_SERVER") or "").strip()
SMTP_SERVER = raw_smtp_server if raw_smtp_server else "smtp.gmail.com"

raw_smtp_port = (os.environ.get("SMTP_PORT") or "").strip()
SMTP_PORT = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587


# Dad's cards. None of them earn rewards/cashback on fuel, so the only lever is the
# fuel surcharge waiver (~1% + 18% GST on the surcharge) — which applies only when a
# single swipe falls inside the card's range.
DAD_CARDS = [
    {
        "card_name": "HDFC Regalia",
        "short": "Regalia",
        "waiver_min": 400,
        "waiver_max": 5000,
        "cap_note": "max ₹500 waiver per statement cycle",
    },
    {
        "card_name": "Amazon Pay ICICI Card",
        "short": "ICICI Amazon",
        "waiver_min": 400,
        "waiver_max": 4000,
        "cap_note": "",
    },
    {
        # The HSBC RuPay *Cashback* card has no fuel waiver. If Dad's card is the RuPay
        # *Platinum*, set waiver_min=400, waiver_max=4000 (capped at ₹250/month).
        "card_name": "HSBC RuPay",
        "short": "HSBC RuPay",
        "waiver_min": None,
        "waiver_max": None,
        "cap_note": "no fuel waiver on RuPay Cashback variant",
    },
    {
        "card_name": "Mastercard Debit Card",
        "short": "Debit",
        "waiver_min": None,
        "waiver_max": None,
        "cap_note": "no fuel waiver on debit cards",
    },
]


def recommend_card(amount):
    """
    Picks the card whose surcharge-waiver range covers a single swipe of `amount`.
    Prefers ICICI Amazon for fills up to ₹4,000 so Regalia's ₹500/cycle cap is kept
    for full tanks; Regalia covers ₹4,000–₹5,000 in one swipe.
    """
    preference = ["ICICI Amazon", "Regalia"]
    eligible = [
        c for c in DAD_CARDS
        if c["waiver_min"] is not None and c["waiver_min"] <= amount <= c["waiver_max"]
    ]
    eligible.sort(key=lambda c: preference.index(c["short"]) if c["short"] in preference else 99)
    if eligible:
        return {"card": eligible[0], "waived": True, "surcharge_saved": round(amount * 0.01, 2)}
    # Over ₹5,000 (or under ₹400): no single swipe is waived — split across Regalia + ICICI.
    return {"card": None, "waived": False, "surcharge_saved": 0.0}


def get_card_benefit(price):
    """Card recommendation for a full 45L tank at this station's price."""
    full_tank = round(price * TANK_CAPACITY_L, 2)
    rec = recommend_card(full_tank)
    if rec["waived"]:
        card = rec["card"]
        return {
            "card_name": card["card_name"],
            "card_short": card["short"],
            "benefit_summary": f"1% waived (~₹{rec['surcharge_saved']:.0f})",
            "full_tank": full_tank,
        }
    return {
        "card_name": "Split: Regalia + ICICI Amazon",
        "card_short": "Split swipe",
        "benefit_summary": "Keep each swipe ≤ ₹4,000/₹5,000",
        "full_tank": full_tank,
    }


FETCH_STATUS: dict = {"live": False, "prices": {}}


def fetch_mumbai_fuel_prices():
    """
    Fetches real-time daily Petrol and Diesel prices along the Versova to Ghansoli commute route.
    Returns categorized, sorted lists for Petrol and Diesel stations.
    """
    # Live prices (india_fuel); Navi Mumbai stations use Thane, the same district.
    # Hard-coded figures are a last resort and are flagged in the email as unverified.
    mumbai = india_fuel.fetch_city("Mumbai")
    navi = india_fuel.fetch_city("Thane") or mumbai
    india_fuel.record("Mumbai", mumbai)
    india_fuel.record("Thane", navi)
    FETCH_STATUS["live"] = bool(mumbai)
    FETCH_STATUS["prices"] = mumbai
    mumbai_petrol = mumbai["petrol"].price if "petrol" in mumbai else 111.21
    mumbai_diesel = mumbai["diesel"].price if "diesel" in mumbai else 97.83
    navi_petrol = navi["petrol"].price if "petrol" in navi else mumbai_petrol
    navi_diesel = navi["diesel"].price if "diesel" in navi else mumbai_diesel

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
        benefit = get_card_benefit(s["listed_price"])
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
                💳 {s['card_short']}<br><span style="font-size: 10px; color: #166534; background: #dcfce7; padding: 1px 4px; border-radius: 3px;">{s['benefit_summary']}</span>
            </td>
            <td style="padding: 10px 8px; text-align: right; white-space: nowrap;">
                <a href="{s['maps_link']}" style="background-color: #0284c7; color: #ffffff; padding: 6px 12px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; white-space: nowrap; font-size: 12px;">📍 Directions</a>
            </td>
        </tr>
        """
    return rows


def _outlook_block() -> str:
    """Fill-up advice for petrol (the Venue's fuel); a warning instead if prices are not live."""
    if not FETCH_STATUS["live"]:
        return ('<div style="padding: 12px 18px; background-color: #fef2f2; border-bottom: 1px solid #fecaca; '
                'color: #991b1b; font-size: 12px;">⚠️ Live prices could not be fetched today; the figures below '
                'are the last known Mumbai rates and may be out of date.</div>')
    petrol = FETCH_STATUS["prices"].get("petrol")
    if not petrol:
        return ""
    try:
        import fx_data, us_fuel_forecast
        brent = us_fuel_forecast.fetch_eia_weekly("RBRTE")
        series = fx_data.fetch_pair_history(years=1)
        return india_fuel.outlook_html(india_fuel.outlook(petrol, brent, dict(zip(series.dates, series.closes))))
    except Exception as e:  # crude/FX context is optional
        print(f"Outlook context unavailable ({e}); showing price stability only.")
        return india_fuel.outlook_html(india_fuel.outlook(petrol))


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
        text_summary += f"• {s['name']} ({s['area']}): {s['fuel_type']} - {s['formatted_price']} [{nitro}] | Pay with: {s['card_short']}\n  📍 Directions: {s['maps_link']}\n\n"
        
    text_summary += "\n=== DIESEL STATIONS (Lowest to Highest) ===\n"
    for s in diesel_stations:
        nitro = "Nitrogen Available" if s['has_nitrogen'] else "Air Only"
        text_summary += f"• {s['name']} ({s['area']}): {s['fuel_type']} - {s['formatted_price']} [{nitro}] | Pay with: {s['card_short']}\n  📍 Directions: {s['maps_link']}\n\n"

    text_summary += "\nCARD TIP: Full tank -> HDFC Regalia (1% waiver up to ₹5,000). Top-ups <= ₹4,000 -> Amazon Pay ICICI. Avoid HSBC RuPay and debit card for fuel.\n"
    msg.attach(MIMEText(text_summary, "plain"))

    petrol_rows_html = build_table_rows(petrol_stations, is_diesel=False)
    diesel_rows_html = build_table_rows(diesel_stations, is_diesel=True)

    outlook_block = _outlook_block()

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
            • 💳 <strong>Which card to swipe</strong>: <strong>Full tank (~₹{full_tank_petrol:,.0f}) → HDFC Regalia</strong> — its 1% surcharge waiver covers up to ₹5,000 in one swipe (max ₹500 waiver per statement cycle).<br>
            • 💳 <strong>Top-ups up to ₹4,000 → Amazon Pay ICICI</strong> — same 1% waiver, and it saves Regalia's monthly cap for full tanks.<br>
            • 🚫 <strong>Avoid for fuel</strong>: HSBC RuPay (no fuel waiver on Cashback card) and Mastercard debit card. Note: No card earns reward points on fuel.
          </div>

          <!-- Should you fill up today? -->
          {outlook_block}

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
