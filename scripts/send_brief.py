#!/usr/bin/env python3
"""Email The Morning Skate's morning brief.

  python scripts/send_brief.py                 # send (needs SMTP env vars)
  python scripts/send_brief.py --preview out.html   # write the HTML, don't send

Reads data.json (the edition that just passed validation) and sends one
email: the morning brief, last night's scores with top performers, today's
games with TV and game-day notes, and the top story per team.

SMTP settings come from environment variables (GitHub secrets in CI):
  BRIEF_SMTP_USER   sending account, e.g. a Gmail address
  BRIEF_SMTP_PASS   its app password (Google Account > Security > App passwords)
  BRIEF_TO          recipient(s), comma-separated
  BRIEF_SMTP_HOST   default smtp.gmail.com     BRIEF_SMTP_PORT  default 465
Missing settings -> prints a note and exits 0 (never fails the build).
"""
import html
import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SITE = "https://themorningskate.to"
INK, CREAM, MUTED, RULE, WIN, LOSS = "#1C1917", "#FAF7F2", "#78716C", "#E7E5E4", "#2E7D4F", "#B3392E"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, 'Helvetica Neue', Arial, sans-serif"
TEAM_ORDER = ("leafs", "jays", "raptors", "commanders")


def esc(s):
    return html.escape(str(s or ""), quote=True)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from game_details import stars_text, preview_text  # noqa: E402

MONO = "Menlo, Consolas, 'Courier New', monospace"
FAINT = "#A8A29E"
TEAM_HEX = {"leafs": "#00205B", "jays": "#134A8E", "raptors": "#CE1141", "commanders": "#773141"}
TAG_STYLE = {"win": ("#F0FDF4", "#15803D"), "loss": ("#FEF2F2", "#B91C1C"),
             "save": ("#EFF6FF", "#1D4ED8"), "": ("#F5F5F4", "#44403C")}
STATUS_STYLE = {"out": ("#FEF2F2", "#B91C1C"), "doubtful": ("#FFF7ED", "#C2410C"),
                "questionable": ("#FEFCE8", "#A16207"), "dtd": ("#F5F5F4", "#44403C")}
CHIP_STYLE = {"confirmed": ("#F0FDF4", "#15803D"), "likely": ("#FEFCE8", "#A16207"),
              "projected": ("#F5F5F4", "#44403C")}


def chip(text, bg, fg, mono=False, minw=0):
    w = f"min-width:{minw}px;" if minw else ""
    return (f'<span style="display:inline-block;{w}text-align:center;font-family:{MONO if mono else SANS};'
            f'font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;padding:3px 6px;'
            f'border-radius:4px;background:{bg};color:{fg};">{esc(text)}</span>')


def small_label(text):
    return (f'<span style="font-family:{SANS};font-size:10px;font-weight:700;letter-spacing:1.4px;'
            f'text-transform:uppercase;color:{MUTED};">{esc(text)}</span>')


def email_stars(stars):
    """Top performers: colored tag | name | stat line."""
    if not stars:
        return ""
    rows = []
    for s in stars:
        if isinstance(s, str):
            rows.append(f'<tr><td colspan="3" style="font-family:{SANS};font-size:13px;color:{MUTED};'
                        f'padding:3px 0;">{esc(s)}</td></tr>')
            continue
        bg, fg = TAG_STYLE.get(s.get("tone") or "", TAG_STYLE[""])
        rows.append(f'<tr><td style="width:46px;padding:3px 0;vertical-align:middle;">'
                    f'{chip(s.get("tag"), bg, fg, mono=True, minw=32)}</td>'
                    f'<td style="font-family:{SANS};font-size:14px;font-weight:600;color:{INK};padding:3px 8px;">'
                    f'{esc(s.get("name"))}</td>'
                    f'<td style="font-family:{MONO};font-size:12px;color:{MUTED};padding:3px 0;text-align:right;'
                    f'white-space:nowrap;">{esc(s.get("stat")).replace(", ", " &middot; ")}</td></tr>')
    return (f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid {RULE};">{small_label("Top performers")}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;">'
            + "".join(rows) + '</table>')


