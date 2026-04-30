# Klartion

**Your bank transactions, inside Notion. Automatically.**

Klartion connects to your bank (EU, US, or Canada) and writes your transactions into a Notion database every day. It runs on your own machine — your financial data never touches any third-party server.

→ **[klartion.com](https://klartion.com)** · [Buy a licence](https://klartion.com)

---

## What you get

- **Flexible sync frequency** — sync every 6, 12, or 24 hours, at a time you choose (SimpleFIN accounts always sync once daily, due to API rate limits)
- **2,500+ European banks via Enable Banking** — Revolut, N26, Monzo, Wise, Millennium BCP, Santander, ING, BNP Paribas, and more across 29 countries
- **Most major US and Canadian banks via SimpleFIN Bridge** — Chase, Bank of America, Wells Fargo, Capital One, RBC, TD, BMO, Scotiabank, and more
- **Multiple bank accounts** — connect up to 2 bank accounts by default, with the option to add more
- **Read-only, always** — Klartion can never move money or modify your account
- **Pending transaction tracking** — pending transactions are imported and automatically updated to Cleared or Cancelled when they settle
- **Duplicate detection** — Klartion tracks every transaction ID so nothing ever gets imported twice
- **Email notifications** — a summary email on success, an alert if something goes wrong
- **Your data, your machine** — bank data goes directly from your provider to your machine, never our servers
- **Lightweight** — runs as a single Docker container, uses minimal CPU and memory
- **Runs anywhere** — supports x86, Raspberry Pi, and other ARM devices out of the box

---

## Requirements

- Docker and Docker Compose (runs on x86, Raspberry Pi, and other ARM devices)
- One of the following bank-data providers, depending on where your bank is:
  - **EU banks:** an [Enable Banking](https://enablebanking.com) account (free)
  - **US/Canadian banks:** a [SimpleFIN Bridge](https://bridge.simplefin.org) account ($15/year — required by SimpleFIN, not Klartion)
- A [Notion](https://notion.so) account
- A Klartion licence key — [buy one at klartion.com](https://klartion.com)

---

## Quick start

> **New to self-hosting?** Follow the step-by-step guide at [klartion.com/getting-started](https://klartion.com/getting-started.html) — it walks you through everything from Enable Banking setup to running your first sync.

### 1. Get your licence key

Purchase at [klartion.com](https://klartion.com). Your key is delivered to your email instantly.

### 2. Set up your bank-data provider

Pick the provider that covers your bank.

#### Option A — EU banks: Enable Banking

Enable Banking is the regulated open banking provider that connects Klartion to your EU bank.

1. Sign up at [enablebanking.com](https://enablebanking.com)
2. Go to **API applications** and click **Register new application**
3. Fill in the form:
   - **Application name:** Klartion
   - **Allowed redirect URLs:** `https://klartion.com/callback`
   - **Application description:** Connect my bank to Notion
   - **Email for data protection matters:** your email address
   - **Privacy URL:** `https://klartion.com/privacy`
   - **Terms URL:** `https://klartion.com/terms`
4. Click **Register** — a `.pem` file will be saved to your Downloads folder. The filename matches your Application ID (e.g. `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.pem`). Keep it safe — you'll need it in the setup wizard.
5. Click **Activate by linking accounts** on your application page
6. Select your country and bank from the dropdowns and click **Link**
7. Follow the steps to log in to your bank and approve read-only access — this activates your Enable Banking app

#### Option B — US/Canadian banks: SimpleFIN Bridge

SimpleFIN Bridge is the bank-data provider Klartion uses for North American banks.

> **Verify your bank first.** Before paying SimpleFIN's $15/year fee, check that your bank is on their [supported institutions list](https://beta-bridge.simplefin.org/info/institutions). A handful of institutions are known to misbehave on SimpleFIN's side (Apple Card needs daily 2FA; Capital One returns no pending transactions; Mercury emits phantom mirror transactions across linked accounts) — see [klartion.com/help](https://klartion.com/help) for details.

1. Sign up at [bridge.simplefin.org](https://bridge.simplefin.org). SimpleFIN charges $15/year — this is paid to SimpleFIN directly, not to Klartion.
2. Inside SimpleFIN, link the banks you want to sync. SimpleFIN's interface will tell you whether your bank is supported before you commit.
3. Once your bank is linked, click **Setup Token** in SimpleFIN. You'll get a one-time code. Copy it.
4. You'll paste this code into Klartion's setup wizard in step 5.

You can mix providers freely — for example, an Enable Banking connection for your EU account and a SimpleFIN connection for your US account on the same Klartion install.

### 3. Install Klartion

**On your server**, create the folder and download the compose file:
```bash
mkdir -p ~/klartion/data && cd ~/klartion
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/docker-compose.yml
```

You do **not** need to clone the repository or create a `.env` file for the standard setup.

**Back on your server**, start the container:
```bash
docker compose up -d
```

Open **http://your-server-address:3001** in your browser. The setup wizard will guide you through the rest, including uploading your Enable Banking `.pem` file directly in the browser.

---

## Setup wizard

The browser-based wizard walks you through six steps:

1. **Licence** — enter your key to activate Klartion on this machine
2. **Notion** — duplicate the [ready-made template](https://hilarious-mirror-513.notion.site/4f95e8e7b23183c3be5381bef1d906b2), create a free integration, and paste your credentials
3. **Notifications** — set your email and SMTP credentials
4. **Sync** — choose your sync frequency (every 6, 12, or 24 hours)
5. **Bank** — connect your bank. The Bank tab has an EU vs US/Canada toggle. For EU, upload your Enable Banking `.pem` (App ID auto-fills from the filename) and authorise via OAuth. For US/Canada, paste your SimpleFIN setup token and pick which accounts to sync. You can connect up to 2 bank accounts by default and mix providers freely. Each bank's transactions are tagged with the bank name in a "Bank" column in Notion.
6. **Status** — view sync history, manage bank connections, check for updates

Once complete, Klartion runs silently in the background. To add a second bank, go to the **Bank** tab and search for another bank. Need more than 2? You can purchase additional bank account slots from the status page.

---

## Session renewal

**Enable Banking (EU)** requires you to re-authorise access roughly every 180 days. If you configured email notifications, you will receive a warning before expiry. To re-authorise, go to the **Status** page in the Klartion web UI and click **Re-authorise bank**.

**SimpleFIN (US/Canada)** access tokens do not expire on a clock — they remain valid as long as your SimpleFIN subscription is active and your bank does not revoke the connection. If access is revoked (subscription lapse, bank change, manual revocation in SimpleFIN), Klartion will surface a banner asking you to regenerate a setup token at [bridge.simplefin.org](https://bridge.simplefin.org) and reconnect the affected account.

---

## How it works
```
Your bank
   ↓  (read-only via Enable Banking [EU] or SimpleFIN Bridge [US/CA])
Klartion (running on your machine)
   ↓  (Notion API)
Your Notion database
   ↓  (SMTP)
Your inbox  ← daily summary email
```

On each sync run, Klartion:

1. Validates your licence key
2. Fetches transactions since the last sync from each connected provider
3. Filters out any transaction IDs already in the local SQLite database
4. Writes new transactions to your Notion database
5. Updates any previously pending transactions that have since settled
6. Logs the result and sends you a notification email

---

## Notion database columns

The [Klartion template](https://hilarious-mirror-513.notion.site/4f95e8e7b23183c3be5381bef1d906b2) includes all of these pre-configured:

| Column | Type | Description |
|---|---|---|
| Merchant | Title | Payee or sender name |
| Date | Date | Booking date |
| Amount | Number | Positive = incoming, negative = outgoing |
| Currency | Text | ISO code, e.g. EUR |
| Category | Select | Bank transaction code, e.g. CARD_PAYMENT |
| Reference | Text | Remittance information |
| Direction | Select | `in` or `out` |
| Status | Select | `Cleared`, `Pending`, or `Cancelled` |
| Transaction ID | Text | Provider reference (deduplication key) |
| Bank | Text | Bank name (e.g. Revolut, N26) |

---

## Updating

Click **Check for updates** on the Status page. Klartion will pull the latest version and restart automatically.

Or run manually:
```bash
docker compose pull && docker compose up -d
```

---

## Licence deactivation

Each licence key supports up to 2 machine activations. To move Klartion to a new machine, go to the **Status** page in the web UI and click **Deactivate licence** before reinstalling.

---

## Licence

MIT + Commons Clause. Free to self-host for personal use. You may not sell, sublicence, or offer Klartion as a competing service. See [LICENSE](LICENSE).

Built by [David Alves](https://david-alves.com).
