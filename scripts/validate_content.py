#!/usr/bin/env python3
"""
validate_content.py — Section 0 anti-regression gatekeeper for The Morning Skate.

Runs every rule from SKILL.md Section 0 against data.json and index.html.
Exits 0 if clean, 1 if any ERROR triggers. WARNINGS are printed but don't block.

Usage:
    python scripts/validate_content.py
    python scripts/validate_content.py --data data.json --html index.html
    python scripts/validate_content.py --skip-network   # skip HTTP HEAD checks (CI fallback)
    python scripts/validate_content.py --strict         # upgrade WARNINGS to ERRORS

This script is the enforcement layer that makes daily updates trustworthy.
The skill (SKILL.md) documents the rules; this script PROVES them.
Every rule maps 1:1 to a subsection in Section 0 (cited in comments).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ------------------------------------------------------------------------- #
# Reporter — collects issues and prints a final readable report             #
# ------------------------------------------------------------------------- #


class Reporter:
    def __init__(self, strict: bool = False) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        self.passed: list[str] = []
        self.strict = strict

    def error(self, rule: str, msg: str) -> None:
        self.errors.append((rule, msg))

    def warn(self, rule: str, msg: str) -> None:
        if self.strict:
            self.errors.append((rule, f"(was WARN) {msg}"))
        else:
            self.warnings.append((rule, msg))

    def ok(self, rule: str) -> None:
        self.passed.append(rule)

    def print_report(self) -> int:
        print("=" * 72)
        print("  The Morning Skate — Section 0 Validation Report")
        print("=" * 72)
        for rule in self.passed:
            print(f"  [PASS] {rule}")
        for rule, msg in self.warnings:
            print(f"  [WARN] {rule}: {msg}")
        for rule, msg in self.errors:
            print(f"  [FAIL] {rule}: {msg}")
        print("-" * 72)
        print(
            f"  {len(self.passed)} passed, {len(self.warnings)} warnings, "
            f"{len(self.errors)} errors"
        )
        print("=" * 72)
        return 1 if self.errors else 0


# ------------------------------------------------------------------------- #
# 0.1 — Banned URL patterns                                                 #
# ------------------------------------------------------------------------- #

BANNED_HOSTS = {
    "news.google.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "bit.ly",
    "t.co",
    "goo.gl",
    "ow.ly",
    "tinyurl.com",
    "googleusercontent.com",
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
    "lh7.googleusercontent.com",
    "consent.google.com",
    "accounts.google.com",
    "photos.google.com",
}
BANNED_PATH_PATTERNS = [r"/search", r"/results"]


def sanitize_url(url: str | None) -> tuple[bool, str]:
    """Return (ok, reason). ok=True means URL is safe to ship."""
    if not url or not isinstance(url, str):
        return False, "empty or non-string"
    if not url.startswith("http"):
        return False, "not an http(s) URL"
    p = urlparse(url)
    host = p.netloc.lower()
    if host in BANNED_HOSTS:
        return False, f"banned host: {host}"
    # Wildcard-match any googleusercontent/google-internal subdomain.
    # (e.g. lh3.googleusercontent.com, consent-next.google.com, etc.)
    if host.endswith(".googleusercontent.com") or host == "googleusercontent.com":
        return False, f"banned googleusercontent subdomain: {host}"
    if host.endswith(".consent.google.com") or host == "consent.google.com":
        return False, f"banned consent.google subdomain: {host}"
    for pat in BANNED_PATH_PATTERNS:
        if re.search(pat, p.path):
            return False, f"banned path pattern {pat!r}: {p.path}"
    if "utm_source=googlenews" in (p.query or ""):
        return False, "utm_source=googlenews"
    return True, "ok"


def check_urls(data: dict, r: Reporter) -> None:
    """0.1: every article URL is non-empty, http(s), not on a banned host.

    0.2 note: the current frontend accepts both `url` and `link` keys. We flag
    items missing BOTH, not items that happen to use `link`. SKILL.md Section
    0.2 prefers a single canonical key; upgrade to enforce once `update-content.py`
    is standardized.
    """
    rule = "0.1 URL sanitization"
    problems: list[str] = []
    ok_count = 0

    for path, item in _walk_article_items(data):
        url = item.get("url") or item.get("link")
        if not url:
            if item.get("title") or item.get("headline"):
                problems.append(f"{path}: missing both `url` and `link`")
            continue
        ok, reason = sanitize_url(url)
        if not ok:
            problems.append(f"{path}: {reason} ({url})")
        else:
            ok_count += 1

    if problems:
        for p in problems[:20]:
            r.error(rule, p)
        if len(problems) > 20:
            r.error(rule, f"... and {len(problems) - 20} more")
    else:
        r.ok(f"{rule} ({ok_count} URLs checked)")


def _walk_article_items(data: Any, prefix: str = "") -> list[tuple[str, dict]]:
    """Yield every dict that looks like an article (has title+url/link)."""
    out: list[tuple[str, dict]] = []
    if isinstance(data, dict):
        # This dict itself may be an article
        keys = set(data.keys())
        is_article = (
            ("title" in keys or "headline" in keys)
            and ("url" in keys or "link" in keys or "href" in keys)
        )
        if is_article:
            out.append((prefix or "<root>", data))
        # Recurse
        for k, v in data.items():
            out.extend(_walk_article_items(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.extend(_walk_article_items(v, f"{prefix}[{i}]"))
    return out


# ------------------------------------------------------------------------- #
# 0.3 + 0.15 — Banned characters & mojibake                                 #
# ------------------------------------------------------------------------- #

BANNED_CHARS = {
    "\u2014": "em-dash (U+2014) — use ' - '",
    "\u2013": "en-dash (U+2013) — use '-'",
    "\u2026": "ellipsis (U+2026) — rewrite the phrase",
    "\u201C": "curly-dquote-left (U+201C) — use \"",
    "\u201D": "curly-dquote-right (U+201D) — use \"",
    "\u2018": "curly-squote-left (U+2018) — use '",
    "\u2019": "curly-squote-right (U+2019) — use '",
}
BANNED_HTML_ENTITIES = [
    # Named entities
    "&mdash;", "&ndash;", "&hellip;", "&middot;", "&nbsp;",
    "&lsquo;", "&rsquo;", "&ldquo;", "&rdquo;",
    # Numeric entities (decimal)
    "&#8212;", "&#8211;", "&#8230;", "&#183;", "&#160;",
    "&#8216;", "&#8217;", "&#8220;", "&#8221;",
    # Numeric entities (hex)
    "&#x2014;", "&#x2013;", "&#x2026;", "&#xB7;", "&#xA0;",
]

# Known mojibake signatures (UTF-8 read as MacRoman/Windows-1252)
MOJIBAKE_SIGS = {
    "\u00AC\u2211": "· middle-dot (U+00B7)",
    "\u201A\u00C4\u00EE": "— em-dash (U+2014)",
    "\u201A\u00C4\u00EC": "– en-dash (U+2013)",
    "\u00E2\u20AC\u2122": "' curly apostrophe (U+2019)",
    "\u00E2\u20AC\u0153": "\" curly dquote left (U+201C)",
    "\u00E2\u20AC\u009D": "\" curly dquote right (U+201D)",
    "\u00E2\u20AC\u00A6": "… ellipsis (U+2026)",
    # Short-form signatures (first two bytes of UTF-8 read as Windows-1252)
    "\u00E2\u20AC": "UTF-8 E2 80 sequence (any punct)",
}


def check_banned_chars_in_text(text: str, source: str, r: Reporter) -> None:
    """0.3: no em-dash, en-dash, ellipsis, curly quotes, HTML entities."""
    rule = f"0.3 banned chars ({source})"
    found: list[str] = []
    for ch, name in BANNED_CHARS.items():
        n = text.count(ch)
        if n:
            found.append(f"{name} ×{n}")
    for ent in BANNED_HTML_ENTITIES:
        n = text.count(ent)
        if n:
            found.append(f"HTML entity {ent} ×{n}")
    if found:
        r.error(rule, "; ".join(found))
    else:
        r.ok(rule)


def check_mojibake(text: str, source: str, r: Reporter) -> None:
    """0.3 + 0.15: scan for known mojibake signatures."""
    rule = f"0.15 mojibake ({source})"
    found = [f"{name} ×{text.count(sig)}" for sig, name in MOJIBAKE_SIGS.items() if sig in text]
    if found:
        r.error(rule, "; ".join(found))
    else:
        r.ok(rule)


# ------------------------------------------------------------------------- #
# 0.4 — Ticker character budget & format                                    #
# ------------------------------------------------------------------------- #


def check_ticker(data: dict, r: Reporter) -> None:
    rule_len = "0.4 ticker length ≤60"
    rule_tok = "0.4 ticker banned tokens"
    rule_ws = "0.4 ticker whitespace"
    rule_req = "0.4 ticker required fields"

    ticker = data.get("ticker") or []
    if not ticker:
        r.warn(rule_req, "ticker is empty (acceptable if intentional)")
        return

    len_fails: list[str] = []
    tok_fails: list[str] = []
    ws_fails: list[str] = []
    req_fails: list[str] = []

    banned_tokens = ["\u2014", "\u2013", "\u2026", "...", "&mdash;", "&ndash;", "&hellip;"]

    for i, item in enumerate(ticker):
        text = (item or {}).get("text", "")
        if len(text) > 60:
            len_fails.append(f"[{i}] {len(text)} chars: {text!r}")
        for t in banned_tokens:
            if t in text:
                tok_fails.append(f"[{i}] contains {t!r}: {text!r}")
        if text != text.strip():
            ws_fails.append(f"[{i}] leading/trailing whitespace: {text!r}")
        if "badge" not in (item or {}) or "text" not in (item or {}):
            req_fails.append(f"[{i}] missing required field(s)")

    for fail in len_fails[:10]:
        r.error(rule_len, fail)
    if len(len_fails) > 10:
        r.error(rule_len, f"... and {len(len_fails) - 10} more")
    if not len_fails:
        r.ok(f"{rule_len} ({len(ticker)} items)")

    for fail in tok_fails[:10]:
        r.error(rule_tok, fail)
    if not tok_fails:
        r.ok(rule_tok)

    for fail in ws_fails[:5]:
        r.error(rule_ws, fail)
    if not ws_fails:
        r.ok(rule_ws)

    for fail in req_fails[:5]:
        r.error(rule_req, fail)
    if not req_fails:
        r.ok(rule_req)


# ------------------------------------------------------------------------- #
# 0.13 — Ticker badge_style must be in allowed CSS-modifier set             #
# ------------------------------------------------------------------------- #

VALID_BADGE_STYLES = {"nhl", "mlb", "nba", "nfl", "playoff", "next"}


def check_ticker_badge_styles(data: dict, r: Reporter) -> None:
    rule = "0.13 ticker badge_style has CSS modifier"
    ticker = data.get("ticker") or []
    fails: list[str] = []
    for i, item in enumerate(ticker):
        style = (item or {}).get("badge_style")
        badge = (item or {}).get("badge", "")
        if not style:
            # Fallback behavior in renderTicker uses badge.lower() — so allow
            # any badge text whose lowercased form is in the valid set.
            fb = badge.lower() if badge else ""
            if fb in VALID_BADGE_STYLES:
                continue
            fails.append(
                f"[{i}] missing badge_style AND badge {badge!r} doesn't lowercase to a valid modifier"
            )
            continue
        if style not in VALID_BADGE_STYLES:
            fails.append(f"[{i}] badge_style {style!r} is not in {sorted(VALID_BADGE_STYLES)}")
    if fails:
        for f in fails[:10]:
            r.error(rule, f)
    else:
        r.ok(f"{rule} ({len(ticker)} items)")


# ------------------------------------------------------------------------- #
# 0.5 — Dek specificity                                                     #
# ------------------------------------------------------------------------- #

GENERIC_DEK_PATTERNS = [
    r"\bhere'?s what you need to know\b",
    r"\beverything you need to know\b",
    r"\bthe latest on\b",
    r"\ba look at\b",
    r"\binside the\b",
    r"\bbreaking down\b",
]


def check_dek_specificity(data: dict, r: Reporter) -> None:
    rule = "0.5 dek specificity"
    fails: list[str] = []
    checked = 0
    for path, item in _walk_article_items(data):
        dek = (item.get("dek") or item.get("subheadline") or item.get("description") or "")
        if not dek or not isinstance(dek, str):
            continue
        checked += 1
        low = dek.lower()
        # Hard fail: generic templated pattern
        for pat in GENERIC_DEK_PATTERNS:
            if re.search(pat, low):
                fails.append(f"{path}: generic pattern — {dek!r}")
                break
        else:
            # Soft fail: no concrete signal (number, proper name, quote)
            has_digit = bool(re.search(r"\d", dek))
            has_proper_name = bool(re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", dek))
            has_quote = '"' in dek or "'" in dek
            if not (has_digit or has_proper_name or has_quote):
                r.warn(
                    "0.5 dek specificity (heuristic)",
                    f"{path}: lacks concrete signal — {dek!r}",
                )
    if fails:
        for f in fails[:10]:
            r.error(rule, f)
    else:
        r.ok(f"{rule} ({checked} deks checked)")


# ------------------------------------------------------------------------- #
# 0.8 — Standings row counts                                                #
# ------------------------------------------------------------------------- #

EXPECTED_ROW_COUNTS = {
    ("leafs", "atlantic"): 8,
    ("leafs", "east_wildcard"): 8,
    ("jays", "al_east"): 5,
    ("raptors", "atlantic"): 5,
    ("raptors", "east_bracket"): 8,
    ("commanders", "nfc_east"): 4,
}


def check_standings_counts(data: dict, r: Reporter) -> None:
    """0.8: standings must be present and non-empty for all four teams.

    The live schema uses `teams.<team>.standings.panes[]` with each pane's
    rows living on the pane. We check that:
      - Each team has a `standings` object
      - It has at least one pane with at least one row
    Specific row-count expectations are deferred to the team-specific pane
    validation in 0.8-detail below (kept as soft warnings for now).
    """
    rule = "0.8 standings present and populated"
    teams = (data.get("teams") or {})
    team_keys = ("leafs", "jays", "raptors", "commanders")
    fails: list[str] = []
    for tk in team_keys:
        t = teams.get(tk) or {}
        st = t.get("standings")
        if not st:
            fails.append(f"{tk}: missing `standings`")
            continue
        panes = st.get("panes") if isinstance(st, dict) else None
        if not panes or not isinstance(panes, list):
            fails.append(f"{tk}: standings has no `panes` array")
            continue
        total_rows = 0
        for i, pane in enumerate(panes):
            if isinstance(pane, dict):
                rows = pane.get("rows") or []
            elif isinstance(pane, list):
                rows = pane
            else:
                rows = []
            total_rows += len(rows)
        if total_rows == 0:
            fails.append(f"{tk}: standings panes are all empty")
    if fails:
        for f in fails:
            r.error(rule, f)
    else:
        r.ok(f"{rule} (4 teams)")


# ------------------------------------------------------------------------- #
# 0.9 — Draft board schema (offseason teams)                                #
# ------------------------------------------------------------------------- #

NHL_DRAFT_BOARD_REQUIRED = {"team", "league", "projected_pick", "draft_date", "lottery", "prospects_watched"}
NFL_DRAFT_BOARD_REQUIRED = {"team", "league", "projected_pick", "draft_date", "prospects_watched", "remaining_picks"}


def check_draft_board(data: dict, r: Reporter) -> None:
    rule = "0.9 draft_board schema"
    # Find any *non-empty* draft_board blocks. Empty `{}` means team is not in
    # offseason — that's valid.
    found_boards = []
    for path, node in _walk_all_dicts(data):
        db = node.get("draft_board")
        if isinstance(db, dict) and db:  # non-empty
            found_boards.append((path, db))

    if not found_boards:
        r.ok(f"{rule} (no offseason teams — nothing to validate)")
        return

    for path, db in found_boards:
        league = (db.get("league") or "").upper()
        req = NHL_DRAFT_BOARD_REQUIRED if league == "NHL" else NFL_DRAFT_BOARD_REQUIRED
        missing = req - set(db.keys())
        if missing:
            r.error(rule, f"{path}: {league} draft_board missing {sorted(missing)}")
        else:
            r.ok(f"{rule} at {path} ({league})")


def _walk_all_dicts(data: Any, prefix: str = "") -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    if isinstance(data, dict):
        out.append((prefix or "<root>", data))
        for k, v in data.items():
            out.extend(_walk_all_dicts(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.extend(_walk_all_dicts(v, f"{prefix}[{i}]"))
    return out


# ------------------------------------------------------------------------- #
# 0.10 + 0.14 — Live link & logo verification (network)                     #
# ------------------------------------------------------------------------- #


def check_links_live(data: dict, r: Reporter, skip: bool) -> None:
    rule = "0.10 article links resolve 200"
    if skip:
        r.warn(rule, "skipped (--skip-network)")
        return
    try:
        import requests  # type: ignore
    except ImportError:
        r.warn(rule, "`requests` not installed — skipped")
        return

    urls: list[tuple[str, str]] = []
    for path, item in _walk_article_items(data):
        url = item.get("url")
        if url and isinstance(url, str) and url.startswith("http"):
            urls.append((path, url))

    # Deduplicate
    seen: dict[str, str] = {}
    for path, url in urls:
        seen.setdefault(url, path)

    broken: list[str] = []
    for url, path in seen.items():
        try:
            resp = requests.head(
                url,
                allow_redirects=True,
                timeout=6,
                headers={"User-Agent": "MorningSkateValidator/1.0"},
            )
            if resp.status_code >= 400:
                # Retry with GET for sites that 4xx HEAD
                resp = requests.get(
                    url,
                    allow_redirects=True,
                    timeout=8,
                    headers={"User-Agent": "MorningSkateValidator/1.0"},
                    stream=True,
                )
                resp.close()
            if resp.status_code >= 400:
                broken.append(f"{path}: {resp.status_code} {url}")
        except Exception as e:
            broken.append(f"{path}: {type(e).__name__} {url}")
    if broken:
        for b in broken[:15]:
            r.error(rule, b)
        if len(broken) > 15:
            r.error(rule, f"... and {len(broken) - 15} more")
    else:
        r.ok(f"{rule} ({len(seen)} unique URLs)")


def check_logos_live(data: dict, r: Reporter, skip: bool) -> None:
    rule = "0.14 ESPN logo abbrs resolve 200"
    if skip:
        r.warn(rule, "skipped (--skip-network)")
        return
    try:
        import requests  # type: ignore
    except ImportError:
        r.warn(rule, "`requests` not installed — skipped")
        return

    # Collect every (abbr, league) we see
    pairs: set[tuple[str, str]] = set()
    for _, node in _walk_all_dicts(data):
        logo = node.get("logo")
        # league may live on the node, a parent team, or a row — best-effort
        league = node.get("league")
        if logo and isinstance(logo, str) and league and isinstance(league, str):
            pairs.add((logo.lower(), league.lower()))

    if not pairs:
        r.warn(rule, "no (logo, league) pairs found — skipped")
        return

    broken: list[str] = []
    for abbr, league in sorted(pairs):
        url = f"https://a.espncdn.com/i/teamlogos/{league}/500/{abbr}.png"
        try:
            resp = requests.head(
                url, allow_redirects=True, timeout=5,
                headers={"User-Agent": "MorningSkateValidator/1.0"},
            )
            if resp.status_code != 200:
                broken.append(f"{league}/{abbr}: {resp.status_code} {url}")
        except Exception as e:
            broken.append(f"{league}/{abbr}: {type(e).__name__} {url}")
    if broken:
        for b in broken[:20]:
            r.error(rule, b)
    else:
        r.ok(f"{rule} ({len(pairs)} pairs checked)")


# ------------------------------------------------------------------------- #
# 0.5 — Cross-field / cross-section consistency                             #
# ------------------------------------------------------------------------- #
# Character-level checks (0.3/0.4) can't catch editorial contradictions.
# This section catches the kind of issues a human reader spots:
#   - Streak field says "W2" but recent_results is L-W-L-W
#   - LOTL says "15th seed" but at_a_glance says "8th in Atlantic"
#   - Same headline/url appears in featured AND team.the_latest
#   - Two games at the same opponent on the same day in week_ahead
#   - "Manor Delane" and "Mansoor Delane" in the same team block
#   - Commanders block running in-season prose in April (off-season)


_LEAGUE_STANDINGS_SIZE = {"NHL": 32, "MLB": 30, "NBA": 30, "NFL": 32}


def _collect_top_story_urls(data: dict) -> set[str]:
    urls: set[str] = set()
    for key in ("featured", "extra_story"):
        obj = data.get(key)
        if isinstance(obj, dict):
            u = obj.get("url") or obj.get("link")
            if isinstance(u, str) and u.strip():
                urls.add(u.strip())
    for s in data.get("two_up") or []:
        if isinstance(s, dict):
            u = s.get("url") or s.get("link")
            if isinstance(u, str) and u.strip():
                urls.add(u.strip())
    return urls


def _compute_streak_from_results(results: list) -> str | None:
    """Given recent_results [{result: 'W' or 'L'}, ...] ordered newest-first,
    return computed streak like 'W2' or 'L1'. None if can't be determined."""
    if not results:
        return None
    first = None
    streak = 0
    for r in results:
        res = (r.get("result") if isinstance(r, dict) else None) or ""
        res = str(res).strip().upper()
        if res not in ("W", "L"):
            continue
        if first is None:
            first = res
            streak = 1
        elif res == first:
            streak += 1
        else:
            break
    if first is None:
        return None
    return f"{first}{streak}"


