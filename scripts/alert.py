#!/usr/bin/env python3
"""Plain-English build alerts for The Morning Skate.

  python scripts/alert.py open      # after a failed build
  python scripts/alert.py resolve   # after a successful build

`open` reads the step logs (tests.txt, update.log, validation.txt), works out
WHAT broke in plain English, and opens a GitHub issue that @-mentions the
owner - GitHub emails that to him - or comments on the alert already open.
If the brief-email SMTP secrets exist, the same message is emailed directly.
`resolve` closes any open alert once a good edition ships.

Needs GH_TOKEN and GITHUB_REPOSITORY (both provided by Actions). Never fails
the workflow itself: an alerting problem must not mask the original failure.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

TITLE_PREFIX = "Morning Skate build failed"
OWNER = "Just2940"

# Validator rule -> what it means for a reader, in plain English
RULE_MEANINGS = {
    "0.29": "ESPN's data didn't load, so scores, schedules, records and standings couldn't be updated.",
    "0.30": "An article from a source that isn't on the approved news list got into the edition.",
    "0.10": "Some article links in the edition are dead (they return 'page not found').",
    "0.14": "Some team logos failed to load from ESPN.",
    "0.3": "Banned punctuation (curly quotes or long dashes) slipped into the text.",
    "0.15": "Garbled characters (encoding errors) slipped into the text.",
    "0.4": "A ticker item broke the ticker rules (too long, or stale).",
    "0.5": "A consistency check failed (a team's facts contradicted each other, or too few sources).",
    "0.8": "A team's standings table came back empty.",
    "0.9": "The draft board section had stale or placeholder information.",
    "0.1": "A link pointed somewhere it shouldn't (search page or redirect).",
}


def read(name):
    try:
        with open(name, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def diagnose():
    """(short cause, list of plain-English explanation lines, raw detail lines)."""
    tests, update, validation = read("tests.txt"), read("update.log"), read("validation.txt")
    if re.search(r"^(FAILED|FAIL:|ERROR:)", tests, re.M):
        names = re.findall(r"^(?:FAIL|ERROR): (\w+)", tests, re.M)
        return ("a code change broke a safety test",
                ["An automated safety test failed before the update even ran - a recent code "
                 "change broke something the tests protect (phases, scores, sources or AI checks)."],
                [f"Failed test: {n}" for n in names[:8]])
    fails = re.findall(r"\[FAIL\] ([\d.]+) ([^:]*): ?(.*)", validation)
    if fails:
        meanings, seen = [], set()
        for rule, _name, _msg in fails:
            m = RULE_MEANINGS.get(rule) or RULE_MEANINGS.get(rule.rsplit(".", 1)[0])
            if m and m not in seen:
                seen.add(m)
                meanings.append(m)
        if not meanings:
            meanings = ["The content checks found a problem with the new edition."]
        short = {"0.29": "ESPN data didn't load", "0.30": "a non-approved source slipped in"}.get(
            fails[0][0], f"content check {fails[0][0]} failed")
        return (short, meanings, [f"[{r}] {n.strip()}: {m.strip()}"[:220] for r, n, m in fails[:10]])
    tb = re.findall(r"^\s*(\w+(?:Error|Exception)[^\n]*)$", update, re.M)
    if "Traceback" in update or tb:
        return ("the update script crashed",
                ["The daily update script crashed before it finished."],
                [f"Last error: {tb[-1][:220]}"] if tb else [])
    mail = read("email.txt")
    if "Brief email FAILED" in mail:
        return ("the brief email didn't send",
                ["Today's edition published fine, but the morning brief email failed to send - "
                 "usually the Gmail app password (BRIEF_SMTP_PASS secret) was revoked or mistyped."],
                [ln.strip() for ln in mail.splitlines() if "FAILED" in ln][:3])
    return ("an unexpected error",
            ["The daily build failed for a reason the alert script couldn't pin down - see the run log."],
            [])


def api(method, path, body=None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "Just2940/the-morning-skate")
    req = Request(f"https://api.github.com/repos/{repo}{path}", method=method,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                           "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "morning-skate-alerts"})
    with urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def open_alerts():
    issues = api("GET", "/issues?state=open&per_page=50")
    return [i for i in issues if i.get("title", "").startswith(TITLE_PREFIX) and "pull_request" not in i]


def email(subject, text):
    """Best effort: reuse the brief's SMTP settings when they exist."""
    user, pw, to = (os.environ.get(k, "") for k in ("BRIEF_SMTP_USER", "BRIEF_SMTP_PASS", "BRIEF_TO"))
    if not (user and pw and to):
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(text)
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        with smtplib.SMTP_SSL(os.environ.get("BRIEF_SMTP_HOST", "smtp.gmail.com"),
                              int(os.environ.get("BRIEF_SMTP_PORT", "465")), timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [a.strip() for a in to.split(",") if a.strip()], msg.as_string())
        print("Alert emailed.")
    except Exception as e:
        print(f"Alert email not sent ({e})")


def _now_et():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))


def cmd_open():
    et = _now_et()
    short, meanings, details = diagnose()
    run_url = os.environ.get("RUN_URL", "")
    email_only = short == "the brief email didn't send"
    opener = (f"@{OWNER} - today's edition is live, but the morning brief email failed ({et:%A, %B %-d})."
              if email_only else
              f"@{OWNER} - this morning's Morning Skate update did not publish ({et:%A, %B %-d}).")
    sees = ("- The site is fine and up to date; only the email is affected." if email_only else
            "- Nothing wrong: the site keeps showing the last good edition until the next successful update.")
    nxt = ("- Tomorrow's run will try the email again; if the app password changed, update the secret." if email_only else
           "- The backup run later this morning tries again automatically. If it keeps failing, it needs a fix.")
    body = "\n".join(
        [opener, "", "**What went wrong**"] + [f"- {m}" for m in meanings] +
        ["", "**What your dad sees**", sees, "", "**What happens next**", nxt] +
        (["", "<details><summary>Technical details</summary>", ""] + [f"    {d}" for d in details] + ["", "</details>"]
         if details else []) +
        ([f"", f"Run log: {run_url}"] if run_url else []))
    title = f"{TITLE_PREFIX}: {short} ({et:%b %-d})"
    try:
        existing = open_alerts()
        if existing:
            api("POST", f"/issues/{existing[0]['number']}/comments", {"body": body})
            print(f"Commented on open alert #{existing[0]['number']}")
        else:
            issue = api("POST", "/issues", {"title": title, "body": body})
            print(f"Opened alert #{issue.get('number')}: {title}")
    except Exception as e:
        print(f"Could not open GitHub alert ({e})")
    email(title, re.sub(r"\*\*|<[^>]+>", "", body))


def cmd_resolve():
    try:
        for issue in open_alerts():
            api("POST", f"/issues/{issue['number']}/comments",
                {"body": "Resolved automatically: today's edition published successfully."})
            api("PATCH", f"/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
            print(f"Closed alert #{issue['number']}")
    except Exception as e:
        print(f"Could not resolve alerts ({e})")


if __name__ == "__main__":
    {"open": cmd_open, "resolve": cmd_resolve}.get((sys.argv[1:] or [""])[0], lambda: print(__doc__))()
    sys.exit(0)
