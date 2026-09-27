"""Game details for The Morning Skate: who starred in a final, and what to
know before a game. Pure functions over ESPN game-summary JSON (and Daily
Faceoff's starting-goalie data) so they can be unit-tested with fixtures.
Deterministic - no AI. Every function returns [] / "" when data is missing.

Output strings are plain ASCII; the page joins list items with a middle dot.
"""
import re

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def last_name(name):
    """'T. Blueger' -> 'Blueger'; 'V. Guerrero Jr.' -> 'Guerrero Jr.'."""
    toks = (name or "").replace(",", " ").split()
    if not toks:
        return ""
    if len(toks) >= 2 and toks[-1].lower() in _SUFFIXES:
        return f"{toks[-2]} {toks[-1]}"
    return toks[-1]


def _athlete_name(a):
    a = a or {}
    return last_name(a.get("shortName") or a.get("displayName") or a.get("fullName") or "")


def _to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _team_box(summary, abbr):
    for team in ((summary or {}).get("boxscore") or {}).get("players") or []:
        if ((team.get("team") or {}).get("abbreviation") or "") == abbr:
            return team.get("statistics") or []
    return []


def _leaders(summary, abbr):
    """{category_name: (athlete_last_name, displayValue)} for one team."""
    out = {}
    for team in (summary or {}).get("leaders") or []:
        if ((team.get("team") or {}).get("abbreviation") or "") != abbr:
            continue
        for cat in team.get("leaders") or []:
            top = (cat.get("leaders") or [{}])[0]
            nm = _athlete_name(top.get("athlete"))
            if nm and top.get("displayValue"):
                out[cat.get("name", "")] = (nm, str(top.get("displayValue")))
    return out


def _stat_rows(group):
    labels = group.get("labels") or []
    ix = {lab: i for i, lab in enumerate(labels)}
    for a in group.get("athletes") or []:
        stats = a.get("stats") or []

        def val(lab, stats=stats):
            i = ix.get(lab)
            return stats[i] if i is not None and i < len(stats) else ""
        yield _athlete_name(a.get("athlete")), val


# --------------------------------------------------------------------- #
# Top performers of a final                                              #
# --------------------------------------------------------------------- #

def stars_from_summary(summary, league, abbr):
    """Up to three short top-performer strings for OUR team's game."""
    if not summary:
        return []
    league = (league or "").upper()
    try:
        if league == "MLB":
            return _mlb_stars(summary, abbr)
        if league == "NHL":
            return _nhl_stars(summary, abbr)
        if league == "NFL":
            return _nfl_stars(summary, abbr)
        if league == "NBA":
            return _nba_stars(summary, abbr)
    except Exception:
        return []
    return []


def _mlb_stars(summary, abbr):
    parts = []
    comp = (((summary.get("header") or {}).get("competitions") or [{}])[0])
    feat = {fa.get("name"): _athlete_name(fa.get("athlete"))
            for fa in ((comp.get("status") or {}).get("featuredAthletes") or [])}
    for key, lab in (("winningPitcher", "W"), ("losingPitcher", "L"), ("savingPitcher", "S")):
        if feat.get(key):
            parts.append(f"{lab}: {feat[key]}")
    best = None
    for group in _team_box(summary, abbr):
        if "H-AB" not in (group.get("labels") or []):
            continue
        for name, val in _stat_rows(group):
            hr, rbi, h = _to_int(val("HR")), _to_int(val("RBI")), _to_int(val("H"))
            score = hr * 4 + rbi * 2 + h
            if name and score and (best is None or score > best[0]):
                hits, _, ab = str(val("H-AB")).partition("-")
                extra = []
                if hr:
                    extra.append("HR" if hr == 1 else f"{hr} HR")
                if rbi:
                    extra.append(f"{rbi} RBI")
                line = f"{name} {hits}-for-{ab}" if ab else name
                if extra:
                    line += ", " + ", ".join(extra)
                best = (score, line)
    if best:
        parts.append(best[1])
    return parts[:4]


def _nhl_stars(summary, abbr):
    goals, goalie = [], None
    for group in _team_box(summary, abbr):
        labels = group.get("labels") or []
        if "SV" in labels:
            for name, val in _stat_rows(group):
                sv = _to_int(val("SV"))
                if name and sv and (goalie is None or sv > goalie[1]):
                    goalie = (name, sv)
        elif "G" in labels:
            for name, val in _stat_rows(group):
                g = _to_int(val("G"))
                if name and g > 0:
                    goals.append((g, name))
    parts = []
    if goals:
        goals.sort(key=lambda x: -x[0])
        parts.append("Goals: " + ", ".join(f"{n} ({g})" if g > 1 else n for g, n in goals[:4]))
    if goalie:
        parts.append(f"{goalie[0]} {goalie[1]} saves")
    return parts


