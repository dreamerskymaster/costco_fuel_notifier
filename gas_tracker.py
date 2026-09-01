import os
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
import requests
from py_gasbuddy import GasBuddy
import gspread

# --- CONFIGURATION ---
ZIP_CODES = ["06460", "06854", "06901", "10801"] 

# Pulling credentials from Environment Variables (GitHub Secrets)
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")
SHEET_NAME = os.environ.get("SHEET_NAME", "Fuel Trends")
SHEET_URL = os.environ.get("SHEET_URL")
SHEET_ID = os.environ.get("SHEET_ID")
SERVICE_ACCOUNT_FILE = "service_account.json"


import re

LOCATION_QUERY_PRICES = "query LocationBySearchTerm($brandId: Int, $cursor: String, $fuel: Int, $lat: Float, $lng: Float, $maxAge: Int, $search: String) { locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat lng: $lng maxAge: $maxAge) { results { address { line1 } id name prices { cash { nickname postedTime price } credit { nickname postedTime price } fuelProduct longName } priceUnit currency id latitude longitude } } trends { areaName country today todayLow trend } } }"

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
                    
                    stations_data.append({
                        "name": name,
                        "zip": zip_code,
                        "distance": station.get("distance", "N/A"),
                        "price": price,
                        "formatted_price": formatted_price,
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
    Logs the lowest fuel price of the day to the 'Fuel Trends' Google Sheet.

    Args:
        stations (list[dict]): List of station objects sorted by price.
    """
    if not stations:
        return
        
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
        
        # Log the absolute cheapest station of the day for trend tracking
        best = stations[0]
        row = [
            datetime.now().strftime("%Y-%m-%d"), 
            best["name"], 
            best["zip"], 
            best["formatted_price"]
        ]
        sheet.append_row(row)
        print("Successfully logged lowest price to Google Sheets.")
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Google Sheet '{SHEET_NAME}' not found via Drive search. If shared, provide SHEET_URL or SHEET_ID.")
    except Exception as e:
        err_msg = e.__cause__ if hasattr(e, "__cause__") and e.__cause__ else e
        print(f"Could not write to Google Sheets: {err_msg}")



def send_email(stations):
    """
    Sends a digest of top fuel prices to RECEIVER_EMAIL via FormSubmit web API.

    FormSubmit forwards form data to the receiver's inbox without requiring
    SMTP credentials or Gmail app passwords.

    Args:
        stations (list[dict]): List of station objects sorted by price.
    """
    if not stations:
        print("No stations found.")
        return
        
    # Create a clean text summary with Waze navigation links
    summary = ""
    for s in stations[:10]:
        stale = " ⚠️ (Stale >12h)" if s["stale"] else ""
        summary += f"• {s['name']} ({s['zip']}): {s['formatted_price']} | {s['distance']} mi | Updated: {s['last_updated']}{stale}\n  🚗 Navigate: {s['waze_link']}\n\n"
    
    # Send form data to FormSubmit's AJAX API
    url = f"https://formsubmit.co/ajax/{RECEIVER_EMAIL}"
    payload = {
        "_subject": "Fuel Update: NYC Commute Route",
        "Top_10_Cheapest_Stations": summary,
        "_template": "box" # Wraps the email in a clean visual border
    }
    headers = {
        "Referer": "https://formsubmit.co"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers)
        res_data = response.json()
        if res_data.get("success") == "true":
            print("Digest email sent successfully via FormSubmit.")
        else:
            print(f"FormSubmit Notice: {res_data.get('message')}")
    except Exception as e:
        print(f"Failed to send email: {e}")


async def main():
    print("Fetching gas prices...")
    stations = await fetch_gas_prices()
    log_to_sheets(stations)
    send_email(stations)

if __name__ == "__main__":
    asyncio.run(main())

