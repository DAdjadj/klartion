import os

privacy = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Klartion - Privacy Policy</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Lora:wght@400;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #fff; color: #111; font-family: 'Inter', sans-serif; max-width: 680px; margin: 0 auto; padding: 4rem 2rem; line-height: 1.7; }
    .logo { display: flex; align-items: center; gap: 9px; margin-bottom: 3rem; text-decoration: none; }
    .logo-mark { width: 28px; height: 28px; background: #111; border-radius: 5px; display: flex; align-items: center; justify-content: center; color: #fff; font-family: 'Lora', serif; font-style: italic; font-size: 14px; }
    .logo-name { font-family: 'Lora', serif; font-size: 18px; font-weight: 700; color: #111; }
    h1 { font-family: 'Lora', serif; font-size: 28px; font-weight: 700; margin-bottom: 0.5rem; }
    .date { font-size: 13px; color: #9ca3af; margin-bottom: 2.5rem; }
    h2 { font-family: 'Lora', serif; font-size: 18px; font-weight: 700; margin: 2rem 0 0.75rem; }
    p { margin-bottom: 1rem; color: #374151; }
    a { color: #111; }
    footer { margin-top: 4rem; padding-top: 2rem; border-top: 1px solid #e5e7eb; font-size: 13px; color: #9ca3af; display: flex; gap: 1.5rem; }
    footer a { color: #6b7280; text-decoration: none; }
  </style>
</head>
<body>
  <a class="logo" href="/">
    <div class="logo-mark">K</div>
    <span class="logo-name">Klartion</span>
  </a>
  <h1>Privacy Policy</h1>
  <div class="date">Last updated: March 2026</div>

  <h2>What Klartion is</h2>
  <p>Klartion is a self-hosted application. You run it on your own machine. Your bank transaction data is fetched from Enable Banking and written directly to your Notion database. It never passes through our servers.</p>

  <h2>What we collect</h2>
  <p>When you purchase a licence, Lemon Squeezy collects your name and email address to process the payment and deliver your licence key. We receive your email address solely for support purposes.</p>
  <p>When you activate your licence key, we record that an activation occurred. We do not store any personally identifiable information beyond your email address.</p>

  <h2>What we do not collect</h2>
  <p>We do not collect, store, or have access to your bank transactions, account balances, Notion data, Enable Banking credentials, or SMTP passwords. All of this data stays on your machine.</p>

  <h2>Third-party services</h2>
  <p>Klartion integrates with the following third-party services on your behalf: Enable Banking (open banking API), Notion (database), and your chosen SMTP provider (email notifications). Their respective privacy policies apply to your use of those services.</p>
  <p>Licence validation is handled by Lemon Squeezy. Their privacy policy is available at lemonsqueezy.com.</p>

  <h2>Data retention</h2>
  <p>We retain your email address for as long as your licence is active. You can request deletion at any time by emailing support@david-alves.com.</p>

  <h2>Contact</h2>
  <p>For any privacy-related questions, email <a href="mailto:support@david-alves.com">support@david-alves.com</a>.</p>

  <footer>
    <a href="/">Klartion</a>
    <a href="/privacy">Privacy</a>
    <a href="/terms">Terms</a>
    <a href="mailto:support@david-alves.com">Support</a>
    <a href="https://github.com/DAdjadj/klartion" target="_blank">GitHub</a>
  </footer>
</body>
</html>"""

terms = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Klartion - Terms of Service</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Lora:wght@400;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #fff; color: #111; font-family: 'Inter', sans-serif; max-width: 680px; margin: 0 auto; padding: 4rem 2rem; line-height: 1.7; }
    .logo { display: flex; align-items: center; gap: 9px; margin-bottom: 3rem; text-decoration: none; }
    .logo-mark { width: 28px; height: 28px; background: #111; border-radius: 5px; display: flex; align-items: center; justify-content: center; color: #fff; font-family: 'Lora', serif; font-style: italic; font-size: 14px; }
    .logo-name { font-family: 'Lora', serif; font-size: 18px; font-weight: 700; color: #111; }
    h1 { font-family: 'Lora', serif; font-size: 28px; font-weight: 700; margin-bottom: 0.5rem; }
    .date { font-size: 13px; color: #9ca3af; margin-bottom: 2.5rem; }
    h2 { font-family: 'Lora', serif; font-size: 18px; font-weight: 700; margin: 2rem 0 0.75rem; }
    p { margin-bottom: 1rem; color: #374151; }
    a { color: #111; }
    footer { margin-top: 4rem; padding-top: 2rem; border-top: 1px solid #e5e7eb; font-size: 13px; color: #9ca3af; display: flex; gap: 1.5rem; }
    footer a { color: #6b7280; text-decoration: none; }
  </style>
</head>
<body>
  <a class="logo" href="/">
    <div class="logo-mark">K</div>
    <span class="logo-name">Klartion</span>
  </a>
  <h1>Terms of Service</h1>
  <div class="date">Last updated: March 2026</div>

  <h2>Licence</h2>
  <p>Klartion is licensed under the MIT licence with a Commons Clause. You may use, modify, and self-host Klartion for personal use at no cost. You may not sell, sublicence, or offer Klartion as a competing hosted or managed service.</p>

  <h2>Paid licence key</h2>
  <p>A paid licence key is required to use Klartion. Each licence key may be activated on up to 2 machines. Licence keys are non-transferable and non-refundable after activation.</p>

  <h2>No warranty</h2>
  <p>Klartion is provided as-is, without warranty of any kind. We do not guarantee uninterrupted access to Enable Banking, Notion, or any third-party service. We are not responsible for any data loss, missed syncs, or inaccuracies in transaction data.</p>

  <h2>Limitation of liability</h2>
  <p>To the maximum extent permitted by law, David Alves shall not be liable for any indirect, incidental, or consequential damages arising from your use of Klartion.</p>

  <h2>Changes</h2>
  <p>We may update these terms at any time. Continued use of Klartion after changes constitutes acceptance of the new terms.</p>

  <h2>Contact</h2>
  <p>For any questions, email <a href="mailto:support@david-alves.com">support@david-alves.com</a>.</p>

  <footer>
    <a href="/">Klartion</a>
    <a href="/privacy">Privacy</a>
    <a href="/terms">Terms</a>
    <a href="mailto:support@david-alves.com">Support</a>
    <a href="https://github.com/DAdjadj/klartion" target="_blank">GitHub</a>
  </footer>
</body>
</html>"""

with open("/home/david/klartion-site/privacy.html", "w") as f:
    f.write(privacy)
print("privacy.html done")

with open("/home/david/klartion-site/terms.html", "w") as f:
    f.write(terms)
print("terms.html done")
