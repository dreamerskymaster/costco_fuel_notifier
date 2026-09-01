# Costco & Top Tier Fuel Notifier

This project automatically scrapes gas prices along your commute and sends a daily email digest. It is configured to run automatically using GitHub Actions.

## Setup Instructions

1. **Upload to GitHub**: Upload all the contents of this folder into a new private GitHub repository. Make sure the hidden `.github` folder is included so the automation works!
2. **Google Sheets Setup**: 
   * Create a Google Sheet named exactly `Fuel Trends`.
   * Go to the Google Cloud Console, enable the **Google Sheets API**, and create a **Service Account**. 
   * Download the JSON key.
   * Share your `Fuel Trends` Google Sheet with the `client_email` found inside that JSON file as an Editor.
3. **GitHub Secrets**:
   Go to your GitHub repository **Settings > Secrets and variables > Actions**. Click **New repository secret** and add these two secrets:
   * `RECEIVER_EMAIL`: Your receiving email address (FormSubmit will forward digests here).
   * `GCP_SERVICE_ACCOUNT`: Paste the entire contents of your Service Account JSON file here.


## Testing
Go to the **Actions** tab in your repository, select "Daily Gas Price Tracker" on the left, and click the **Run workflow** button to test it immediately.
