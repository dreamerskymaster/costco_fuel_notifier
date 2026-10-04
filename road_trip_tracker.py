"""
road_trip_tracker.py
====================
One-time automated road trip fuel digest and route navigator for Dad.

Trip schedule (IST dates):
  Sep 30  – Mumbai -> Goa   (Choice of NH48 via Kolhapur OR NH66 Coastal)
  Oct  4  – Goa    -> Mumbai (Choice of NH48 via Kolhapur OR NH66 Coastal)
  Oct  9  – Mumbai -> Pune   (Mumbai-Pune Expressway / NH48)
  Oct 10  – Pune   -> Mumbai (Mumbai-Pune Expressway / NH48)
  Oct 15  – Mumbai -> Pune   (Mumbai-Pune Expressway / NH48)
  Oct 17  – Pune   -> Mumbai (Mumbai-Pune Expressway / NH48)

The script is invoked daily at ~5:30 AM IST (00:00 UTC) via GitHub Actions.
If today does not match any trip date, it exits silently.
On a matching date, it sends a personalized morning road trip guide:
  • Route Comparison & 1-tap Google Maps Driving Directions + Waze links
  • Departure-city fuel stations with Nitrogen availability & 1-tap Maps links
  • Dad's Credit Card Optimizer:
      - Full tank (~₹4,600+): HDFC Regalia (covers up to ₹5,000 in a single swipe)
      - Top-ups <= ₹4,000: Amazon Pay ICICI (uncapped, preserves Regalia monthly limit)
      - Avoid HSBC RuPay and Mastercard debit cards for fuel (0% waiver)
  • Hyundai Venue (2019) tyre pressure (33 PSI normal / 36 PSI highway)
"""

import os
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

import india_fuel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VEHICLE_NAME = "Hyundai Venue (2019 Model)"
TANK_CAPACITY_L = 45
REC_TYRE_PSI = "33 PSI (Cold City) / 36 PSI (Loaded Highway)"

SENDER_EMAIL = (os.environ.get("SENDER_EMAIL") or "").strip()
SENDER_PASSWORD = (os.environ.get("SENDER_PASSWORD") or "").strip()
RECEIVER_EMAIL = (
    os.environ.get("MUMBAI_RECEIVER_EMAIL")
    or os.environ.get("RECEIVER_EMAIL")
    or "srikanthsund@gmail.com"
).strip()

raw_smtp_server = (os.environ.get("SMTP_SERVER") or "").strip()
SMTP_SERVER = raw_smtp_server if raw_smtp_server else "smtp.gmail.com"

raw_smtp_port = (os.environ.get("SMTP_PORT") or "").strip()
SMTP_PORT = int(raw_smtp_port) if raw_smtp_port.isdigit() else 587

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Dad's Credit Card Engine
# ---------------------------------------------------------------------------

DAD_CARDS = [
    {
        "card_name": "HDFC Regalia",
        "short": "HDFC Regalia",
        "waiver_min": 400,
        "waiver_max": 5000,
        "cap_note": "max ₹500 waiver per statement cycle",
    },
    {
        "card_name": "Amazon Pay ICICI Card",
        "short": "ICICI Amazon",
        "waiver_min": 400,
        "waiver_max": 4000,
        "cap_note": "no monthly ceiling",
    },
    {
        "card_name": "HSBC RuPay",
        "short": "HSBC RuPay",
        "waiver_min": None,
        "waiver_max": None,
        "cap_note": "no fuel waiver on RuPay Cashback card",
    },
    {
        "card_name": "Mastercard Debit Card",
        "short": "Debit",
        "waiver_min": None,
        "waiver_max": None,
        "cap_note": "no fuel waiver on debit cards",
    },
]


