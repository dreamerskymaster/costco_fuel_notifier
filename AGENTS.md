# AGENTS.md - Workspace Memory & User Preferences

## 1. User Vehicles & Profiles

### US Vehicle: Volkswagen Passat (2013 SE)
- **Engine / Fuel**: Regular Unleaded (87 Octane).
- **Fuel Tank Capacity**: **18.5 US Gallons** (70 Litres).
- **Highway Efficiency**: ~31 MPG (~573 miles highway cruising range).
- **Tyre Pressure Recommendation**: **34 PSI** (Cold Normal) / **38 PSI** (Loaded Highway).
- **Arbitrage Dynamic**: The Norwalk ↔ Jersey City round trip is ~120 miles. An 18.5-gal fill gives 570+ miles of range (4+ round trips). **Never buy gas in Connecticut**; fill up 100% in New Jersey at Costco Bayonne (`07002`) or Costco Teterboro (`07608`) to save ~$0.55–$0.60/gal (~$10.50+ per full tank).

### India Vehicle: Hyundai Venue (2019 Model)
- **Engine / Fuel**: Regular Petrol (91) / Diesel (BS-VI).
- **Fuel Tank Capacity**: 45 Litres (~₹4,680 full tank).
- **Tyre Pressure Recommendation**: **33 PSI** (Cold Normal) / **36 PSI** (Loaded Highway), Nitrogen preferred.

---

## 2. US Commute Corridor & Route Optimization (Non-Toll Preference)

- **Route**: Norwalk, CT (Home) ↔ 850 Westside Ave, Jersey City, NJ.
- **Timing**: Leaves Norwalk Friday after 5:00 PM EDT; returns from Jersey City Sunday evening.
- **E-ZPass & Toll Policy**: User holds a NY E-ZPass but **prefers to avoid tolls** and minimize travel costs:
  - **Friday (Norwalk → Jersey City) — 100% Toll-Free ($0.00)**:
    1. Take **Merritt Parkway (CT-15 S)** into **Hutchinson River Parkway (NY-15 S)** — completely bypasses the I-95 New Rochelle toll barrier.
    2. Cross Bronx Expressway (I-95 S) to the **George Washington Bridge (GWB)** — westbound into New Jersey is **100% Toll-Free**.
    3. Exit GWB onto **US-1&9 South (Tonnelle Ave)** straight to 850 Westside Ave — completely bypasses the NJ Turnpike toll.
    4. **Total Toll Paid**: **$0.00**.
  - **Sunday (Jersey City → Norwalk) — Toll-Minimized**:
    1. Take **US-1&9 North** through Jersey City up to GWB (Free, bypasses NJ Turnpike).
    2. Cross GWB Eastbound ($13.38 off-peak NY E-ZPass — *unavoidable Hudson River crossing*).
    3. Cross Bronx to **Hutchinson River Parkway North → Merritt Parkway (CT-15 N)** into Norwalk (Free, bypasses I-95 toll barrier).
- **Monitored ZIP Codes**:
  - `06854` (Norwalk, CT - Home)
  - `06901` (Stamford, CT - I-95 corridor)
  - `10801` (New Rochelle, NY - Costco New Rochelle)
  - `07608` (Teterboro, NJ - Costco Teterboro on Route 46/I-80)
  - `07306` (Jersey City, NJ - 850 Westside Ave neighborhood)
  - `07002` (Bayonne, NJ - Costco Bayonne, ~4.5 mi south of Westside Ave)
  - *(Milford, CT `06460` is permanently removed).*

---

## 3. Credit Card Optimization Rules

### US Cards:
1. **Costco Gas**: Citi Costco Anywhere Visa (4% cash back, Visa only).
2. **Standalone Gas** (Mobil, Shell, CITGO, Lukoil, QuickChek, BP, Exxon, Speedway): Citi Costco Anywhere Visa (4% primary) or Amex Blue Cash Everyday (3% secondary).
3. **Supermarket Gas** (Stop & Shop, Walmart, BJ's): Excluded from 4%/3% bonus; earns base 1%.

### India Cards (Dad):
1. **HDFC Regalia**: 1% fuel surcharge waiver on ₹400–₹5,000 (max ₹500 waiver/cycle). **Best for Full Tanks (~₹4,600+)**.
2. **Amazon Pay ICICI**: 1% fuel surcharge waiver on ₹400–₹4,000 (uncapped). **Best for top-ups ≤ ₹4,000**.
3. **HSBC RuPay (Cashback) & Mastercard Debit**: Excluded (no fuel waiver; 0% reward points on fuel across all Indian cards).

---

## 4. Workflows & Schedules

- **US Tracker (`gas_tracker.py`)**:
  - Every 3 hours: `0 */3 * * *`
  - Friday Pre-Departure Alert: `0 20 * * 5` (4:00 PM EDT / 20:00 UTC)
  - Sunday Pre-Return Alert: `0 19 * * 0` (3:00 PM EDT / 19:00 UTC)
- **Mumbai Daily Digest (`mumbai_gas_tracker.py`)**: `30 1 * * *` (7:00 AM IST)
- **Road Trip Digest (`road_trip_tracker.py`)**: `0 0 * * *` (5:30 AM IST on trip dates)
