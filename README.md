# Klartion

Sync your EU bank transactions into Notion automatically, every day.

Klartion connects to your bank via [Enable Banking](https://enablebanking.com) (open banking, read-only) and writes transactions into a Notion database on a daily schedule. It runs locally on your machine inside Docker — your financial data never touches any third-party server.

**Supported banks:** Any of the 2,500+ banks across 29 European countries supported by Enable Banking, including Revolut, N26, Monzo, Wise, Millennium BCP, Santander, ING, BNP Paribas, and more.

---

## Requirements

- Docker and Docker Compose
- An [Enable Banking](https://enablebanking.com) account and application (free, restricted mode)
- A [Notion](https://notion.so) integration and database
- A Klartion licence key (purchased at [klartion.com](https://klartion.com))

---

## Quick start

### 1. Purchase a licence key

Go to [klartion.com](https://klartion.com) and purchase a licence key. It will be delivered to your email immediately.

### 2. Set up Enable Banking

1. Sign up at [enablebanking.com](https://enablebanking.com)
2. Go to **API applications** and create a new application called `Klartion`
3. Set the redirect URL to `https://klartion.com/callback`
4. Under **Keys**, select **Generate in the browser** and download your private key (`.pem` file)
5. Note your **App ID** from the application page
6. Click **Link accounts** and link your bank account (this activates restricted mode — free forever)

### 3. Set up your Notion database

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and create a new integration called `Klartion`
2. Copy the **Internal Integration Token** (starts with `secret_`)
3. Create a new full-page database in Notion with these exact columns:

| Column | Type |
|---|---|
| Merchant | Title |
| Date | Date |
| Amount | Number |
| Currency | Text |
| Category | Select |
| Reference | Text |
| Direction | Select |
| Status | Select |
| Transaction ID | Text |

4. Open the database, click `...` → **Connections** → connect `Klartion`
5. Copy the database ID from the URL (the string after the last `/` and before `?`)

### 4. Install Klartion

```bash
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/.env.example
cp .env.example .env
```

### 5. Configure your .env

Edit `.env` and fill in all values:

```env
LICENCE_KEY=your-licence-key
EB_APP_ID=your-enable-banking-app-id
EB_PRIVATE_KEY_PATH=/app/data/eb_private.key
NOTION_API_KEY=secret_xxxxxxxxxxxx
NOTION_DATABASE_ID=your-database-id
SMTP_HOST=smtp.mail.me.com
SMTP_PORT=587
SMTP_USER=your@icloud.com
SMTP_PASSWORD=your-app-specific-password
NOTIFY_EMAIL=your@email.com
SYNC_TIME=08:00
SECRET_KEY=any-random-string
KLARTION_URL=http://your-server-address:3001
DB_PATH=/app/data/klartion.db
```

**Note on `KLARTION_URL`:** Set this to the address you use to access Klartion in your browser. Examples:
- `http://192.168.1.50:3001` — if accessing by IP
- `http://my-server:3001` — if your server has a hostname
- `http://localhost:3001` — if running on the same machine you browse from

Copy your Enable Banking private key into the `data/` folder:

```bash
mkdir -p data
cp /path/to/your/private.key data/eb_private.key
```

Generate a random SECRET_KEY:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 6. Run

```bash
docker compose up -d
```

Open `http://your-server-address:3001` in your browser to complete setup and connect your bank.

**Note:** If you use iCloud Private Relay, your browser may ask to show your IP address when accessing Klartion. This is expected — click **Continue**.

---

## How it works

1. On startup, Klartion validates your licence key and starts a local web server
2. You connect your bank once via the web UI — a standard OAuth flow in your browser
3. Every day at your configured sync time, Klartion fetches new transactions from Enable Banking
4. New transactions are written to Notion. Duplicate detection prevents re-importing
5. Pending transactions are tracked and updated to Cleared or Cancelled when they settle
6. You receive an email on success or failure

---

## Notion database columns

| Column | Description |
|---|---|
| Merchant | Payee or sender name |
| Date | Booking date |
| Amount | Positive for incoming, negative for outgoing |
| Currency | ISO currency code (e.g. EUR) |
| Category | Bank transaction code (e.g. CARD_PAYMENT, TRANSFER) |
| Reference | Remittance information |
| Direction | `in` or `out` |
| Status | `Cleared`, `Pending`, or `Cancelled` |
| Transaction ID | Enable Banking reference (used for deduplication) |

---

## Updating

```bash
docker compose pull
docker compose up -d --build
```

---

## Licence

MIT + Commons Clause. Free to self-host for personal use. You may not resell or offer this as a competing service. See [LICENSE](LICENSE).

Built by [David Alves](https://github.com/DAdjadj).