def email_preview(p, team):
    """Game-day preview: pitcher/goalie duel + color-coded injury report."""
    if not p:
        return ""
    if isinstance(p, list):  # older editions stored plain strings
        return "".join(f'<div style="font-family:{SANS};font-size:13px;color:{MUTED};padding-top:3px;">'
                       f'{esc(n)}</div>' for n in p)
    out = ""
    d = p.get("duel")
    if d:
        tc = TEAM_HEX.get(team, INK)
        st = d.get("status")
        chip_html = chip(st, *CHIP_STYLE.get(st, CHIP_STYLE["projected"])) if st else ""

        def side(s, align, color):
            s = s or {}
            return (f'<td style="width:45%;text-align:{align};vertical-align:middle;">'
                    f'<div style="font-family:{SANS};font-size:15px;font-weight:700;color:{color};">'
                    f'{esc(s.get("name") or "TBD")}</div>'
                    + (f'<div style="font-family:{MONO};font-size:11px;color:{MUTED};padding-top:2px;">'
                       f'{esc(s["sub"])}</div>' if s.get("sub") else "") + '</td>')
        out += (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">'
                f'<tr><td>{small_label(d.get("label") or "Matchup")}</td>'
                f'<td style="text-align:right;">{chip_html}</td></tr></table>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;'
                f'background:{CREAM};border:1px solid {RULE};border-left:3px solid {tc};border-radius:8px;">'
                f'<tr><td style="padding:10px 12px;"><table role="presentation" width="100%" cellpadding="0" '
                f'cellspacing="0"><tr>' + side(d.get("ours"), "left", tc)
                + f'<td style="width:10%;text-align:center;font-family:{SERIF};font-style:italic;font-size:14px;'
                  f'color:{FAINT};">vs</td>'
                + side(d.get("theirs"), "right", INK) + '</tr></table></td></tr></table>')
    groups = p.get("injuries") or []
    if groups:
        rows = []
        for g in groups:
            bg, fg = STATUS_STYLE.get(g.get("tone"), STATUS_STYLE["dtd"])
            names = " &middot; ".join(
                esc(pl.get("name")) + (f' <span style="color:{MUTED};font-size:12px;">{esc(pl["note"])}</span>'
                                       if pl.get("note") else "")
                for pl in g.get("players") or [])
            if g.get("more"):
                names += f' <span style="color:{MUTED};font-size:12px;">+{esc(g["more"])} more</span>'
            rows.append(f'<tr><td style="width:98px;vertical-align:top;padding:4px 0;">'
                        f'{chip(g.get("status"), bg, fg, minw=82)}</td>'
                        f'<td style="font-family:{SANS};font-size:13px;color:{INK};line-height:1.55;'
                        f'padding:4px 0 4px 8px;">{names}</td></tr>')
        out += (f'<div style="margin-top:12px;">{small_label("Injury report")}</div>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;">'
                + "".join(rows) + '</table>')
    return out


def label(text):
    return (f'<tr><td style="padding:26px 0 8px;font-family:{SANS};font-size:11px;font-weight:700;'
            f'letter-spacing:2px;text-transform:uppercase;color:{MUTED};border-bottom:1px solid {RULE};">'
            f'{esc(text)}</td></tr>')


