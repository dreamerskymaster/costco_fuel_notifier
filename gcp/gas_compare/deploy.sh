#!/usr/bin/env bash
# Set up or update the gas-compare test on Google Cloud. Safe to re-run.
#
#   ENV_FILE=/path/to/gas-compare.env gcp/gas_compare/deploy.sh
#
# ENV_FILE (first run only) holds SMTP_USER, SMTP_PASSWORD (Gmail app password)
# and TO_EMAIL. It becomes the secret gas-compare-env; later runs leave it alone.
set -euo pipefail
cd "$(dirname "$0")"

PROJECT=${PROJECT:-ajith-automations}
REGION=${REGION:-us-central1}
BUCKET=$PROJECT-gas-compare
BUILD_BUCKET=$PROJECT-build
REPO=automations
SECRET=gas-compare-env
RUNNER=gas-compare-runner@$PROJECT.iam.gserviceaccount.com
BUILDER=gas-compare-builder@$PROJECT.iam.gserviceaccount.com
STARTER=gas-compare-scheduler@$PROJECT.iam.gserviceaccount.com
SCHEDULE=gas-compare-twice-daily
LABELS=app=gas-compare

g() { gcloud --project "$PROJECT" --quiet "$@"; }
exists() { "$@" >/dev/null 2>&1; }
say() { printf '\n== %s\n' "$*"; }
retry() { local i; for i in 1 2 3 4 5 6; do "$@" >/dev/null 2>&1 && return 0; sleep 10; done; "$@" >/dev/null; }

say "Service accounts"
make_sa() {
  exists g iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" \
    || g iam service-accounts create "$1" --display-name "$2" --description "$3"
}
make_sa gas-compare-runner "Gas compare: runs the job" \
  "Identity of the gas-compare Cloud Run job. Calls Google Places (fuel prices), writes run summaries to the gas-compare bucket, reads the gas-compare-env secret."
make_sa gas-compare-builder "Gas compare: builds the image" \
  "Used by Cloud Build when gcp/gas_compare/deploy.sh builds the container image."
make_sa gas-compare-scheduler "Gas compare: starts the job" \
  "Used by Cloud Scheduler to start gas-compare at 7:30 AM and 5:30 PM."

say "Bucket (run summaries, deleted after 30 days)"
if ! exists g storage buckets describe "gs://$BUCKET"; then
  g storage buckets create "gs://$BUCKET" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention --soft-delete-duration 0
  g storage buckets update "gs://$BUCKET" --update-labels "$LABELS,contents=run-summaries"
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' >/tmp/gc-lc.json
  g storage buckets update "gs://$BUCKET" --lifecycle-file /tmp/gc-lc.json
fi
if ! exists g storage buckets describe "gs://$BUILD_BUCKET"; then
  g storage buckets create "gs://$BUILD_BUCKET" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention --soft-delete-duration 0
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' >/tmp/gc-build-lc.json
  g storage buckets update "gs://$BUILD_BUCKET" --lifecycle-file /tmp/gc-build-lc.json
fi
retry g storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNNER" --role roles/storage.objectUser
retry g storage buckets add-iam-policy-binding "gs://$BUILD_BUCKET" \
  --member "serviceAccount:$BUILDER" --role roles/storage.objectViewer
# Calling Places with the job's own identity (no API key) needs this on the project.
retry g projects add-iam-policy-binding "$PROJECT" --condition None \
  --member "serviceAccount:$RUNNER" --role roles/serviceusage.serviceUsageConsumer
retry g projects add-iam-policy-binding "$PROJECT" --condition None \
  --member "serviceAccount:$BUILDER" --role roles/logging.logWriter

say "Image repository"
exists g artifacts repositories describe "$REPO" --location "$REGION" \
  || g artifacts repositories create "$REPO" --location "$REGION" --repository-format docker \
       --description "Container images for Ajith's automations" --labels "purpose=automations"
retry g artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:$BUILDER" --role roles/artifactregistry.writer

say "Settings secret ($SECRET)"
if ! exists g secrets describe "$SECRET"; then
  [ -n "${ENV_FILE:-}" ] && [ -f "$ENV_FILE" ] || { echo "Set ENV_FILE for the first run" >&2; exit 1; }
  g secrets create "$SECRET" --replication-policy user-managed --locations "$REGION" \
    --labels "$LABELS" --data-file "$ENV_FILE"
else
  echo "Exists; left unchanged."
fi
retry g secrets add-iam-policy-binding "$SECRET" \
  --member "serviceAccount:$RUNNER" --role roles/secretmanager.secretAccessor

say "Build image"
TAG=$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . && echo "" || echo "-dirty")
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/gas-compare:$TAG
g builds submit --region "$REGION" --config cloudbuild.yaml \
  --substitutions "_IMAGE=$IMAGE" \
  --service-account "projects/$PROJECT/serviceAccounts/$BUILDER" \
  --gcs-source-staging-dir "gs://$BUILD_BUCKET/source" .

say "Cloud Run job"
g run jobs deploy gas-compare --region "$REGION" --image "$IMAGE" \
  --service-account "$RUNNER" \
  --set-secrets "GAS_COMPARE_ENV=$SECRET:latest" \
  --set-env-vars "STATE_BUCKET=$BUCKET,GOOGLE_CLOUD_PROJECT=$PROJECT" \
  --cpu 1 --memory 512Mi --task-timeout 10m --max-retries 0 --labels "$LABELS"
retry g run jobs add-iam-policy-binding gas-compare --region "$REGION" \
  --member "serviceAccount:$STARTER" --role roles/run.invoker

say "Schedule"
if exists g scheduler jobs describe "$SCHEDULE" --location "$REGION"; then
  echo "$SCHEDULE exists; left unchanged."
else
  g scheduler jobs create http "$SCHEDULE" --location "$REGION" --schedule "30 7,17 * * *" \
    --time-zone America/New_York --http-method POST \
    --description "Starts gas-compare (Google Maps vs GasBuddy fuel price test) at 7:30 AM and 5:30 PM New York time. Delete after the test ends 2026-11-20." \
    --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/gas-compare:run" \
    --oauth-service-account-email "$STARTER"
fi

say "Done. Image: $IMAGE"
