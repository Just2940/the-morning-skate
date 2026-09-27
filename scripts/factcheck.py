"""Number grounding for AI-written text (Lay of the Land, Morning Brief).

The worst errors the app has shipped were confident, wrong numbers: "stretched
the skid to four straight" (it was two), "5 points behind in the standings"
(exhibition standings). Upstream facts are fixed, but a language model can
still invent a number. Every claim of these kinds must appear in the verified
facts the model was given, or the draft is rejected and regenerated:

  - scores and records     "5-1", "78-83", "32-36-14" (either order for pairs)
  - streak counts          "four straight", "3-game losing streak", "skid to four"
  - games/points back      "20 games back", "five points behind"
  - standings positions    "5th in the AL East", "fifth in the Atlantic"

Deliberately NOT checked: player stat lines, contract figures, dates, times -
they are either verified elsewhere or too varied to check without false
alarms, and a rejected draft costs a regeneration.
"""
import re

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_ORD_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_N = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"

_PAIR_RX = re.compile(r"(?<![\d$./:])(\d{1,3})-(\d{1,3})(?:-(\d{1,3}))?(?![\d%/:])")
_STREAK_RXS = [
    re.compile(r"\b" + _N + r"[- ](?:game[- ])?(?:straight|consecutive)\b", re.I),
    re.compile(r"\b" + _N + r"[- ]game (?:win(?:ning)?|los(?:ing|s)|unbeaten|winless|skid|slide|streak)", re.I),
    re.compile(r"\b(?:skid|streak|slide|run) (?:of|to|at|stretched to) " + _N + r"\b", re.I),
    re.compile(r"\b" + _N + r" (?:wins|losses|games) in a row\b", re.I),
]
_BACK_RX = re.compile(r"\b(\d{1,2}(?:\.\d)?|" + "|".join(_NUM_WORDS) + r")\s+(?:games?|points?)\s+(?:back|behind|out)\b", re.I)
_DIV = r"(AL|NL|NFC|AFC|American|National|Atlantic|Metropolitan|Central|Pacific|Eastern|Western|East|West|North|South)"
_ORD_RX = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)(?:\s+place)?\s+in\s+(?:the\s+)?" + _DIV, re.I)
_ORD_WORD_RX = re.compile(r"\b(" + "|".join(_ORD_WORDS) + r")(?:\s+place)?\s+in\s+(?:the\s+)?" + _DIV, re.I)


def _n(tok):
    tok = (tok or "").lower()
    if tok in _NUM_WORDS:
        return float(_NUM_WORDS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


def _plain(text):
    return re.sub(r"<[^>]+>", " ", text or "")


def _pairs(text):
    two, three = set(), set()
    for a, b, c in _PAIR_RX.findall(text):
        if c:
            three.add((int(a), int(b), int(c)))
        else:
            two.add(frozenset((int(a), int(b))) if a != b else frozenset((int(a),)))
    return two, three


def _streaks(text):
    found = set()
    for rx in _STREAK_RXS:
        for m in rx.finditer(text):
            v = _n(m.group(1))
            if v is not None:
                found.add(v)
    return found


def _allowed_streaks(corpus):
    ok = _streaks(corpus)
    for m in re.finditer(r"CURRENT RUN:\s*(\d+)", corpus):
        ok.add(float(m.group(1)))
    for m in re.finditer(r"\b[WL](\d{1,2})\b", corpus):
        ok.add(float(m.group(1)))
    return ok


def _backs(text):
    return {v for v in (_n(m.group(1)) for m in _BACK_RX.finditer(text)) if v is not None}


def _allowed_backs(corpus):
    ok = _backs(corpus)
    for line in corpus.splitlines():
        if re.search(r"back|behind|\bGB\b", line, re.I):
            ok |= {float(x) for x in re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d)?)(?![\d.])", line)}
    return ok


def _ordinals(text):
    out = set()
    for m in _ORD_RX.finditer(text):
        out.add((int(m.group(1)), m.group(2).lower()))
    for m in _ORD_WORD_RX.finditer(text):
        out.add((_ORD_WORDS[m.group(1).lower()], m.group(2).lower()))
    return out


def ungrounded_numbers(text, corpus):
    """Claims in `text` that the verified `corpus` does not support.
    Returns a list of short descriptions; empty means grounded."""
    text, corpus = _plain(text), _plain(corpus)
    problems = []

    two_ok, three_ok = _pairs(corpus)
    two, three = _pairs(text)
    for p in sorted(two, key=lambda s: sorted(s)):
        if p not in two_ok:
            problems.append("score/record " + "-".join(str(x) for x in sorted(p, reverse=True)))
    for t in sorted(three):
        if t not in three_ok:
            problems.append("record " + "-".join(str(x) for x in t))

    streak_ok = _allowed_streaks(corpus)
    for v in sorted(_streaks(text)):
        if v not in streak_ok:
            problems.append(f"streak of {int(v)}")

    back_ok = _allowed_backs(corpus)
    for v in sorted(_backs(text)):
        if v not in back_ok:
            problems.append(f"{v:g} back")

    ord_ok = _ordinals(corpus)
    for n, div in sorted(_ordinals(text)):
        if (n, div) not in ord_ok:
            sfx = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            problems.append(f"{n}{sfx} in {div}")
    return problems
