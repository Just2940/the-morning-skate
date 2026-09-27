"""Game details for The Morning Skate: who starred in a final, and what to
know before a game. Pure functions over ESPN game-summary JSON (and Daily
Faceoff's starting-goalie data) so they can be unit-tested with fixtures.
Deterministic - no AI. Missing data yields [] / {} / None, never an error.

Structured output (rendered as designed components by the page and email):
  stars:   [{"tag": "W", "tone": "win", "name": "Lowder", "stat": "6.0 IP, 0 ER, 2 K"}, ...]
           tone: win | loss | save | "" (neutral stat tag)
  preview: {"duel": {"label": "Probable starters", "status": "confirmed"|"likely"|"projected"|None,
                     "ours": {"name", "sub"}, "theirs": {"name", "sub"}},
            "injuries": [{"status": "Out", "tone": "out", "players": [{"name", "note"}], "more": 1}]}
Text versions (brief facts, plain-text email): stars_text(), preview_text().
All strings are plain ASCII.
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


def _team_groups(summary, abbr=None):
    """[(team_abbr, stat_group)] from the box score (optionally one team)."""
    out = []
    for team in ((summary or {}).get("boxscore") or {}).get("players") or []:
        ab = (team.get("team") or {}).get("abbreviation") or ""
        if abbr and ab != abbr:
            continue
        for group in team.get("statistics") or []:
            out.append((ab, group))
    return out


def _rows(group):
    labels = group.get("labels") or []
    ix = {lab: i for i, lab in enumerate(labels)}
    for a in group.get("athletes") or []:
        stats = a.get("stats") or []

        def val(lab, stats=stats):
            i = ix.get(lab)
            return stats[i] if i is not None and i < len(stats) else ""
        yield _athlete_name(a.get("athlete")), val


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


def _star(tag, name, stat="", tone=""):
    return {"tag": tag, "tone": tone, "name": name, "stat": stat}


# --------------------------------------------------------------------- #
# Top performers of a final                                              #
# --------------------------------------------------------------------- #

def stars_from_summary(summary, league, abbr):
    """Up to four top performers for OUR team's final (structured)."""
    if not summary:
        return []
    fn = {"MLB": _mlb_stars, "NHL": _nhl_stars, "NFL": _nfl_stars, "NBA": _nba_stars}.get((league or "").upper())
    if not fn:
        return []
    try:
        return fn(summary, abbr)
    except Exception:
        return []


def _mlb_stars(summary, abbr):
    comp = (((summary.get("header") or {}).get("competitions") or [{}])[0])
    feats = [(fa.get("name"), _athlete_name(fa.get("athlete")))
             for fa in ((comp.get("status") or {}).get("featuredAthletes") or [])]
    # Pitching lines for everyone, so decision pitchers get a real stat line
    lines = {}
    for team_ab, group in _team_groups(summary):
        if "IP" not in (group.get("labels") or []):
            continue
        for name, val in _rows(group):
            if name:
                lines[name] = (team_ab, f"{val('IP')} IP, {_to_int(val('ER'))} ER, {_to_int(val('K'))} K")
    decisions = []
    for key, tag, tone in (("winningPitcher", "W", "win"), ("losingPitcher", "L", "loss"),
                           ("savingPitcher", "SV", "save")):
        for fname, pname in feats:
            if fname == key and pname:
                team_ab, line = lines.get(pname, ("", ""))
                decisions.append((0 if team_ab == abbr else 1, _star(tag, pname, line, tone)))
    decisions.sort(key=lambda d: d[0])  # our pitcher first
    parts = [d[1] for d in decisions]
    best = None
    for _ab, group in _team_groups(summary, abbr):
        if "H-AB" not in (group.get("labels") or []):
            continue
        for name, val in _rows(group):
            hr, rbi, h = _to_int(val("HR")), _to_int(val("RBI")), _to_int(val("H"))
            score = hr * 4 + rbi * 2 + h
            if name and score and (best is None or score > best[0]):
                hits, _, ab = str(val("H-AB")).partition("-")
                extra = []
                if hr:
                    extra.append("HR" if hr == 1 else f"{hr} HR")
                if rbi:
                    extra.append(f"{rbi} RBI")
                stat = f"{hits}-for-{ab}" if ab else ""
                if extra:
                    stat = ", ".join([stat] + extra) if stat else ", ".join(extra)
                best = (score, _star("BAT", name, stat))
    if best:
        parts.append(best[1])
    return parts[:4]


