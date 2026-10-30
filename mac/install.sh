#!/bin/zsh
# One-time Mac setup for the gas fetcher. Run from a clone that launchd can read
# (~/Projects/costco_fuel_notifier, not OneDrive) on branch main:  zsh mac/install.sh
# Undo: launchctl bootout gui/$(id -u)/com.local.gasfetch && rm ~/Library/LaunchAgents/com.local.gasfetch.plist
set -eu
REPO=${0:A:h:h}
cd "$REPO"
[[ $(git branch --show-current) == main ]] || { echo "check out main first"; exit 1; }
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p ~/Library/Logs/costco-fuel-notifier
PLIST=~/Library/LaunchAgents/com.local.gasfetch.plist
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" mac/com.local.gasfetch.plist > "$PLIST"
launchctl bootout gui/$(id -u)/com.local.gasfetch 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Loaded com.local.gasfetch (every 30 min; fetches when prices are ~3 h old)."
echo "Log: ~/Library/Logs/costco-fuel-notifier/gas-fetch.log"