def _nfl_stars(summary, abbr):
    ld = _leaders(summary, abbr)
    parts = []

    def yds(dv):
        m = re.search(r"(-?\d+)\s*YDS", dv, re.I)
        return m.group(1) if m else ""

    def tds(dv):
        m = re.search(r"(\d+)\s*TD", dv, re.I)
        return int(m.group(1)) if m else 0
    if "passingYards" in ld:
        n, dv = ld["passingYards"]
        y, t = yds(dv), tds(dv)
        if y:
            parts.append(f"{n} {y} pass yds" + (f", {t} TD" if t else ""))
    for key, lab in (("rushingYards", "rush"), ("receivingYards", "rec")):
        if key in ld:
            n, dv = ld[key]
            y, t = yds(dv), tds(dv)
            if y:
                parts.append(f"{n} {y} {lab} yds" + (f", {t} TD" if t else ""))
    return parts[:3]


def _nba_stars(summary, abbr):
    ld = _leaders(summary, abbr)
    parts = []
    for key, lab in (("points", "pts"), ("rebounds", "reb"), ("assists", "ast")):
        if key in ld:
            n, dv = ld[key]
            num = re.match(r"\s*(\d+)", dv)
            if num:
                parts.append(f"{n} {num.group(1)} {lab}")
    return parts


# --------------------------------------------------------------------- #
# Game-day preview                                                       #
# --------------------------------------------------------------------- #

def _probable_line(competitor):
    p = ((competitor or {}).get("probables") or [{}])[0]
    name = _athlete_name(p.get("athlete"))
    if not name:
        return ""
    cats = {}
    for c in (((p.get("statistics") or {}).get("splits") or {}).get("categories") or []):
        cats[c.get("abbreviation")] = c.get("displayValue")
    w, l, era = cats.get("W"), cats.get("L"), cats.get("ERA")
    if w is not None and l is not None and era:
        return f"{name} ({w}-{l}, {era} ERA)"
    return name


INJURY_ORDER = ("Out", "Doubtful", "Questionable", "Day-To-Day")


def preview_from_summary(summary, league, abbr):
    """Game-day notes for OUR team: probable pitchers (MLB) and the injury
    report (NFL/NHL/NBA). MLB injured lists are season-long, so skipped."""
    if not summary:
        return []
    league = (league or "").upper()
    out = []
    try:
        comp = (((summary.get("header") or {}).get("competitions") or [{}])[0])
        comps = comp.get("competitors") or []
        us = next((c for c in comps if ((c.get("team") or {}).get("abbreviation")) == abbr), None)
        them = next((c for c in comps if c is not us), None)
        if league == "MLB":
            a, b = _probable_line(us), _probable_line(them)
            if a or b:
                out.append(f"Probables: {a or 'TBD'} vs. {b or 'TBD'}")
            return out
        for team in summary.get("injuries") or []:
            if ((team.get("team") or {}).get("abbreviation") or "") != abbr:
                continue
            by_status = {}
            for inj in team.get("injuries") or []:
                status = (inj.get("status") or "").strip()
                name = _athlete_name(inj.get("athlete"))
                kind = ((inj.get("details") or {}).get("type") or "").strip().lower()
                if not name or status not in INJURY_ORDER:
                    continue
                by_status.setdefault(status, []).append(f"{name} ({kind})" if kind else name)
            for status in INJURY_ORDER:
                names = by_status.get(status) or []
                if names:
                    label = "Day-to-day" if status == "Day-To-Day" else status
                    more = f" +{len(names) - 4} more" if len(names) > 4 else ""
                    out.append(f"{label}: {', '.join(names[:4])}{more}")
    except Exception:
        return out
    return out[:3]


def goalies_from_dfo(games, team_full_name):
    """'Goalies: Bobrovsky vs. Dobes (confirmed)' from Daily Faceoff's
    starting-goalies page data, or '' if our game isn't listed."""
    for g in games or []:
        home, away = g.get("homeTeamName", ""), g.get("awayTeamName", "")
        if team_full_name not in (home, away):
            continue
        side, other = ("home", "away") if home == team_full_name else ("away", "home")
        ours = last_name(g.get(f"{side}GoalieName") or "")
        theirs = last_name(g.get(f"{other}GoalieName") or "")
        if not ours:
            return ""
        status = (g.get(f"{side}NewsStrengthName") or "").strip().lower()
        tag = {"confirmed": "confirmed", "likely": "likely"}.get(status, "projected")
        return f"Goalies: {ours} vs. {theirs or 'TBD'} ({tag})"
    return ""