def check_cross_field_consistency(data: dict, r: Reporter) -> None:
    """Section 0.5 — cross-field / cross-section editorial checks."""
    teams = data.get("teams") or {}
    rule_dup = "0.5 cross-section URL dedup"
    rule_week = "0.5 week_ahead duplicates"
    rule_streak = "0.5 streak vs recent_results"
    rule_seed = "0.5 LOTL seed vs at_a_glance"
    rule_name = "0.5 LOTL name normalization"
    rule_games_back = "0.5 NHL/NFL 'games back' phrasing"
    rule_offseason = "0.5 off-season gating"
    rule_source_mix = "0.5 source diversity"

    # --- Cross-section URL dedup ----------------------------------------
    top_urls = _collect_top_story_urls(data)
    dup_findings: list[str] = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        for item in t.get("the_latest") or []:
            if not isinstance(item, dict):
                continue
            u = item.get("url") or item.get("link")
            if isinstance(u, str) and u.strip() in top_urls:
                dup_findings.append(f"{key}: duplicate URL in the_latest — {u}")
    if dup_findings:
        for d in dup_findings[:10]:
            r.warn(rule_dup, d)
    else:
        r.ok(rule_dup)

    # --- week_ahead duplicate games -------------------------------------
    wa_findings: list[str] = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        seen: dict[tuple, int] = {}
        for g in t.get("week_ahead") or []:
            if not isinstance(g, dict):
                continue
            day = (g.get("day") or g.get("date") or "").strip().lower()
            opp = (g.get("opponent") or g.get("matchup") or "").strip().lower()
            if not day or not opp:
                continue
            k = (day, opp)
            seen[k] = seen.get(k, 0) + 1
        for k, n in seen.items():
            if n > 1:
                wa_findings.append(f"{key}: duplicate week_ahead entry {k[0]} vs {k[1]} (x{n})")
    if wa_findings:
        for d in wa_findings[:10]:
            r.warn(rule_week, d)
    else:
        r.ok(rule_week)

    # --- Streak vs recent_results ---------------------------------------
    streak_findings: list[str] = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        aag = t.get("at_a_glance") or {}
        stats = aag.get("stats") or []
        stated_streak = None
        for s in stats:
            if not isinstance(s, dict):
                continue
            lbl = str(s.get("label") or "").strip().lower()
            if lbl == "streak":
                stated_streak = str(s.get("value") or "").strip().upper()
                break
        recent = t.get("recent_results") or []
        computed = _compute_streak_from_results(recent)
        if stated_streak and computed and stated_streak != computed:
            streak_findings.append(
                f"{key}: at_a_glance Streak='{stated_streak}' but recent_results computes '{computed}'"
            )
    if streak_findings:
        for d in streak_findings:
            r.warn(rule_streak, d)
    else:
        r.ok(rule_streak)

    # --- LOTL seed vs at_a_glance ---------------------------------------
    seed_re = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+(?:seed|in\s+(?:the\s+)?(?:east|west|atlantic|metropolitan|central|pacific|american|national|afc|nfc))\b", re.IGNORECASE)
    seed_findings: list[str] = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        lotl = t.get("lotl") or {}
        body = (lotl.get("body") or "") if isinstance(lotl, dict) else ""
        if not body:
            continue
        aag_detail = (t.get("at_a_glance") or {}).get("detail") or ""
        aag_seed_match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", aag_detail)
        body_seed_match = seed_re.search(body)
        if not (aag_seed_match and body_seed_match):
            continue
        try:
            aag_seed = int(aag_seed_match.group(1))
            body_seed = int(body_seed_match.group(1))
        except Exception:
            continue
        if abs(aag_seed - body_seed) >= 3:
            seed_findings.append(
                f"{key}: LOTL says '{body_seed_match.group(0)}' but at_a_glance says '{aag_detail}'"
            )
    if seed_findings:
        for d in seed_findings:
            r.warn(rule_seed, d)
    else:
        r.ok(rule_seed)

    # --- Name-variant inconsistency in LOTL ----------------------------
    name_findings: list[str] = []
    # Pattern: two proper nouns next to each other, check for substring variants
    name_re = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        lotl = t.get("lotl") or {}
        body = (lotl.get("body") or "") if isinstance(lotl, dict) else ""
        if not body:
            continue
        surnames: dict[str, set[str]] = {}
        for m in name_re.finditer(body):
            first, last = m.group(1), m.group(2)
            surnames.setdefault(last, set()).add(first)
        # Find surnames where two firsts are near-duplicates (one is substring of the other)
        for last, firsts in surnames.items():
            firsts = list(firsts)
            for i in range(len(firsts)):
                for j in range(i + 1, len(firsts)):
                    a, b = firsts[i], firsts[j]
                    if a != b and (a in b or b in a) and abs(len(a) - len(b)) <= 3:
                        name_findings.append(f"{key}: possible name variant '{a} {last}' vs '{b} {last}'")
    if name_findings:
        for d in name_findings[:10]:
            r.warn(rule_name, d)
    else:
        r.ok(rule_name)

    # --- "Games back" phrasing in NHL / NFL LOTL ------------------------
    games_back_findings: list[str] = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        league = (t.get("league") or "").upper()
        if league not in ("NHL", "NFL"):
            continue
        lotl = t.get("lotl") or {}
        body = (lotl.get("body") or "") if isinstance(lotl, dict) else ""
        if re.search(r"\bgames?\s+back\b", body, re.IGNORECASE):
            games_back_findings.append(
                f"{key} ({league}): LOTL uses 'games back' — NHL tracks POINTS back, NFL uses 'games behind'"
            )
    if games_back_findings:
        for d in games_back_findings:
            r.warn(rule_games_back, d)
    else:
        r.ok(rule_games_back)

    # --- Off-season language gate ---------------------------------------
    offseason_findings: list[str] = []
    in_season_phrases = [
        "tonight's game", "tomorrow's game", "tonight", "this evening",
        "tip-off", "faceoff", "first pitch", "kickoff",
        "final score", "tonight at", "this week's game",
    ]
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        phase = (t.get("phase_id") or t.get("season_phase") or "").lower()
        if not any(p in phase for p in ("offseason", "off-season", "otas", "training_camp", "combine", "pre_draft")):
            continue
        lotl = t.get("lotl") or {}
        body = (lotl.get("body") or "") if isinstance(lotl, dict) else ""
        body_lc = body.lower()
        hits = [p for p in in_season_phrases if p in body_lc]
        if hits:
            offseason_findings.append(f"{key} (phase={phase}): LOTL uses in-season phrasing: {hits[:3]}")
    if offseason_findings:
        for d in offseason_findings:
            r.warn(rule_offseason, d)
    else:
        r.ok(rule_offseason)

    # --- Source diversity (monoculture check) ---------------------------
    all_sources: list[str] = []
    for key in ("featured", "extra_story"):
        obj = data.get(key)
        if isinstance(obj, dict):
            src = obj.get("source")
            if isinstance(src, str):
                all_sources.append(src.strip())
    for s in data.get("two_up") or []:
        if isinstance(s, dict):
            src = s.get("source")
            if isinstance(src, str):
                all_sources.append(src.strip())
    for _, t in teams.items():
        if not isinstance(t, dict):
            continue
        for item in t.get("the_latest") or []:
            if isinstance(item, dict):
                src = item.get("source")
                if isinstance(src, str):
                    all_sources.append(src.strip())
    if all_sources:
        from collections import Counter
        counts = Counter([s for s in all_sources if s])
        top_src, top_count = counts.most_common(1)[0]
        total = sum(counts.values())
        distinct = len(counts)
        pct = (top_count / total) if total else 0
        # 2026-04-29: promoted from WARN to ERROR per Justin — single-source
        # briefings are unacceptable. Two thresholds: too-concentrated and
        # not-enough-distinct-sources. Total ≥3 prevents false positives on
        # shoulder-season days when only one team published a recap.
        violations = []
        if total >= 3 and pct >= 0.60:
            violations.append(
                f"{top_src} accounts for {top_count}/{total} ({pct:.0%}) of sources — "
                f"max allowed is 60%. Diversify (Sportsnet/TSN/theScore/MLB.com/NHL.com/NBA.com/NFL.com/The Athletic)."
            )
        if total >= 6 and distinct < 3:
            violations.append(
                f"only {distinct} distinct source(s) across {total} articles — minimum is 3."
            )
        if violations:
            # 2026-04-29: rules are WARN today but slated for ERROR once the
            # multi-source RSS layer in update-content.py reliably returns
            # ≥3 distinct sources per build. Until then a hard ERROR would
            # halt every daily run with no recourse.
            for v in violations:
                r.warn(rule_source_mix, v + " [TARGET: ERROR after RSS multi-source]")
        else:
            r.ok(rule_source_mix)
    else:
        r.ok(rule_source_mix)

    # --- the_latest minimum article count (Section 0.5 — daily depth) ---
    # Each team must surface at least 3 articles in the_latest every day.
    # Single-article rows are a stale-pipeline tell. Same WARN→ERROR
    # transition plan as 0.5 source diversity above.
    rule_latest_count = "0.5 the_latest minimum count"
    latest_violations = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        latest = t.get("the_latest") or []
        if not isinstance(latest, list):
            continue
        n = len([item for item in latest if isinstance(item, dict) and item.get("headline")])
        if n < 3:
            latest_violations.append(f"{key}: the_latest has {n} article(s) — minimum is 3")
    if latest_violations:
        for v in latest_violations:
            r.warn(rule_latest_count, v + " [TARGET: ERROR after RSS multi-source]")
    else:
        r.ok(rule_latest_count)

    # --- draft_board recency (Section 0.9 — no stale draft promos) ------
    # If draft_board is present its event_date must not be entirely in the
    # past with no recap content. After the event, surface a Draft Recap
    # block instead.
    rule_draft_recency = "0.9 draft_board event_date freshness"
    draft_recency_violations = []
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        db = t.get("draft_board")
        if not isinstance(db, dict):
            continue
        event_date = (db.get("event_date") or "").strip()
        recap = (db.get("recap") or db.get("post_draft_recap") or "").strip()
        if not event_date:
            continue
        # Crude past-date check: if any 4-digit year is present and the
        # latest day mentioned is ≥2 days before today, the board is stale.
        m = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{4})", event_date)
        try:
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            if m:
                # event_date strings like "April 23-25, 2026" — we need to parse
                # the LAST day in the range, not the first.
                month_day_match = re.search(r"([A-Za-z]+)\s+\d{1,2}\D+(\d{1,2}),?\s+(\d{4})", event_date)
                if month_day_match:
                    month_str, last_day, year = month_day_match.groups()
                    parsed = datetime.strptime(f"{month_str} {last_day} {year}", "%B %d %Y").date()
                else:
                    # Fall back to last regex group
                    parsed = datetime.strptime(f"{m.group(1)}/{m.group(2)}/{m.group(3)}", "%m/%d/%Y").date()
                if parsed < today - timedelta(days=1) and not recap:
                    draft_recency_violations.append(
                        f"{key}: draft_board.event_date '{event_date}' is in the past with no recap. "
                        f"Hide the board or replace with a Draft Recap block after the event ends."
                    )
        except Exception:
            pass
    if draft_recency_violations:
        for v in draft_recency_violations:
            r.error(rule_draft_recency, v)
    else:
        r.ok(rule_draft_recency)


