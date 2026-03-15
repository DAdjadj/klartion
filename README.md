# Klartion

Sync your EU bank transactions into Notion automatically, every day.

Klartion connects to your bank via [Enable Banking](https://enablebanking.com) (open banking, read-only) and writes transactions into a Notion database on a daily schedule. It runs locally on your machine inside Docker — your financial data never touches any third-party server.

**Supported banks:** Any of the 2,500+ banks across 29 European countries supported by Enable Banking, including Revolut, N26, Monzo, Wise, Millennium BCP, Santander, ING, and more.

---

## Requirements

- Docker and Docker Compose
- An [Enable Banking](https://enablebanking.com) account and application (free, restricted mode)
- A [Notion](https://notion.so) integration and database
- A Klartion licence key (purchased at [klartion.com](https://klartion.com))

---

## Quick start

### 1. Get your Enable Banking credentials

1. Sign up at [enablebanking.com](https://enablebanking.com)
2. Create a new application in the Control Panel
3. Note your **App ID**
4. Generate and download your **RSA private key** (Settings → Keys)
5. In the application settings, add `http://localhost:3000/callback` as a redirect URI
6. Link your own account to activate restricted mode (free)

### 2. Set up your Notion database

1. Duplicate the [Klartion Notion template](#) into your workspace
2. Create a new Notion integration at [notion.so/my-integrations](https://www.notion.so/my-integrations)
3. Share the duplicated database with your integration
4. Copy the **integration token** (starts with `secret_`) and the **database ID** from the database URL

### 3. Install Klartion

```bash
# Download docker-compose.yml
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/docker-compose.yml

# Copy the example env file
curl -O https://raw.githubusercontent.com/DAdjadj/klartion/main/.env.example
cp .env.example .env
```

### 4. Configure your .env

Edit `.env` and fill in all values:

```env
LICENCE_KEY=your-licence-key-here
EB_APP_ID=your-enable-banking-app-id
EB_PRIVATE_KEY_PATH=/app/data/eb_private.key
NOTION_API_KEY=secret_xxxxxxxxxxxx
NOTION_DATABASE_ID=your-database-id
SMTP_USER=your@icloud.com
SMTP_PASSWORD=your-app-specific-password
NOTIFY_EMAIL=your@email.com
SYNC_TIME=08:00
SECRET_KEY=any-random-string-here
```

Copy your Enable Banking private key file into the `data/` folder:

```bash
mkdir -p data
cp /path/to/your/private.key data/eb_private.key
```

### 5. Run

```bash
docker compose up -d
```

Open [http://localhost:3000](http://localhost:3000) in your browser to complete setup and connect your bank.

---

## Notion database columns

Klartion expects the following properties in your Notion database:

| Column | Type |
|---|---|
| Merchant | Title |
| Date | Date |
| Amount | Number |
| Currency | Text |
| Category | Select |
| Reference | Text |
| Direction | Select (in / out) |
| Status | Select (Cleared / Pending / Cancelled) |
| Transaction ID | Text |

---

## How it works

1. On startup, Klartion validates your licence key and starts a local web server at port 3000
2. You connect your bank once via the web UI — a standard OAuth flow handled entirely in your browser
3. Every day at your configured sync time, Klartion fetches the last 48 hours of transactions from Enable Banking
4. New transactions are written to Notion. Duplicate detection is handled via Transaction ID
5. Pending transactions are tracked and updated to Cleared or Cancelled when they settle
6. You receive an email on success or failure

---

## Updating

```bash
docker compose pull
docker compose up -d
```

---

## Licence

MIT + Commons Clause. Free to self-host for personal use. You may not resell or offer this as a competing service. See [LICENSE](LICENSE).

Built by [David Alves](https://github.com/DAdjadj).