def _nhl_stars(summary, abbr):
    skaters, goalie = [], None
    for _ab, group in _team_groups(summary, abbr):
        labels = group.get("labels") or []
        if "SV" in labels:
            for name, val in _rows(group):
                sv, sa = _to_int(val("SV")), _to_int(val("SA"))
                if name and sv and (goalie is None or sv > goalie[1]):
                    goalie = (name, sv, sa)
        elif "G" in labels:
            for name, val in _rows(group):
                g, a = _to_int(val("G")), _to_int(val("A"))
                if name and (g or a):
                    skaters.append((g, a, name))
    parts = []
    skaters.sort(key=lambda x: (-x[0], -x[1]))
    # "GOAL" for scorers and "SV" for the goalie: in hockey a bare "G" reads as
    # the goalie's position, so it can't also mean "scored".
    for g, a, name in [s for s in skaters if s[0] > 0][:3]:
        parts.append(_star("GOAL", name, f"{g}G, {a}A" if a else f"{g}G"))
    if goalie:
        name, sv, sa = goalie
        pct = f", {sv / sa:.3f}".replace("0.", ".") if sa else ""
        parts.append(_star("SV", name, f"{sv} saves{pct}", "save"))
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
    for key, tag in (("passingYards", "PASS"), ("rushingYards", "RUSH"), ("receivingYards", "REC")):
        if key in ld:
            n, dv = ld[key]
            y, t = yds(dv), tds(dv)
            if y:
                parts.append(_star(tag, n, f"{y} yds" + (f", {t} TD" if t else "")))
    return parts[:3]


def _nba_stars(summary, abbr):
    ld = _leaders(summary, abbr)
    parts = []
    for key, tag, unit in (("points", "PTS", "pts"), ("rebounds", "REB", "reb"), ("assists", "AST", "ast")):
        if key in ld:
            n, dv = ld[key]
            num = re.match(r"\s*(\d+)", dv)
            if num:
                parts.append(_star(tag, n, f"{num.group(1)} {unit}"))
    return parts


def stars_text(stars):
    """['W: Lowder (6.0 IP, 0 ER, 2 K)', ...] for prompts and plain text."""
    out = []
    for s in stars or []:
        if isinstance(s, str):
            out.append(s)
            continue
        tag, name, stat = s.get("tag", ""), s.get("name", ""), s.get("stat", "")
        out.append(f"{tag}: {name}" + (f" ({stat})" if stat else ""))
    return out


# --------------------------------------------------------------------- #
# Game-day preview                                                       #
# --------------------------------------------------------------------- #

def _probable(competitor):
    p = ((competitor or {}).get("probables") or [{}])[0]
    name = _athlete_name(p.get("athlete"))
    if not name:
        return None
    cats = {}
    for c in (((p.get("statistics") or {}).get("splits") or {}).get("categories") or []):
        cats[c.get("abbreviation")] = c.get("displayValue")
    w, l, era = cats.get("W"), cats.get("L"), cats.get("ERA")
    sub = f"{w}-{l}, {era} ERA" if (w is not None and l is not None and era) else ""
    return {"name": name, "sub": sub}


INJURY_ORDER = (("Out", "out"), ("Doubtful", "doubtful"), ("Questionable", "questionable"),
                ("Day-To-Day", "dtd"))