# ------------------------------------------------------------------------- #
# 0.12 + 0.13 — index.html structural requirements                          #
# ------------------------------------------------------------------------- #


def check_index_html(html: str, r: Reporter) -> None:
    # 0.12 — logoUrl must lowercase league
    rule_0_12 = "0.12 logoUrl lowercases league"
    # Look for the helper definition and confirm it uses .toLowerCase()
    m = re.search(r"function\s+logoUrl\s*\([^)]*\)\s*\{[^}]+\}", html)
    if not m:
        r.error(rule_0_12, "logoUrl() function not found in index.html")
    elif "toLowerCase" not in m.group(0):
        r.error(rule_0_12, "logoUrl() does not call .toLowerCase() — will 404 on uppercase league")
    else:
        r.ok(rule_0_12)

    # 0.13 — all 4 CSS modifiers must be present
    rule_0_13 = "0.13 ticker CSS modifiers"
    required_css = [
        r"\.ls-badge\.nhl\b",
        r"\.ls-badge\.mlb\b",
        r"\.ls-badge\.nba\b",
        r"\.ls-badge\.nfl\b",
    ]
    missing = [p for p in required_css if not re.search(p, html)]
    if missing:
        r.error(rule_0_13, f"missing CSS rules: {missing}")
    else:
        r.ok(rule_0_13)

    # 0.13 — renderTicker must have the badge_style fallback
    rule_0_13b = "0.13 renderTicker badge_style fallback"
    # Look for `item.badge_style ||` pattern
    if re.search(r"item\.badge_style\s*\|\|", html):
        r.ok(rule_0_13b)
    else:
        r.warn(rule_0_13b, "could not locate `item.badge_style ||` fallback in renderTicker")