def build(data):
    meta = data.get("meta") or {}
    date = meta.get("date_display", "")
    brief = data.get("morning_brief") or {}
    art = brief.get("article") or {}
    teams = data.get("teams") or {}
    rows, text = [], [f"THE MORNING SKATE - {date}", ""]

    # --- The brief -------------------------------------------------------
    if art.get("paragraphs"):
        title = art.get("title") or "The Morning Brief"
        rows.append(f'<tr><td style="padding:22px 0 6px;font-family:{SERIF};font-size:24px;font-weight:700;'
                    f'line-height:1.25;color:{INK};">{esc(title)}</td></tr>')
        text += [title.upper(), ""]
        for p in art["paragraphs"]:
            rows.append(f'<tr><td style="padding:8px 0;font-family:{SERIF};font-size:17px;line-height:1.6;'
                        f'color:{INK};">{esc(p)}</td></tr>')
            text += [p, ""]
    else:
        for t in brief.get("teams") or []:
            lines = [ln.get("text", "") for ln in t.get("lines") or []]
            if not lines:
                continue
            rows.append(label(f"{t.get('name', '')} - {t.get('league', '')}"))
            for ln in lines:
                rows.append(f'<tr><td style="padding:6px 0;font-family:{SERIF};font-size:16px;color:{INK};">'
                            f'{esc(ln)}</td></tr>')
            text += [t.get("name", "").upper()] + lines + [""]

    # --- Last night's scores ---------------------------------------------
    sb = data.get("scoreboard") or []
    if sb:
        rows.append(label("Scores"))
        text += ["SCORES"]
        for g in sb:
            won = g.get("result") == "W"
            kind = "Preseason" if g.get("preseason") else "Final"
            head = (f'{esc(g.get("name"))} {esc(g.get("team_score"))}, '
                    f'{esc(g.get("opp_name"))} {esc(g.get("opp_score"))}')
            tag = (f'<span style="color:{WIN if won else LOSS};font-weight:700;">'
                   f'{"W" if won else ("T" if g.get("result") == "T" else "L")}</span>')
            rows.append(
                f'<tr><td style="padding:12px 0;border-bottom:1px solid {RULE};">'
                f'<div style="font-family:{SANS};font-size:11px;color:{MUTED};letter-spacing:1px;">'
                f'{esc(g.get("league"))} {kind.upper()} &middot; {esc(g.get("date"))}</div>'
                f'<div style="font-family:{SERIF};font-size:18px;color:{INK};padding-top:2px;">{tag} {head}</div>'
                + email_stars(g.get("stars")) + '</td></tr>')
            stars = stars_text(g.get("stars"))
            text.append(f"{'W' if won else 'L'} {g.get('name')} {g.get('team_score')}, "
                        f"{g.get('opp_name')} {g.get('opp_score')} ({kind}, {g.get('date')})")
            text += [f"  {s_}" for s_ in stars]
        text.append("")

    # --- Today's games ---------------------------------------------------
    games = [s for s in data.get("today_slate") or [] if not s.get("off")]
    if games:
        rows.append(label("Today"))
        text += ["TODAY"]
        for s in games:
            when = s.get("detail", "") + (f" on {s['channel']}" if s.get("channel") else "")
            rows.append(f'<tr><td style="padding:12px 0;border-bottom:1px solid {RULE};">'
                        f'<div style="font-family:{SERIF};font-size:18px;color:{INK};">{esc(s.get("matchup"))}</div>'
                        f'<div style="font-family:{SANS};font-size:14px;color:{INK};padding-top:2px;">{esc(when)}</div>'
                        f'{email_preview(s.get("preview"), s.get("team"))}</td></tr>')
            text += [f"{s.get('matchup')} - {when}"] + [f"  {n}" for n in preview_text(s.get("preview"))]
        text.append("")

    # --- Top story per team ----------------------------------------------
    stories = []
    for tk in TEAM_ORDER:
        for a in (teams.get(tk) or {}).get("the_latest") or []:
            h = a.get("headline", "")
            if h and not h.lower().startswith(((teams[tk].get("full_name") or "").split()[-1].lower() + " ",)):
                stories.append((tk, a))
                break
    if stories:
        rows.append(label("Top stories"))
        text += ["TOP STORIES"]
        for tk, a in stories:
            rows.append(f'<tr><td style="padding:10px 0;border-bottom:1px solid {RULE};">'
                        f'<a href="{esc(a.get("link"))}" style="font-family:{SERIF};font-size:17px;'
                        f'color:{INK};text-decoration:none;">{esc(a.get("headline"))}</a>'
                        f'<div style="font-family:{SANS};font-size:12px;color:{MUTED};padding-top:3px;">'
                        f'{esc(a.get("source"))}</div></td></tr>')
            text += [f"{a.get('headline')} ({a.get('source')})", f"  {a.get('link')}"]
        text.append("")

    rows.append(f'<tr><td style="padding:30px 0 10px;text-align:center;">'
                f'<a href="{SITE}" style="display:inline-block;padding:12px 26px;background:{INK};color:{CREAM};'
                f'font-family:{SANS};font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">'
                f'Open The Morning Skate</a></td></tr>')
    text += [f"Open The Morning Skate: {SITE}"]

    body = (f'<!doctype html><html><body style="margin:0;padding:0;background:{CREAM};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM};">'
            f'<tr><td align="center" style="padding:24px 14px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">'
            f'<tr><td style="text-align:center;padding-bottom:14px;border-bottom:3px double {INK};">'
            f'<div style="font-family:{SERIF};font-size:34px;font-weight:900;letter-spacing:-1px;color:{INK};">'
            f'The Morning Skate</div>'
            f'<div style="font-family:{SANS};font-size:11px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{MUTED};padding-top:6px;">{esc(date)}</div></td></tr>'
            + "".join(rows) + '</table></td></tr></table></body></html>')
    subject = f"The Morning Skate: {art.get('title')}" if art.get("title") else f"The Morning Skate - {date}"
    return subject, body, "\n".join(text)


def main():
    data_path = "data.json"
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    subject, body, text = build(data)
    if len(sys.argv) >= 3 and sys.argv[1] == "--preview":
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            f.write(body)
        print(f"Preview written to {sys.argv[2]} | subject: {subject}")
        return 0
    user, pw, to = (os.environ.get(k, "").strip() for k in ("BRIEF_SMTP_USER", "BRIEF_SMTP_PASS", "BRIEF_TO"))
    if not (user and pw and to):
        print("Brief email not configured (BRIEF_SMTP_USER / BRIEF_SMTP_PASS / BRIEF_TO) - skipping.")
        return 0
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"The Morning Skate <{user}>", to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))
    recipients = [a.strip() for a in to.split(",") if a.strip()]
    try:
        with smtplib.SMTP_SSL(os.environ.get("BRIEF_SMTP_HOST", "smtp.gmail.com"),
                              int(os.environ.get("BRIEF_SMTP_PORT", "465")), timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
    except Exception as e:
        print(f"Brief email FAILED: {e}")
        return 1
    print(f"Brief emailed to {len(recipients)} recipient(s): {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
