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
            stars = " &middot; ".join(esc(s) for s in g.get("stars") or [])
            rows.append(
                f'<tr><td style="padding:10px 0;border-bottom:1px solid {RULE};">'
                f'<div style="font-family:{SANS};font-size:11px;color:{MUTED};letter-spacing:1px;">'
                f'{esc(g.get("league"))} {kind.upper()} &middot; {esc(g.get("date"))}</div>'
                f'<div style="font-family:{SERIF};font-size:18px;color:{INK};padding-top:2px;">{tag} {head}</div>'
                + (f'<div style="font-family:{SANS};font-size:13px;color:{MUTED};padding-top:3px;">{stars}</div>'
                   if stars else "") + '</td></tr>')
            text.append(f"{'W' if won else 'L'} {g.get('name')} {g.get('team_score')}, "
                        f"{g.get('opp_name')} {g.get('opp_score')} ({kind}, {g.get('date')})"
                        + (f" - {'; '.join(g.get('stars'))}" if g.get("stars") else ""))
        text.append("")

    # --- Today's games ---------------------------------------------------
    games = [s for s in data.get("today_slate") or [] if not s.get("off")]
    if games:
        rows.append(label("Today"))
        text += ["TODAY"]
        for s in games:
            when = s.get("detail", "") + (f" on {s['channel']}" if s.get("channel") else "")
            notes = "".join(f'<div style="font-family:{SANS};font-size:13px;color:{MUTED};padding-top:3px;">'
                            f'{esc(n)}</div>' for n in s.get("preview") or [])
            rows.append(f'<tr><td style="padding:10px 0;border-bottom:1px solid {RULE};">'
                        f'<div style="font-family:{SERIF};font-size:18px;color:{INK};">{esc(s.get("matchup"))}</div>'
                        f'<div style="font-family:{SANS};font-size:14px;color:{INK};padding-top:2px;">{esc(when)}</div>'
                        f'{notes}</td></tr>')
            text += [f"{s.get('matchup')} - {when}"] + [f"  {n}" for n in s.get("preview") or []]
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
