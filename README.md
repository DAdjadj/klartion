# Klartion

**Your bank transactions, inside Notion. Automatically.**

Klartion connects to your EU bank via open banking and writes your transactions into a Notion database every day. It runs on your own machine — your financial data never touches any third-party server.

→ **[klartion.com](https://klartion.com)** · [Buy a licence](https://klartion.com)

---

## What you get

- **Daily automatic sync** — transactions land in Notion once a day, at a time you choose
- **2,500+ European banks** — Revolut, N26, Monzo, Wise, Millennium BCP, Santander, ING, BNP Paribas, and more across 29 countries
- **Multiple bank accounts** — connect up to 2 bank accounts by default, with the option to add more
- **Read-only, always** — Klartion can never move money or modify your account
- **Pending transaction tracking** — pending transactions are imported and automatically updated to Cleared or Cancelled when they settle
- **Duplicate detection** — Klartion tracks every transaction ID so nothing ever gets imported twice
- **Email notifications** — a summary email on success, an alert if something goes wrong
- **Your data, your machine** — bank data goes directly from Enable Banking to your machine, never our servers
- **Lightweight** — runs as a single Docker container, uses minimal CPU and memory

---

## Requirements

- Docker and Docker Compose
- An [Enable Banking](https://enablebanking.com) account (free)
- A [Notion](https://notion.so) account
- A Klartion licence key — [buy one at klartion.com](https://klartion.com)

---

## Quick start

> **New to self-hosting?** Follow the step-by-step guide at [klartion.com/getting-started](https://klartion.com/getting-started.html) — it walks you through everything from Enable Banking setup to running your first sync.

### 1. Get your licence key

Purchase at [klartion.com](https://klartion.com). Your key is delivered to your email instantly.

### 2. Set up Enable Banking

Enable Banking is the regulated open banking provider that connects Klartion to your bank.

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

### 3. Install Klartion

**On your server**, create the folder and download the compose file:
```bash
mkdir -p ~/klartion/data && cd ~/klartion
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/docker-compose.yml
```

**On your local machine**, upload the private key to your server. The filename matches your Enable Banking Application ID:
```bash
scp ~/Downloads/your-app-id.pem user@your-server:~/klartion/data/your-app-id.pem
```


**Back on your server**, start the container:
```bash
docker compose up -d
```

Open **http://your-server-address:3001** in your browser. The setup wizard will guide you through the rest.

---

## Setup wizard

The browser-based wizard walks you through four steps:

1. **Licence** — enter your key to activate Klartion on this machine
2. **Notion** — duplicate the [ready-made template](https://hilarious-mirror-513.notion.site/4f95e8e7b23183c3be5381bef1d906b2), create a free integration, and paste your credentials
3. **Notifications** — set your email, SMTP credentials, and daily sync time
4. **Bank** — connect your bank via Enable Banking OAuth (one-time, browser-based). You can connect up to 2 bank accounts by default. Each bank's transactions are tagged with the bank name in a "Bank" column in Notion.

Once complete, Klartion runs silently in the background. To add a second bank, go to the **Bank** tab and search for another bank. Need more than 2? You can purchase additional bank account slots from the status page.

---

## Session renewal (every ~180 days)

Enable Banking requires you to re-authorise access roughly every 6 months. If you configured email notifications, you will receive a warning before expiry.

To re-authorise, go to the **Status** page in the Klartion web UI and click **Re-authorise bank**.

---

## How it works
```
Your bank
   ↓  (read-only OAuth, Enable Banking)
Klartion (running on your machine)
   ↓  (Notion API)
Your Notion database
   ↓  (SMTP)
Your inbox  ← daily summary email
```

On each sync run, Klartion:

1. Validates your licence key
2. Fetches transactions since the last sync from Enable Banking
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
| Transaction ID | Text | Enable Banking reference (deduplication key) |
| Bank | Text | Bank name (e.g. Revolut, N26) |

---

## Updating
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