def recommend_card(amount: float) -> dict:
    """
    Recommends the best card to swipe for a fuel purchase of `amount`.
    - Fills up to ₹4,000: Amazon Pay ICICI (keeps Regalia's ₹500/month cap intact).
    - Fills between ₹4,000 and ₹5,000 (e.g. full tank): HDFC Regalia (covers whole swipe).
    - Fills above ₹5,000: split across Regalia + ICICI Amazon.
    """
    if 4000 < amount <= 5000:
        return {
            "card_short": "HDFC Regalia",
            "card_name": "HDFC Regalia",
            "benefit_summary": f"1% waived in 1 swipe (~₹{amount * 0.01:.0f})",
            "note": "Covers full tank up to ₹5,000 in one transaction (₹500 cycle cap).",
        }
    if 400 <= amount <= 4000:
        return {
            "card_short": "ICICI Amazon",
            "card_name": "Amazon Pay ICICI Card",
            "benefit_summary": f"1% waived (~₹{amount * 0.01:.0f})",
            "note": "Uncapped monthly. Saves Regalia's limit for full tanks.",
        }
    if amount > 5000:
        return {
            "card_short": "Split: Regalia + ICICI",
            "card_name": "Split Swipe",
            "benefit_summary": "Split: ₹4,000 ICICI + balance Regalia",
            "note": "Split bill to keep each swipe within waiver limits.",
        }
    return {
        "card_short": "Cash / UPI",
        "card_name": "Cash / UPI",
        "benefit_summary": "Under ₹400 minimum waiver limit",
        "note": "Transactions under ₹400 do not qualify for surcharge waiver.",
    }


# ---------------------------------------------------------------------------
# Trip Schedules & Route Data
# ---------------------------------------------------------------------------

