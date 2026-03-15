content = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Redirecting...</title>
</head>
<body>
  <script>
    window.location.href = "{{ target }}";
  </script>
  <p>Redirecting... <a href="{{ target }}">click here if not redirected</a></p>
</body>
</html>'''
with open("/home/david/klartion/app/web/templates/callback_redirect.html", "w") as f:
    f.write(content)
print("Done")