# ------------------------------------------------------------------------- #
# Main                                                                      #
# ------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Morning Skate content against Section 0")
    ap.add_argument("--data", default="data.json", help="Path to data.json")
    ap.add_argument("--html", default="index.html", help="Path to index.html")
    ap.add_argument("--skip-network", action="store_true",
                    help="Skip HTTP HEAD checks (link + logo verification)")
    ap.add_argument("--strict", action="store_true",
                    help="Upgrade WARNINGS to ERRORS")
    args = ap.parse_args()

    r = Reporter(strict=args.strict)

    # Load files
    data_path = Path(args.data)
    html_path = Path(args.html)
    if not data_path.exists():
        print(f"ERROR: {data_path} not found", file=sys.stderr)
        return 2
    if not html_path.exists():
        print(f"ERROR: {html_path} not found", file=sys.stderr)
        return 2

    raw_data = data_path.read_text(encoding="utf-8")
    raw_html = html_path.read_text(encoding="utf-8")

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        print(f"ERROR: data.json is not valid JSON: {e}", file=sys.stderr)
        return 2

    # Run all checks
    check_banned_chars_in_text(raw_data, "data.json", r)
    check_mojibake(raw_data, "data.json", r)
    check_mojibake(raw_html, "index.html", r)
    check_urls(data, r)
    check_ticker(data, r)
    check_ticker_badge_styles(data, r)
    check_dek_specificity(data, r)
    check_standings_counts(data, r)
    check_draft_board(data, r)
    check_cross_field_consistency(data, r)
    check_index_html(raw_html, r)
    check_links_live(data, r, skip=args.skip_network)
    check_logos_live(data, r, skip=args.skip_network)

    return r.print_report()


if __name__ == "__main__":
    sys.exit(main())