def preview_from_summary(summary, league, abbr):
    """Game-day preview for OUR team: probable pitchers (MLB) and the injury
    report (NFL/NHL/NBA). MLB injured lists are season-long, so skipped."""
    out = {}
    if not summary:
        return out
    league = (league or "").upper()
    try:
        comp = (((summary.get("header") or {}).get("competitions") or [{}])[0])
        comps = comp.get("competitors") or []
        us = next((c for c in comps if ((c.get("team") or {}).get("abbreviation")) == abbr), None)
        them = next((c for c in comps if c is not us), None)
        if league == "MLB":
            a, b = _probable(us), _probable(them)
            if a or b:
                out["duel"] = {"label": "Probable starters", "status": None,
                               "ours": a or {"name": "TBD", "sub": ""},
                               "theirs": b or {"name": "TBD", "sub": ""}}
            return out
        groups = []
        for team in summary.get("injuries") or []:
            if ((team.get("team") or {}).get("abbreviation") or "") != abbr:
                continue
            by_status = {}
            for inj in team.get("injuries") or []:
                status = (inj.get("status") or "").strip()
                name = _athlete_name(inj.get("athlete"))
                note = ((inj.get("details") or {}).get("type") or "").strip().lower()
                if name and status in dict(INJURY_ORDER):
                    by_status.setdefault(status, []).append({"name": name, "note": note})
            for status, tone in INJURY_ORDER:
                players = by_status.get(status) or []
                if players:
                    groups.append({"status": "Day-to-day" if status == "Day-To-Day" else status,
                                   "tone": tone, "players": players[:4], "more": max(0, len(players) - 4)})
        if groups:
            out["injuries"] = groups[:3]
    except Exception:
        return out
    return out


def goalies_from_dfo(games, team_full_name):
    """Goalie duel from Daily Faceoff's starting-goalies data, or None."""
    for g in games or []:
        home, away = g.get("homeTeamName", ""), g.get("awayTeamName", "")
        if team_full_name not in (home, away):
            continue
        side, other = ("home", "away") if home == team_full_name else ("away", "home")

        def goalie(pfx):
            name = last_name(g.get(f"{pfx}GoalieName") or "")
            if not name:
                return None
            w, l, otl = (_to_int(g.get(f"{pfx}Goalie{k}")) for k in ("Wins", "Losses", "OvertimeLosses"))
            gaa = str(g.get(f"{pfx}GoalieGoalsAgainstAvg") or "").strip()
            sub = f"{w}-{l}-{otl}" + (f", {gaa} GAA" if gaa and gaa not in ("0", "0.0", "0.00") else "") \
                if (w or l or otl) else ""
            return {"name": name, "sub": sub}
        ours = goalie(side)
        if not ours:
            return None
        status = (g.get(f"{side}NewsStrengthName") or "").strip().lower()
        return {"label": "In goal", "status": status if status in ("confirmed", "likely") else "projected",
                "ours": ours, "theirs": goalie(other) or {"name": "TBD", "sub": ""}}
    return None


def preview_text(preview):
    """Plain-text lines for prompts and the text email."""
    if isinstance(preview, list):  # legacy string form
        return [str(x) for x in preview]
    out = []
    d = (preview or {}).get("duel")
    if d:
        def side(s):
            return s.get("name", "TBD") + (f" ({s['sub']})" if s.get("sub") else "")
        tag = f" ({d['status']})" if d.get("status") else ""
        out.append(f"{d.get('label', 'Matchup')}: {side(d.get('ours') or {})} vs. {side(d.get('theirs') or {})}{tag}")
    for grp in (preview or {}).get("injuries") or []:
        names = ", ".join(p["name"] + (f" ({p['note']})" if p.get("note") else "") for p in grp.get("players") or [])
        more = f" +{grp['more']} more" if grp.get("more") else ""
        out.append(f"{grp.get('status')}: {names}{more}")
    return out
