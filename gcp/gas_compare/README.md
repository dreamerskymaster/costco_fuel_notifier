# Gas price source test: Google Maps vs GasBuddy (Nov 6 – Nov 20, 2026)

**Question:** can Google Maps' fuel prices replace the GasBuddy fetch that only
works from the Mac? If yes, the Mac is no longer needed for gas at all.

**How the test works:** twice a day (7:30 AM and 5:30 PM New York time) a small
Google Cloud job compares both sources for the same 11 corridor ZIPs, and once a
day it emails Ajith a scorecard. On Nov 20 it sends a final verdict and stops
asking Google for prices. Your normal gas emails, Sheet and dashboard are
**not touched**: they still come from GasBuddy through the Mac.

What it measures, for each source:

- how many stations have a price less than 12 hours old (older prices are hidden by the real email)
- how many of the 11 ZIPs have at least one fresh price
- whether **Costco Teterboro, Bayonne and New Rochelle** have a fresh price
- how old prices are (median)
- whether the source was usable at all (the Mac snapshot is too old when the Mac was asleep)

First look (Nov 5, ~11:50 PM): Google listed 216 stations (158 with prices,
including Stamford, which GasBuddy never returns) and agreed with GasBuddy to
within about 1¢ on the cheapest stations. But its prices were older (median
13 h) and it had **no price for Costco Teterboro or New Rochelle**.

## Where it lives

| Piece | Name (Google Cloud project `ajith-automations`) |
|---|---|
| Cloud Run job | `gas-compare` |
| Schedule | `gas-compare-twice-daily` (7:30 AM + 5:30 PM) |
| Run summaries | bucket `ajith-automations-gas-compare` (auto-deleted after 30 days) |
| Email settings | secret `gas-compare-env` (Gmail sender, app password, recipient) |
| Identities | `gas-compare-runner`, `gas-compare-scheduler`, `gas-compare-builder` |

- Run history and logs: https://console.cloud.google.com/run/jobs/details/us-central1/gas-compare/executions?project=ajith-automations
- Google API usage: https://console.cloud.google.com/apis/api/places.googleapis.com/metrics?project=ajith-automations

## Cost: $0, enforced

Google's fuel prices are billed as "Text Search Enterprise + Atmosphere": 1,000
free calls a month, then $0.04 each. The test uses 22 calls a day (~680/month).
A quota caps the project at **32 calls a day** (992/month at most), so it cannot
pass the free tier even if something loops. The job signs in with its own
service account, so there is no API key to leak.

## Commands

```bash
# run it now (no email unless it is after noon and none was sent today)
gcloud run jobs execute gas-compare --region us-central1 --project ajith-automations

# stop the test early / remove it after Nov 20
gcloud scheduler jobs delete gas-compare-twice-daily --location us-central1 --project ajith-automations
gcloud run jobs delete gas-compare --region us-central1 --project ajith-automations

# redeploy after changing compare.py (safe to re-run)
gcp/gas_compare/deploy.sh
```

## If Google wins

Move the fetch into the cloud pipeline: a Places-based `fetch_gas_prices()` in
`gas_tracker.py` run by `schedule.yml` on its own cron, the Mac launchd job
(`mac/`) retired, and the 32/day cap raised only as far as the free tier allows.
Google Maps terms also ask for "Prices from Google Maps" attribution where the
prices are shown, and limit how long Places data may be stored, so the
long-term price history would need a look before switching.