TRIP_SCHEDULE = [
    {
        "date": "2026-09-30",
        "from_city": "Mumbai",
        "to_city": "Goa",
        "trip_type": "goa",
        "routes": [
            {
                "name": "Option 1 (Fastest & Smoothest): NH48 via Kolhapur & Amboli Ghat",
                "tag": "RECOMMENDED DRIVING ROUTE",
                "tag_color": "#15803d",
                "tag_bg": "#dcfce7",
                "highway": "NH48 (Mumbai-Pune Expwy → Satara → Kolhapur → Nipani → Amboli → Goa)",
                "distance_km": 590,
                "drive_time": "10 – 11 Hours",
                "toll_estimate": "₹1,050 – ₹1,200",
                "road_condition": "4-6 lane dual-carriageway expressway for ~85% of distance. Smooth bypasses, reliable amenities, safe for night or early-morning driving.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Versova,+Mumbai&destination=Panaji,+Goa&waypoints=Kolhapur,+Maharashtra%7CAmboli,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Goa+India&navigate=yes",
            },
            {
                "name": "Option 2 (Scenic Coastal): NH66 Coastal Highway",
                "tag": "SCENIC ALTERNATIVE",
                "tag_color": "#0369a1",
                "tag_bg": "#e0f2fe",
                "highway": "NH66 (Panvel → Pen → Mangaon → Chiplun → Sawantwadi → Goa)",
                "distance_km": 550,
                "drive_time": "12 – 14 Hours",
                "toll_estimate": "₹600 – ₹750",
                "road_condition": "Picturesque Konkan views, but active 4-laning widening works, single-lane bypass diversions, and narrow Ghat stretches in Raigad/Ratnagiri.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Versova,+Mumbai&destination=Panaji,+Goa&waypoints=Chiplun,+Maharashtra%7CSawantwadi,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Goa+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "BPCL Petrol Pump",
                "area": "Andheri West / SV Road",
                "fuel_type": "Regular Petrol (91) & Speed",
                "notes": "Closest to Versova; fill before hitting morning city traffic",
                "has_nitrogen": True,
            },
            {
                "name": "HPCL Petrol Pump",
                "area": "Kharghar / Panvel Naka",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Last Mumbai/Navi Mumbai station before Expressway / NH66 split",
                "has_nitrogen": True,
            },
            {
                "name": "BPCL Fuel Station",
                "area": "Khalapur / Expressway Food Mall",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Ideal breakfast & tyre check stop on Expressway",
                "has_nitrogen": True,
            },
            {
                "name": "IOCL Fuel Station",
                "area": "Kolhapur Bypass / NH48",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-way refuel point on NH48 before Amboli Ghat descent",
                "has_nitrogen": True,
            },
        ],
    },
    {
        "date": "2026-10-04",
        "from_city": "Goa",
        "to_city": "Mumbai",
        "trip_type": "goa",
        "routes": [
            {
                "name": "Option 1 (Fastest & Smoothest): NH48 via Amboli Ghat & Kolhapur",
                "tag": "RECOMMENDED DRIVING ROUTE",
                "tag_color": "#15803d",
                "tag_bg": "#dcfce7",
                "highway": "NH48 (Panaji → Amboli Ghat → Nipani → Kolhapur → Pune → Mumbai)",
                "distance_km": 590,
                "drive_time": "10 – 11 Hours",
                "toll_estimate": "₹1,050 – ₹1,200",
                "road_condition": "Smooth 4-6 lane carriageway past Nipani. Consistently high speeds, ample food plazas.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Panaji,+Goa&destination=Versova,+Mumbai&waypoints=Kolhapur,+Maharashtra%7CPune,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Mumbai+India&navigate=yes",
            },
            {
                "name": "Option 2 (Scenic Coastal): NH66 Coastal Highway",
                "tag": "SCENIC ALTERNATIVE",
                "tag_color": "#0369a1",
                "tag_bg": "#e0f2fe",
                "highway": "NH66 (Sawantwadi → Chiplun → Mangaon → Pen → Panvel → Mumbai)",
                "distance_km": 550,
                "drive_time": "12 – 14 Hours",
                "toll_estimate": "₹600 – ₹750",
                "road_condition": "Coastline route; slower due to diversions and narrow highway sections.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Panaji,+Goa&destination=Versova,+Mumbai&waypoints=Sawantwadi,+Maharashtra%7CChiplun,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Mumbai+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "BPCL Fuel Station",
                "area": "Panaji / NH66 Bypass, Goa",
                "fuel_type": "Regular Petrol (91)",
                "notes": "FILL FULL TANK IN GOA! Goa fuel is ~₹2.50/L cheaper than Maharashtra",
                "has_nitrogen": False,
            },
            {
                "name": "HPCL Auto Care",
                "area": "Mapusa / NH66 North Goa",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Alternate North Goa fill-up before exiting state border",
                "has_nitrogen": True,
            },
            {
                "name": "IOCL Fuel Station",
                "area": "Nipani / Kolhapur Toll NH48",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-way refill on NH48",
                "has_nitrogen": True,
            },
            {
                "name": "BPCL Highway Care",
                "area": "Shirwal / Pune Bypass NH48",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Final top-up stop before Mumbai-Pune Expressway entry",
                "has_nitrogen": True,
            },
        ],
    },
    {
        "date": "2026-10-09",
        "from_city": "Mumbai",
        "to_city": "Pune",
        "trip_type": "pune",
        "routes": [
            {
                "name": "Optimized Route: Mumbai-Pune Expressway (Yashwantrao Chavan) / NH48",
                "tag": "FASTEST EXPRESSWAY ROUTE",
                "tag_color": "#1d4ed8",
                "tag_bg": "#dbeafe",
                "highway": "Versova → JVLR → Eastern Express Highway / SCLR → Expressway → Wakad",
                "distance_km": 149,
                "drive_time": "2.5 – 3 Hours",
                "toll_estimate": "₹320 (one-way)",
                "road_condition": "6-lane access-controlled expressway. Smooth tarmac, dedicated ghat lanes, food plazas at Khalapur and Ozarde.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Versova,+Mumbai&destination=Pune,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Pune+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "HPCL Petrol Pump",
                "area": "Versova / JP Road",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Fill locally in Versova before heading into peak morning traffic",
                "has_nitrogen": True,
            },
            {
                "name": "HPCL Petrol Pump",
                "area": "Kharghar / Panvel Naka",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Last pump before Mumbai-Pune Expressway toll gate",
                "has_nitrogen": True,
            },
            {
                "name": "IOCL Fuel Station",
                "area": "Khalapur / Expressway Service Plaza",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-expressway top-up & nitrogen tyre check",
                "has_nitrogen": True,
            },
        ],
    },
    {
        "date": "2026-10-10",
        "from_city": "Pune",
        "to_city": "Mumbai",
        "trip_type": "pune",
        "routes": [
            {
                "name": "Optimized Route: Mumbai-Pune Expressway (Yashwantrao Chavan) / NH48",
                "tag": "FASTEST EXPRESSWAY ROUTE",
                "tag_color": "#1d4ed8",
                "tag_bg": "#dbeafe",
                "highway": "Wakad / Hinjewadi → Expressway → Panvel → JVLR → Versova",
                "distance_km": 149,
                "drive_time": "2.5 – 3 Hours",
                "toll_estimate": "₹320 (one-way)",
                "road_condition": "Smooth expressway run descending through Bhor Ghat into Navi Mumbai.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Pune,+Maharashtra&destination=Versova,+Mumbai&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Mumbai+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "BPCL Speed Petrol",
                "area": "Wakad / Hinjewadi Link Rd, Pune",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Fill up in Pune before entering the Expressway",
                "has_nitrogen": True,
            },
            {
                "name": "HPCL Fuel Station",
                "area": "Dehu Road / Old Pune-Mumbai Highway",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Convenient Pune exit station with Nitrogen tyre inflation",
                "has_nitrogen": True,
            },
            {
                "name": "BPCL Expressway Plaza",
                "area": "Ozarde Food Mall / Expressway",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-way expressway food & fuel stop",
                "has_nitrogen": False,
            },
        ],
    },
    {
        "date": "2026-10-15",
        "from_city": "Mumbai",
        "to_city": "Pune",
        "trip_type": "pune",
        "routes": [
            {
                "name": "Optimized Route: Mumbai-Pune Expressway (Yashwantrao Chavan) / NH48",
                "tag": "FASTEST EXPRESSWAY ROUTE",
                "tag_color": "#1d4ed8",
                "tag_bg": "#dbeafe",
                "highway": "Versova → JVLR → Eastern Express Highway / SCLR → Expressway → Wakad",
                "distance_km": 149,
                "drive_time": "2.5 – 3 Hours",
                "toll_estimate": "₹320 (one-way)",
                "road_condition": "6-lane expressway. Check tyre pressure before Ghat ascent.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Versova,+Mumbai&destination=Pune,+Maharashtra&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Pune+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "HPCL Petrol Pump",
                "area": "Versova / JP Road",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Fill locally in Versova before heading into traffic",
                "has_nitrogen": True,
            },
            {
                "name": "HPCL Petrol Pump",
                "area": "Kharghar / Panvel Naka",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Last pump before Mumbai-Pune Expressway toll gate",
                "has_nitrogen": True,
            },
            {
                "name": "IOCL Fuel Station",
                "area": "Khalapur / Expressway Service Plaza",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-expressway top-up & nitrogen tyre check",
                "has_nitrogen": True,
            },
        ],
    },
    {
        "date": "2026-10-17",
        "from_city": "Pune",
        "to_city": "Mumbai",
        "trip_type": "pune",
        "routes": [
            {
                "name": "Optimized Route: Mumbai-Pune Expressway (Yashwantrao Chavan) / NH48",
                "tag": "FASTEST EXPRESSWAY ROUTE",
                "tag_color": "#1d4ed8",
                "tag_bg": "#dbeafe",
                "highway": "Wakad / Hinjewadi → Expressway → Panvel → JVLR → Versova",
                "distance_km": 149,
                "drive_time": "2.5 – 3 Hours",
                "toll_estimate": "₹320 (one-way)",
                "road_condition": "Smooth expressway run descending through Bhor Ghat into Navi Mumbai.",
                "gmaps_link": "https://www.google.com/maps/dir/?api=1&origin=Pune,+Maharashtra&destination=Versova,+Mumbai&travelmode=driving",
                "waze_link": "https://waze.com/ul?q=Mumbai+India&navigate=yes",
            },
        ],
        "stations": [
            {
                "name": "BPCL Speed Petrol",
                "area": "Wakad / Hinjewadi Link Rd, Pune",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Fill up in Pune before entering the Expressway",
                "has_nitrogen": True,
            },
            {
                "name": "HPCL Fuel Station",
                "area": "Dehu Road / Old Pune-Mumbai Highway",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Convenient Pune exit station with Nitrogen tyre inflation",
                "has_nitrogen": True,
            },
            {
                "name": "BPCL Expressway Plaza",
                "area": "Ozarde Food Mall / Expressway",
                "fuel_type": "Regular Petrol (91)",
                "notes": "Mid-way expressway food & fuel stop",
                "has_nitrogen": False,
            },
        ],
    },
]

