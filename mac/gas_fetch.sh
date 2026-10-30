#!/bin/zsh
# Mac half of the US gas pipeline. GasBuddy turns away GitHub's runners, so the Mac fetches
# prices and pushes data/gas_snapshot.json; that push runs the cloud workflow (email, Sheet,
# prediction, dashboard data). launchd starts this every 30 min (mac/install.sh); it fetches
# only when the last snapshot is MIN_AGE_MIN old, so prices refresh ~every 3 h while the Mac
# is awake and online, and a Mac that slept catches up within 30 min of waking.
set -u
MIN_AGE_MIN=${MIN_AGE_MIN:-170}
RETRY_AFTER_FAIL_MIN=${RETRY_AFTER_FAIL_MIN:-60}
cd "${0:A:h}/.." || exit 1
FAIL_MARK=data/cache/gas_fetch_failed
ts() { date '+%Y-%m-%d %H:%M:%S'; }

curl -fsS -m 10 -o /dev/null https://github.com || { echo "$(ts) offline, skipping"; exit 0; }
if [[ -f $FAIL_MARK && -n $(find "$FAIL_MARK" -mmin -$RETRY_AFTER_FAIL_MIN) ]]; then
  echo "$(ts) last fetch failed under ${RETRY_AFTER_FAIL_MIN} min ago, waiting"; exit 0
fi
git pull -q --rebase --autostash origin main || { echo "$(ts) git pull failed"; exit 1; }

age=$(.venv/bin/python gas_snapshot.py age)
if (( age < MIN_AGE_MIN )); then echo "$(ts) snapshot is ${age} min old, nothing to do"; exit 0; fi

if ! .venv/bin/python gas_tracker.py --fetch-only; then
  mkdir -p data/cache && touch "$FAIL_MARK"
  echo "$(ts) fetch failed (see the lines above); next try in ${RETRY_AFTER_FAIL_MIN} min"; exit 1
fi
rm -f "$FAIL_MARK"

git add data/gas_snapshot.json
git commit -q -m "chore(data): gas snapshot from the Mac $(date -u '+%Y-%m-%d %H:%MZ')" || exit 0
for i in 1 2 3; do
  git push -q origin HEAD:main && { echo "$(ts) pushed snapshot"; exit 0; }
  sleep 5; git pull -q --rebase origin main
done
echo "$(ts) push failed"; exit 1
