"""Layout for Klartion's alert emails, in HTML and plain text.

Ported from the licence server's customer emails (license-api, src/email.js)
so a sync alert looks like every other Klartion email: a Notion page, with
its cover, icon, serif title and page properties in Notion's tag colours.
Problems sit in a Notion callout and balances in a Notion table. Each alert
is described once as content (title, intro, properties, callouts, steps,
button) and rendered twice from it, HTML and plain text, so the two never
drift apart.

Email-safe on purpose: tables, inline styles, PNG and JPEG images only, and
one <style> block that adds dark mode in the mail apps that support it
(Apple Mail, iOS Mail, Outlook for Mac). Images load from the licence
server, the same files the licence emails use.
"""
import html as _html
import re

ASSET_BASE = "https://api.klartion.com/static/email/"
ASSET_VERSION = "1"

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
SERIF = "Lora,Georgia,'Times New Roman',serif"

NAME = "Klartion"
SITE = "klartion.com"
TERMS = "https://klartion.com/terms"
PRIVACY = "https://klartion.com/privacy"

ACCENT = "#2d7a4f"
ON_ACCENT = "#ffffff"
LINK = "#2d7a4f"
INK = "#1a1a1a"
TEXT = "#4a4a47"
MUTED = "#75746f"
LINE = "#e9e8e3"
SOFT = "#f7f6f3"
PAGE = "#fafaf8"
CARD = "#ffffff"
DARK = {"page": "#191918", "card": "#202020", "soft": "#262624", "line": "#34332f", "ink": "#f1f0ec",
        "text": "#cac8c1", "muted": "#8f8d86", "link": "#74c495", "red": "#3a2523"}

# Notion's tag colours (background, text) and its red callout.
TAGS = {"green": ("#dbeddb", "#1c3829"), "orange": ("#fadec9", "#49290e"), "blue": ("#d3e5ef", "#183347"),
        "gray": ("#e3e2e0", "#32302c"), "red": ("#ffe2dd", "#5d1715")}
CALLOUT_RED = "#fdebec"


def asset(name: str) -> str:
    return f"{ASSET_BASE}{name}?v={ASSET_VERSION}"


# ── Text helpers ─────────────────────────────────────────────────

def esc(s) -> str:
    return _html.escape("" if s is None else str(s), quote=True).replace("&#x27;", "'")


# Content strings may carry [links](https://...). HTML gets an <a>, text gets "words (address)".
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def md(s, link=LINK, cls="lk") -> str:
    return _LINK.sub(lambda m: f'<a class="{cls}" href="{m.group(2)}" style="color:{link};text-decoration:underline">{m.group(1)}</a>', esc(s))


def plain(s) -> str:
    def repl(m):
        txt, href = m.group(1), m.group(2)
        if href == f"mailto:{txt}":
            return txt
        if txt == re.sub(r"^https?://", "", href):
            return href
        return f"{txt} ({href})"
    return _LINK.sub(repl, "" if s is None else str(s))


# ── Document ─────────────────────────────────────────────────────

def _dark_css() -> str:
    d = DARK
    return f"""<style>
:root{{color-scheme:light dark;supported-color-schemes:light dark}}
@media (prefers-color-scheme:dark){{
.pg{{background:{d['page']}!important}}
.cd{{background:{d['card']}!important;border-color:{d['line']}!important}}
.sf{{background:{d['soft']}!important;border-color:{d['line']}!important}}
.ln{{border-color:{d['line']}!important}}
.ik{{color:{d['ink']}!important}}
.tx{{color:{d['text']}!important}}
.mu{{color:{d['muted']}!important}}
.lk{{color:{d['link']}!important}}
.icb{{border-color:{d['card']}!important}}
.co{{background:{d['red']}!important}}
}}
@media (max-width:480px){{.px{{padding-left:22px!important;padding-right:22px!important}}.h1{{font-size:25px!important}}.sm-hide{{display:none!important}}}}
</style>"""


def _page(e: dict, inner: str) -> str:
    hidden = "&#8204;&nbsp;" * 70
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark"><meta name="x-apple-disable-message-reformatting">
<title>{esc(e.get('subject') or e.get('title'))}</title>
<link href="https://fonts.googleapis.com/css2?family=Lora:wght@500;600&display=swap" rel="stylesheet">
{_dark_css()}</head>
<body class="pg" style="margin:0;padding:0;background:{PAGE};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all">{esc(e.get('preheader') or '')}{hidden}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="pg" style="background:{PAGE}"><tr><td align="center" style="padding:36px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
{inner}
</table></td></tr></table>
</body></html>"""


# ── Shared parts ─────────────────────────────────────────────────

def _para(s, size=16, mb=18, color=TEXT, cls="tx") -> str:
    return f'<p class="{cls}" style="margin:0 0 {mb}px;font:{size}px/1.6 {SANS};color:{color}">{md(s)}</p>'


def _heading(s) -> str:
    return f'<div class="ik" style="font:600 15px/1.4 {SERIF};color:{INK};margin:0 0 12px">{esc(s)}</div>'


def _tag(value, colour) -> str:
    bg, fg = TAGS[colour]
    return f'<span style="display:inline-block;background:{bg};color:{fg};font:13px/1 {SANS};padding:4px 7px;border-radius:4px">{esc(value)}</span>'


def notion_props(facts: list) -> str:
    """Notion page properties: a label column and values, some of them coloured tags."""
    rows = []
    for f in facts:
        v = _tag(f["value"], f["tag"]) if f.get("tag") else esc(f["value"])
        font = f"500 14px/1.45 {MONO};word-break:break-all" if f.get("mono") else f"14px/1.45 {SANS}"
        rows.append(f"""<tr><td class="mu" width="128" style="padding:6px 12px 6px 0;font:14px/1.4 {SANS};color:{MUTED};vertical-align:top;white-space:nowrap">{esc(f['label'])}</td>