# ---------------------------------------------------------------------------
# Live Fuel Price Fetching
# ---------------------------------------------------------------------------

def _fetch_city_petrol_price(city_key: str) -> float:
    """
    Fetches today's live petrol price (india_fuel: goodreturns, then bankbazaar).
    Falls back to regional benchmark if unavailable.
    """
    defaults = {            # last known retail rates (Oct 2026), used only if every source fails
        "mumbai": 111.21,
        "pune": 112.02,
        "goa": 104.06,
    }
    live = india_fuel.fetch(city_key, "petrol")
    if live:
        return live.price
    print(f"warning: no live petrol price for {city_key}; using last known benchmark")
    return defaults.get(city_key, 111.21)


def _city_to_api_key(city: str) -> str:
    mapping = {"Mumbai": "mumbai", "Goa": "goa", "Pune": "pune"}
    return mapping.get(city, city.lower())


# ---------------------------------------------------------------------------
# HTML Builders
# ---------------------------------------------------------------------------

def _build_route_cards_html(routes: list) -> str:
    """Renders visual driving route comparison cards with Google Maps and Waze buttons."""
    cards_html = ""
    for r in routes:
        cards_html += f"""
        <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
            <span style="background:{r['tag_bg']};color:{r['tag_color']};padding:3px 8px;border-radius:4px;font-size:11px;font-weight:800;letter-spacing:0.5px;">
              {r['tag']}
            </span>
            <span style="font-size:12px;color:#475569;font-weight:600;">
              ⏱️ {r['drive_time']} &nbsp;•&nbsp; 📏 ~{r['distance_km']} km
            </span>
          </div>
          <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:4px;">
            {r['name']}
          </div>
          <div style="font-size:12px;color:#334155;line-height:1.5;margin-bottom:8px;">
            🛣️ <strong>Highway</strong>: {r['highway']}<br>
            💰 <strong>Tolls</strong>: {r['toll_estimate']}<br>
            ℹ️ <strong>Road State</strong>: {r['road_condition']}
          </div>
          <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
            <a href="{r['gmaps_link']}"
               style="background:#0284c7;color:#ffffff;padding:8px 14px;text-decoration:none;border-radius:6px;font-weight:700;font-size:12px;display:inline-block;white-space:nowrap;">
              🗺️ Open in Google Maps
            </a>
            <a href="{r['waze_link']}"
               style="background:#059669;color:#ffffff;padding:8px 14px;text-decoration:none;border-radius:6px;font-weight:700;font-size:12px;display:inline-block;white-space:nowrap;">
              🧭 Open in Waze
            </a>
          </div>
        </div>
        """
    return cards_html


