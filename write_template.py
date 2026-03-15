with open("/home/david/klartion/app/web/templates/setup_notifications.html", "w") as f:
    f.write("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Klartion - Notifications</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="wrap">
    <a class="logo" href="/">
      <div class="logo-mark">K</div>
      <span class="logo-name">Klartion</span>
    </a>
    <div class="steps">
      <div class="step done"></div>
      <div class="step done"></div>
      <div class="step active"></div>
      <div class="step"></div>
    </div>
    <div class="card">
      <div class="card-title">Notifications</div>
      <div class="card-sub">Step 3 of 4 - how should Klartion notify you?</div>
      {% if error %}
        <div class="alert alert-error">{{ error }}</div>
      {% endif %}
      <form method="POST">
        <label for="klartion_url" style="margin-top:0;">Your Klartion URL</label>
        <input type="url" id="klartion_url" name="klartion_url" value="{{ klartion_url }}" placeholder="http://192.168.1.50:3001" autocomplete="off" required>
        <div style="font-size:11px; color:var(--hint); margin-top:0.4rem;">The address you use to open Klartion in your browser. Used to redirect you back after connecting your bank.</div>
        <label for="notify_email">Notification email</label>
        <input type="email" id="notify_email" name="notify_email" value="{{ notify_email }}" placeholder="you@example.com" required>
        <label for="smtp_user">SMTP username</label>
        <input type="text" id="smtp_user" name="smtp_user" value="{{ smtp_user }}" placeholder="you@icloud.com" autocomplete="off" required>
        <label for="smtp_password">SMTP password</label>
        <input type="password" id="smtp_password" name="smtp_password" value="{{ smtp_password }}" placeholder="App-specific password" autocomplete="off" required>
        <div style="font-size:11px; color:var(--hint); margin-top:0.4rem;">For iCloud: use an app-specific password from <a href="https://appleid.apple.com" target="_blank" style="color:var(--muted);">appleid.apple.com</a></div>
        <label for="sync_time">Daily sync time</label>
        <input type="time" id="sync_time" name="sync_time" value="{{ sync_time }}" required>
        <button class="btn btn-primary" type="submit">Continue</button>
      </form>
    </div>
    <nav class="footer-nav">
      <a href="/setup">Licence</a>
      <a href="/setup/notion">Notion</a>
      <a href="/setup/notifications" class="active">Notifications</a>
      <a href="/connect">Bank</a>
    </nav>
  </div>
</body>
</html>""")
print("Done")