<td class="ik" style="padding:6px 0;font:{font};color:{INK}">{v}</td></tr>""")
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px">{"".join(rows)}</table>'
            f'<div class="ln" style="border-top:1px solid {LINE};margin:0 0 24px"></div>')


def callout(items: list) -> str:
    """A red Notion callout: one line per problem, the account in bold."""
    if not items:
        return ""
    lines = []
    for i, it in enumerate(items):
        name = f'<span style="font-weight:600">{esc(it["name"])}</span><br>' if it.get("name") else ""
        lines.append(f'<div class="ik" style="font:14.5px/1.55 {SANS};color:{INK};{"margin-top:12px;" if i else ""}word-break:break-word">{name}{esc(it["detail"])}</div>')
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px"><tr>
<td class="co" style="background:{CALLOUT_RED};border-radius:6px;padding:14px 16px">{"".join(lines)}</td></tr></table>"""


def simple_table(title: str, rows: list, column: str = "") -> str:
    """A Notion simple table with a header row: label left, figure right."""
    if not rows:
        return ""
    cell = f"border:1px solid {LINE};padding:8px 10px;font:14px/1.4 {SANS};color:{INK}"
    head = (f'<tr><td class="ln sf mu" style="{cell};background:{SOFT};color:{MUTED}">Account</td>'
            f'<td class="ln sf mu" align="right" style="{cell};background:{SOFT};color:{MUTED}">{esc(column or title)}</td></tr>')
    body = "".join(f'<tr><td class="ln ik" style="{cell}">{esc(label)}</td>'
                   f'<td class="ln ik" align="right" style="{cell};white-space:nowrap;font-variant-numeric:tabular-nums">{esc(value)}</td></tr>'
                   for label, value in rows)
    return (_heading(title) +
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 28px">{head}{body}</table>')


def _steps(e: dict) -> str:
    if not e.get("steps"):
        return ""
    rows = "".join(f"""<tr>
<td width="26" style="vertical-align:top;padding:0 0 14px"><div class="ik" style="font:15px/1.55 {SANS};color:{INK}">{i + 1}.</div></td>
<td class="tx" style="vertical-align:top;padding:0 0 14px;font:15px/1.55 {SANS};color:{TEXT}">{md(s)}</td></tr>""" for i, s in enumerate(e["steps"]))
    title = _heading(e["steps_title"]) if e.get("steps_title") else ""
    return f'{title}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px">{rows}</table>'


def _button(c: dict, secondary=False, last=True) -> str:
    r = 6
    style = f"border:1.5px solid {LINE};border-radius:{r}px" if secondary else f"background:{ACCENT};border-radius:{r}px"
    mb = 10 if c.get("sub") else (30 if last else 12)
    sub = (f'<div class="mu" style="font:13.5px/1.55 {SANS};color:{MUTED};margin:0 0 {30 if last else 16}px">{md(c["sub"])}</div>'
           if c.get("sub") else "")
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 {mb}px"><tr>
<td class="{'ln' if secondary else 'btn'}" style="{style}" align="center"><a href="{esc(c['href'])}" style="display:inline-block;padding:13px 24px;font:600 15px/1.2 {SANS};color:{INK if secondary else ON_ACCENT};text-decoration:none;border-radius:{r}px"><span class="{'ik' if secondary else ''}">{esc(c['label'])}</span></a></td>
</tr></table>{sub}"""


def _ctas(e: dict) -> list:
    return [c for c in (e.get("ctas") or ([e["cta"]] if e.get("cta") else [])) if c and c.get("href")]


def _buttons(e: dict) -> str:
    cs = _ctas(e)
    if len(cs) == 2 and not any(c.get("sub") for c in cs):
        # Two buttons side by side, the second one quiet.
        a, b = _button(cs[0]), _button(cs[1], secondary=True)
        return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="vertical-align:top;padding-right:10px">{a}</td>'
                f'<td style="vertical-align:top">{b}</td></tr></table>')
    return "".join(_button(c, secondary=i > 0, last=i == len(cs) - 1) for i, c in enumerate(cs))


def _footer(e: dict) -> str:
    a = lambda href, txt: f'<a class="mu" href="{esc(href)}" style="color:{MUTED};text-decoration:underline">{txt}</a>'
    links = [NAME, a(f"https://{SITE}", SITE), a(TERMS, "Terms"), a(PRIVACY, "Privacy")]
    fine = "".join(f'<p class="mu" style="margin:0 0 10px;font:13px/1.6 {SANS};color:{MUTED}">{md(s)}</p>' for s in e.get("fine") or [])
    why = (f'<p class="mu" style="margin:0 0 6px;font:12.5px/1.6 {SANS};color:{MUTED}">{md(e["reason"], MUTED, "mu")}</p>'
           if e.get("reason") else "")
    return f"""<tr><td class="px" style="padding:24px 8px 0">
{fine}
<p class="mu" style="margin:{18 if fine else 0}px 0 6px;font:12.5px/1.6 {SANS};color:{MUTED}">{" &nbsp;·&nbsp; ".join(links)}</p>
{why}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px"><tr>
<td style="vertical-align:middle"><a href="https://david-alves.com"><img src="{asset('da-mark.png')}" width="24" height="16" alt="DA" style="display:block;border:0"></a></td>
<td class="mu" style="vertical-align:middle;padding-left:7px;font:12px/1 {SANS};color:{MUTED}">Made by David Alves</td></tr></table>
</td></tr>"""


def _notion(e: dict) -> str:
    """The card: a Notion page with cover, icon, breadcrumb, title and properties, then the body."""
    crumb = f" &nbsp;/&nbsp; {esc(e['chip'])}" if e.get("chip") else ""
    props = notion_props(e["facts"]) if e.get("facts") else f'<div class="ln" style="border-top:1px solid {LINE};margin:0 0 24px"></div>'
    head = f"""<tr><td style="padding:0;border-radius:15px 15px 0 0"><img src="{asset('kl-cover.jpg')}" width="560" alt="" style="display:block;width:100%;height:auto;border:0;border-radius:15px 15px 0 0"></td></tr>
<tr><td class="px" style="padding:0 40px"><img src="{asset('klartion.png')}" width="58" height="58" alt="" class="icb" style="display:block;margin-top:-29px;border:3px solid {CARD};border-radius:16px"></td></tr>
<tr><td class="px" style="padding:16px 40px 0">
<div class="mu" style="font:13px/1 {SANS};color:{MUTED};margin:0 0 10px">{NAME}{crumb}</div>
<h1 class="h1 ik" style="margin:0;font:600 30px/1.15 {SERIF};letter-spacing:-0.2px;color:{INK}">{esc(e['title'])}</h1>
<div style="height:18px"></div>
{props}
</td></tr>"""
    body = "".join(_para(s) for s in e.get("intro") or [])
    body += callout(e.get("problems") or [])
    if e.get("table"):
        body += simple_table(e["table"]["title"], e["table"]["rows"], e["table"].get("column", ""))
    body += "".join(_para(s, size=15, color=MUTED, cls="mu") for s in e.get("notes") or [])
    body += _steps(e) + _buttons(e)
    body += "".join(_para(s) for s in e.get("after") or [])
    return f"""<tr><td class="cd" style="background:{CARD};border:1px solid {LINE};border-radius:16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{head}
<tr><td class="px" style="padding:0 40px 8px">{body}</td></tr></table></td></tr>"""


def render_html(e: dict) -> str:
    return _page(e, _notion(e) + _footer(e))


def render_text(e: dict) -> str:
    out = [e["title"]]
    out += [plain(s) for s in e.get("intro") or []]
    facts = [f for f in e.get("facts") or [] if not f.get("text_skip")]
    if facts:
        out.append("\n".join(f"{f['label']}: {f['value']}" for f in facts))
    problems = e.get("problems") or []
    if problems:
        out.append("What went wrong:\n" + "\n".join(
            f"- {p['name']}: {p['detail']}" if p.get("name") else f"- {p['detail']}" for p in problems))
    if e.get("table"):
        out.append(f"{e['table']['title']}:\n" + "\n".join(f"- {label}: {value}" for label, value in e["table"]["rows"]))
    out += [plain(s) for s in e.get("notes") or []]
    if e.get("steps"):
        head = f"{e['steps_title']}:\n" if e.get("steps_title") else ""
        out.append(head + "\n".join(f"{i + 1}. {plain(s)}" for i, s in enumerate(e["steps"])))
    for c in _ctas(e):
        out.append(f"{c['label']}: {c['href']}" + (f"\n{plain(c['sub'])}" if c.get("sub") else ""))
    out += [plain(s) for s in e.get("after") or []]
    out += [plain(s) for s in e.get("fine") or []]
    out.append(f"--\n{NAME}: https://{SITE}" + (f"\n{plain(e['reason'])}" if e.get("reason") else ""))
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(out))