def _build_stations_table_html(stations: list, petrol_price: float) -> str:
    """Renders HTML table rows for recommended stations along departure route."""
    rows = ""
    full_tank_cost = round(petrol_price * TANK_CAPACITY_L, 2)
    card_rec = recommend_card(full_tank_cost)

    for idx, s in enumerate(stations):
        bg = "#f8fafc" if idx % 2 == 0 else "#ffffff"
        nitrogen_badge = (
            '<span style="background-color:#dcfce7;color:#15803d;padding:2px 6px;border-radius:4px;font-weight:600;font-size:11px;">🎈 Nitrogen</span>'
            if s["has_nitrogen"]
            else '<span style="background-color:#f1f5f9;color:#64748b;padding:2px 6px;border-radius:4px;font-size:11px;">💨 Air Only</span>'
        )
        query = urllib.parse.quote_plus(f"{s['name']} {s['area']}")
        maps_url = f"https://www.google.com/maps/search/?api=1&query={query}"

        rows += f"""
        <tr style="background-color:{bg};border-bottom:1px solid #e2e8f0;">
          <td style="padding:10px 8px;font-weight:600;color:#0f172a;font-size:13px;">
            {s['name']}<br>
            <span style="font-size:11px;color:#64748b;font-weight:normal;">{s['area']}</span>
          </td>
          <td style="padding:10px 8px;font-size:12px;color:#0369a1;">
            ⛽ {s['fuel_type']}<br>
            <span style="font-size:11px;color:#64748b;">{s.get('notes', '')}</span>
          </td>
          <td style="padding:10px 8px;white-space:nowrap;">
            <span style="color:#0f172a;font-weight:700;font-size:14px;">₹{petrol_price:.2f}/L</span><br>
            <span style="font-size:11px;color:#64748b;">Tank ≈ ₹{full_tank_cost:,.0f}</span>
          </td>
          <td style="padding:10px 8px;white-space:nowrap;">{nitrogen_badge}</td>
          <td style="padding:10px 8px;font-size:12px;color:#d97706;font-weight:600;white-space:nowrap;">
            💳 {card_rec['card_short']}<br>
            <span style="font-size:10px;color:#166534;background:#dcfce7;padding:1px 4px;border-radius:3px;">
              {card_rec['benefit_summary']}
            </span>
          </td>
          <td style="padding:10px 8px;text-align:right;white-space:nowrap;">
            <a href="{maps_url}"
               style="background-color:#0284c7;color:#ffffff;padding:6px 10px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;white-space:nowrap;font-size:12px;">
              📍 Maps
            </a>
          </td>
        </tr>
        """
    return rows


