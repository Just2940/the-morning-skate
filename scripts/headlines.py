"""Headlines for the Morning Brief.

Every edition gets a real, specific news headline - never a label such as
"Sunday's Skate". Three layers, in order:

  1. The AI article's own headline, if it passes headline_problem().
  2. A headline-only rewrite from the AI (update-content.py), same check.
  3. fact_headline(): built from the verified scoreboard, slate and schedule,
     so it is always true ("Scherzer Starts Jays' Season Finale").

headline_problem() rejects generic labels, lists of team names, questions,
drought talk, numbers that aren't in the verified facts, headlines that name
no team or player, and garbled words ("McKenna Choves Number 92"). A word is
accepted when it is English (words_en.txt.gz, SCOWL), sports vocabulary, or
appears in the verified facts or the article itself - so player names pass
and invented words don't.
"""
import gzip
import os
import re

from factcheck import ungrounded_numbers

_HERE = os.path.dirname(os.path.abspath(__file__))
_LEXICON = None

# Sports vocabulary and team names the general dictionary lacks
SPORTS_WORDS = set("""
vs ot qb qbs td tds ir nhl mlb nba nfl al nl afc nfc gm mvp era rbi rbis hr hrs gaa
walkoff outduel outduels outdueled outduelled netminder netminders homestand roadtrip
callup callups preseason postseason offseason powerplay faceoff faceoffs backcheck
forecheck blueliner blueliners barnburner nailbiter dinger dingers punchout righty
wideout wideouts cornerbacks tuneup
habs sens bolts canes pens isles caps wings bucs cavs mavs mets nats niners phils sox
sixers yanks pats jags
astros blackhawks canadiens flyers knicks lakers phillies sabres seahawks steelers
timberwolves kraken oilers canucks predators hurricanes islanders penguins avalanche
orioles mariners diamondbacks rockies padres dodgers yankees brewers marlins nationals
guardians athletics angels royals twins tigers rays braves cubs pirates reds cardinals
giants rangers celtics nets hornets pistons pacers nuggets clippers grizzlies pelicans
spurs thunder blazers warriors suns rockets bucks bulls cavaliers wizards hawks heat
magic jazz kings raptors leafs jays commanders bruins capitals devils senators flames
jets kings ducks sharks wild blues stars lightning panthers ravens bengals browns
texans colts titans chiefs raiders chargers broncos rams packers bears lions vikings
saints falcons dolphins bills eagles cowboys patriots jaguars buccaneers mammoth
""".split())

GENERIC_RX = re.compile(
    r"\b(update|updates|roundup|round-up|recap|recaps|briefing|brief|digest|edition|"
    r"notebook|skate|morning|headlines|what to watch|what to know|sports scene|sports day|"
    r"around the league|this week in)\b", re.I)
TEAM_WORDS = ("leafs", "jays", "raptors", "commanders", "toronto", "washington")
_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "if", "in", "nor", "of",
          "on", "or", "the", "to", "vs", "vs.", "via"}
_WEEKDAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
             "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def lexicon():
    global _LEXICON
    if _LEXICON is None:
        try:
            with gzip.open(os.path.join(_HERE, "words_en.txt.gz"), "rt", encoding="utf-8") as fh:
                _LEXICON = set(fh.read().split())
        except OSError:
            _LEXICON = set()  # fail closed: unknown words then need the facts/article
        _LEXICON |= SPORTS_WORDS
    return _LEXICON


def is_english(word):
    w = word.lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    parts = [p for p in w.split("-") if p]
    lex = lexicon()
    return bool(parts) and all(p in lex or len(p) == 1 for p in parts)


def clean_headline(text):
    t = re.sub(r"\[\d+\]", "", str(text or ""))
    t = re.sub(r"^\s*(headline|title)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", t)
    t = t.strip().strip("#*_\"'`").strip()
    t = re.sub(r"\s+", " ", t)
    return t.rstrip(".").strip()


