# Klartion

**Your bank transactions, inside Notion. Automatically.**

Klartion connects to your EU bank via open banking and writes your transactions into a Notion database every day. It runs on your own machine — your financial data never touches any third-party server.

→ **[klartion.com](https://klartion.com)** · [Buy a licence](https://david-alves.lemonsqueezy.com/checkout/buy/f24a36ac-3c66-4aaf-b272-9cce226a0ebf)

---

## What you get

- **Daily automatic sync** — transactions land in Notion once a day, at a time you choose
- **2,500+ European banks** — Revolut, N26, Monzo, Wise, Millennium BCP, Santander, ING, BNP Paribas, and more across 29 countries
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

### 1. Get your licence key

Purchase at [klartion.com](https://klartion.com). Your key is delivered to your email instantly.

### 2. Set up Enable Banking

Enable Banking is the regulated open banking provider that connects Klartion to your bank.

1. Sign up at [enablebanking.com](https://enablebanking.com)
2. Go to **API applications** and create a new application — name it `Klartion`
3. Set the redirect URL to `https://klartion.com/callback`
4. Under **Keys**, select **Generate in the browser** and download your private key (`.pem` file)
5. Note your **App ID** from the application dashboard
6. Click **Link accounts** and link your bank — this activates restricted mode (free, no expiry)

### 3. Install Klartion

Create a folder and download the compose file:

```bash
mkdir klartion && cd klartion
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/docker-compose.yml
mkdir -p data
cp /path/to/your/downloaded.pem data/eb_private.key
```

Start the container:

```bash
docker compose up -d
```

Open **http://your-server-address:3001** in your browser. The setup wizard will guide you through the rest — licence key, Notion connection, notifications, and bank OAuth. No manual config file editing required.

> **Tip:** If you use iCloud Private Relay, your browser may ask to reveal your IP address when accessing a local address. This is expected — click **Continue**.

---

## Setup wizard

The browser-based wizard walks you through four steps:

1. **Licence** — enter your key to activate Klartion on this machine
2. **Notion** — duplicate the [ready-made template](https://hilarious-mirror-513.notion.site/4f95e8e7b23183c3be5381bef1d906b2), create a free integration, and paste your credentials
3. **Notifications** — set your email, SMTP credentials, and daily sync time
4. **Bank** — connect your bank via Enable Banking OAuth (one-time, browser-based)

Once complete, Klartion runs silently in the background.

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

---

## Updating

```bash
docker compose pull && docker compose up -d --build
```

---

## Licence deactivation

Each licence key supports up to 2 machine activations. To move Klartion to a new machine, go to the **Status** page in the web UI and click **Deactivate licence** before reinstalling.

---

## Licence

MIT + Commons Clause. Free to self-host for personal use. You may not sell, sublicence, or offer Klartion as a competing service. See [LICENSE](LICENSE).

Built by [David Alves](https://david-alves.com).