# ---------------------------------------------------------------------------
# Email Dispatcher
# ---------------------------------------------------------------------------

def build_and_send_trip_email(trip: dict) -> bool:
    """Builds and dispatches the customized road trip digest to Dad."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Error: SENDER_EMAIL or SENDER_PASSWORD not configured.")
        return False

    recipient_list = [r.strip() for r in RECEIVER_EMAIL.split(",") if r.strip()]
    if not recipient_list:
        print("Error: RECEIVER_EMAIL not set.")
        return False

    from_city = trip["from_city"]
    to_city = trip["to_city"]
    trip_type = trip["trip_type"]
    routes = trip["routes"]
    stations = trip["stations"]

    # Live prices
    api_key = _city_to_api_key(from_city)
    petrol_price = _fetch_city_petrol_price(api_key)
    full_tank_cost = round(petrol_price * TANK_CAPACITY_L, 2)
    card_rec = recommend_card(full_tank_cost)

    # Styling per destination
    if trip_type == "goa":
        header_gradient = "linear-gradient(135deg, #065f46, #0f766e)"
        badge_emoji = "🏖️"
        theme_color = "#0f766e"
    else:
        header_gradient = "linear-gradient(135deg, #1e3a8a, #1d4ed8)"
        badge_emoji = "🏙️"
        theme_color = "#1d4ed8"

    subject = f"🚗 Trip Alert: {from_city} → {to_city} Today — Route Options & Fuel Guide"

    # Plain text version
    plain_text = (
        f"ROAD TRIP TODAY: {from_city} to {to_city} ({VEHICLE_NAME})\n"
        f"Departure Date: {trip['date']}\n\n"
        f"LIVE PETROL ({from_city}): ₹{petrol_price:.2f}/L (Full 45L tank: ~₹{full_tank_cost:,.0f})\n\n"
        f"CREDIT CARD OPTIMIZATION (DAD'S CARDS):\n"
        f"• FULL TANK (~₹{full_tank_cost:,.0f}): Swipe HDFC REGALIA (1% surcharge waived up to ₹5,000 in one swipe; saves ~₹{full_tank_cost * 0.01:.0f}).\n"
        f"• TOP-UPS (≤ ₹4,000): Swipe AMAZON PAY ICICI (1% surcharge waived, uncapped monthly).\n"
        f"• AVOID: HSBC RuPay and Mastercard debit cards for fuel (no surcharge waiver).\n\n"
        f"TYRE PRESSURE: {REC_TYRE_PSI} — Top up with Nitrogen before highway.\n\n"
        "DRIVING ROUTES:\n"
    )
    for r in routes:
        plain_text += (
            f"[{r['tag']}] {r['name']}\n"
            f"Distance: ~{r['distance_km']} km | Time: {r['drive_time']} | Toll: {r['toll_estimate']}\n"
            f"Google Maps: {r['gmaps_link']}\n"
            f"Waze: {r['waze_link']}\n\n"
        )
    plain_text += "DEPARTURE FUEL STATIONS:\n"
    for s in stations:
        plain_text += f"• {s['name']} ({s['area']}) - {s['fuel_type']} [{s.get('notes', '')}]\n"

    # HTML version
    route_cards_html = _build_route_cards_html(routes)
    station_rows_html = _build_stations_table_html(stations, petrol_price)

    # Route options title
    route_section_title = (
        "🧭 Driving Route Options (Pick Your Route This Morning)"
        if len(routes) > 1
        else "🧭 Optimized Driving Route & Navigation"
    )

    html_content = f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
  </head>
  <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#334155;margin:0;padding:12px;background-color:#f1f5f9;">
    <div style="max-width:720px;margin:0 auto;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">

      <!-- Header -->
      <div style="background:{header_gradient};padding:22px;color:#ffffff;">
        <span style="background:rgba(255,255,255,0.2);color:#ffffff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">
          One-Time Road Trip Guide
        </span>
        <h2 style="margin:8px 0 4px 0;font-size:22px;font-weight:800;">
          {badge_emoji} {from_city} → {to_city}
        </h2>
        <p style="margin:0;font-size:12px;opacity:0.9;">
          Personalized Route, Fuel &amp; Card Guide for Dad • {VEHICLE_NAME}
        </p>
      </div>

      <!-- Vehicle Profile & Dad's Cards -->
      <div style="padding:16px 20px;background-color:#f0fdf4;border-bottom:1px solid #bbf7d0;color:#166534;font-size:12px;line-height:1.7;">
        <div style="font-weight:800;font-size:14px;color:#14532d;margin-bottom:6px;">
          🚘 Vehicle &amp; Credit Card Briefing
        </div>
        • ⛽ <strong>Live Petrol ({from_city})</strong>: <strong>₹{petrol_price:.2f}/L</strong> &nbsp;•&nbsp; <strong>Full Tank (45L)</strong>: <strong>₹{full_tank_cost:,.2f}</strong><br>
        • 🛞 <strong>Tyre Pressure</strong>: <strong>{REC_TYRE_PSI}</strong>. Inflate with <strong>Nitrogen 🎈</strong> before highway for temperature stability.<br>
        • 💳 <strong>Full Tank Swipe (~₹{full_tank_cost:,.0f}) → Swipe HDFC REGALIA</strong>: Its 1% surcharge waiver covers transactions up to <strong>₹5,000</strong> in a single swipe (saving ~₹{full_tank_cost * 0.01:.0f} surcharge + 18% GST).<br>
        • 💳 <strong>Top-ups / Partial fills (≤ ₹4,000) → Swipe AMAZON PAY ICICI</strong>: 1% waiver with no monthly ceiling, saving Regalia's monthly limit.<br>
        • 🚫 <strong>Avoid for fuel</strong>: HSBC RuPay (no fuel waiver on Cashback card) and Mastercard debit card. Note: No credit card earns reward points on fuel MCC per bank rules.
      </div>

      <!-- Route Cards Section -->
      <div style="padding:18px 20px 8px 20px;background-color:#f8fafc;border-bottom:1px solid #e2e8f0;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:{theme_color};">
          {route_section_title}
        </h3>
        {route_cards_html}
      </div>

      <!-- Departure Stations Section -->
      <div style="padding:18px 20px;background-color:#ffffff;">
        <h3 style="margin:0 0 6px 0;font-size:16px;color:{theme_color};">
          ⛽ Recommended Departure Fuel Stations ({from_city})
        </h3>
        <p style="margin:0 0 12px 0;font-size:12px;color:#64748b;">
          Fill up before hitting the open highway. Stations are listed in order of departure:
        </p>
        <div style="width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;">
          <table style="width:100%;border-collapse:collapse;text-align:left;min-width:600px;">
            <thead>
              <tr style="background-color:#f1f5f9;border-bottom:2px solid #e2e8f0;color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">
                <th style="padding:10px 8px;">Station &amp; Area</th>
                <th style="padding:10px 8px;">Fuel Type &amp; Tip</th>
                <th style="padding:10px 8px;">Rate / Full Tank</th>
                <th style="padding:10px 8px;">Tyre Care</th>
                <th style="padding:10px 8px;">Card to Swipe</th>
                <th style="padding:10px 8px;text-align:right;">Maps</th>
              </tr>
            </thead>
            <tbody>
              {station_rows_html}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Footer -->
      <div style="padding:14px 20px;background-color:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;text-align:center;">
        Costco &amp; Fuel Notifier • One-Time Road Trip Edition for Dad • Have a safe and smooth journey to {to_city}! 🙏
      </div>
    </div>
  </body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(recipient_list)
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        print(f"Connecting to SMTP {SMTP_SERVER}:{SMTP_PORT}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient_list, msg.as_string())
        server.quit()
        print(f"✅ Road trip digest sent: {from_city} to {to_city} to {', '.join(recipient_list)}")
        return True
    except Exception as exc:
        print(f"❌ Failed to send road trip digest: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today_ist = datetime.now(IST).strftime("%Y-%m-%d")
    print(f"Road trip check for IST date: {today_ist}")

    matched_trip = None
    for trip in TRIP_SCHEDULE:
        if trip["date"] == today_ist:
            matched_trip = trip
            break

    if matched_trip is None:
        print("No road trip scheduled for today -- exiting silently.")
        sys.exit(0)

    print(f"🚗 Matched road trip: {matched_trip['from_city']} to {matched_trip['to_city']}")
    success = build_and_send_trip_email(matched_trip)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