def headline_case(text):
    """Newspaper title case; leaves scores, acronyms and names like McLaurin alone."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if any(c.isdigit() for c in w) or any(c.isupper() for c in w[1:]):
            out.append(w)
        elif 0 < i < len(words) - 1 and w.lower() in _SMALL:
            out.append(w.lower())
        else:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out)


def headline_problem(title, facts, body="", banned_rx=None):
    """'' if the headline is publishable, else a short reason."""
    t = clean_headline(title)
    n = len(t.split())
    if not 3 <= n <= 10:
        return f"{n} words"
    if len(t) > 75:
        return "too long"
    if "?" in t or ":" in t:
        return "question or colon"
    low = t.lower()
    if GENERIC_RX.search(low):
        return "generic label"
    if sum(1 for w in ("leafs", "jays", "raptors", "commanders") if w in low) >= 3:
        return "list of teams"
    if banned_rx is not None and banned_rx.search(t):
        return "drought talk"
    nums = ungrounded_numbers(t, facts)
    if nums:
        return f"unverified number {nums[0]}"
    facts_low, body_low = (facts or "").lower(), (body or "").lower()

    def seen(w):
        rx = r"\b" + re.escape(w) + r"\b"
        return bool(re.search(rx, facts_low) or re.search(rx, body_low))
    specific = any(re.search(rf"\b{w}\b", low) for w in TEAM_WORDS)
    for tok in re.findall(r"[A-Za-z][A-Za-z'-]*", t):
        w = tok.lower().strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) < 2:
            continue
        english = is_english(w)
        if not english and not seen(w):
            return f"unrecognized word '{tok}'"
        if not english and len(w) >= 4 and re.search(r"\b" + re.escape(w) + r"\b", facts_low):
            specific = True  # a name from the verified facts (Scherzer, Seahawks)
    if not specific:
        return "names no team or player"
    return ""


def _opp(matchup):
    m = re.search(r" (vs\.|at) (.+)$", matchup or "")
    return (m.group(1) == "vs.", m.group(2).strip()) if m else (None, "")


def fact_headline(scoreboard, slate, nick, openers=None, news=None, today="", yest=""):
    """A true headline from verified data, or '' if there is nothing to say.

    scoreboard: [{team, result, team_score, opp_score, opp_name, game_date, preseason}]
    slate:      [{team, matchup: "Jays vs. Reds", detail: "3:07 PM ET - Season finale",
                  off, preview}]
    nick:       {team: "Jays"} in display order
    openers:    {team: {"day": "Tue 9/29", "opp": "vs. Canadiens"}} (regular season
                opens within three days)
    news:       {team: "a reputable outlet's headline"}
    """
    order = {t: i for i, t in enumerate(nick)}
    cands = []
    for s in slate or []:
        t = s.get("team")
        if s.get("off") or t not in nick:
            continue
        home, opp = _opp(s.get("matchup"))
        if not opp:
            continue
        n = nick[t]
        detail = s.get("detail") or ""
        note = detail.split(" - ", 1)[1].strip().lower() if " - " in detail else ""
        prev = s.get("preview") if isinstance(s.get("preview"), dict) else {}
        duel = prev.get("duel") or {}
        starter = (duel.get("ours") or {}).get("name") if duel.get("label") == "Probable starters" else ""
        starter = "" if starter in (None, "", "TBD") else starter
        where = "Host" if home else "Visit"
        if note in ("season finale", "season opener"):
            kind = note.split()[1].capitalize()
            txt = (f"{starter} Starts {n}' Season {kind}" if starter else
                   f"{n} Close Out Season Against {opp}" if kind == "Finale" else
                   f"{n} Open Season Against {opp}")
            cands.append((95, order[t], txt))
        elif note == "playoffs":
            cands.append((90, order[t], f"{n} {where} {opp} in Playoffs"))
        elif note == "preseason":
            cands.append((40, order[t], f"{n} {where} {opp} in Preseason"))
        else:
            cands.append((65, order[t], f"{starter} Starts as {n} {where} {opp}" if starter
                          else f"{n} {where} {opp}"))
    for g in scoreboard or []:
        t = g.get("team")
        if t not in nick or g.get("game_date") not in (today, yest):
            continue
        try:
            a, b = int(g.get("team_score")), int(g.get("opp_score"))
        except (TypeError, ValueError):
            continue
        n, opp = nick[t], g.get("opp_name") or "Opponent"
        hi, lo = max(a, b), min(a, b)
        res = g.get("result")
        if g.get("preseason"):
            txt = f"{n} Beat {opp} in Preseason" if res == "W" else f"{n} Fall to {opp} in Preseason"
            cands.append((50, order[t], txt))
        else:
            txt = (f"{n} Beat {opp} {hi}-{lo}" if res == "W" else
                   f"{n} Fall to {opp} {hi}-{lo}" if res == "L" else f"{n} and {opp} Tie {hi}-{lo}")
            cands.append((85, order[t], txt))
    for t, op in (openers or {}).items():
        if t in nick and op:
            day = _WEEKDAYS.get(str(op.get("day", ""))[:3], "")
            opp = re.sub(r"^(vs\.|at)\s+", "", op.get("opp") or "").strip()
            if day and opp:
                cands.append((70, order[t], f"{nick[t]} Open Season {day} Against {opp}"))
    for t, h in (news or {}).items():
        h = clean_headline(h)
        if t in nick and h and 3 <= len(h.split()) <= 14:
            cands.append((30, order[t], h))
    if not cands:
        return ""
    cands.sort(key=lambda c: (-c[0], c[1]))
    best = cands[0]
    return best[2] if best[0] == 30 else headline_case(best[2])
