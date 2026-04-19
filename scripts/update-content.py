#!/usr/bin/env python3
"""
The Morning Skate ‚Äî Daily Update Script
Runs at 4:00 AM EST via GitHub Actions.
Fetches scores, standings, news, and AI-generated editorials.
Writes data.json for the front-end to render.
"""

import json
import os
import re
import sys
import time
import html
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote_plus, urlparse, parse_qs
import base64


# === GOOGLE NEWS URL RESOLVER ===
def resolve_google_news_url(google_url, timeout=10):
    """Resolve a Google News wrapper URL to the actual article URL.
    Google News RSS feeds wrap article URLs in redirect links.
    This function follows the redirect to get the real destination.
    Falls back to trying to decode the URL from the path if redirect fails."""
    if not google_url:
        return google_url
    # Only process Google News wrapper URLs
    parsed = urlparse(google_url)
    if "news.google" not in parsed.netloc:
        return google_url
    try:
        # Method 1: Follow the HTTP redirect
        req = Request(google_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MorningSkatBot/1.0)",
        })
        req.method = "HEAD"
        with urlopen(req, timeout=timeout) as resp:
            final_url = resp.url
            # Verify we actually got redirected away from Google (incl. googleusercontent intermediates)
            netloc = urlparse(final_url).netloc.lower()
            if not any(b in netloc for b in ("news.google", "googleusercontent", "consent.google")):
                return final_url
    except Exception:
        pass
    try:
        # Method 2: Follow with GET (some redirects need it)
        req = Request(google_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MorningSkatBot/1.0)",
        })
        with urlopen(req, timeout=timeout) as resp:
            final_url = resp.url
            netloc = urlparse(final_url).netloc.lower()
            if not any(b in netloc for b in ("news.google", "googleusercontent", "consent.google")):
                return final_url
            # Method 3: Check for meta refresh or JS redirect in body
            body = resp.read(8192).decode("utf-8", errors="ignore")
            meta_match = re.search(r'url=([^"\\s>]+)', body, re.IGNORECASE)
            if meta_match:
                candidate = meta_match.group(1).strip()
                if candidate.startswith("http") and not any(b in candidate.lower() for b in ("news.google", "googleusercontent", "consent.google")):
                    return candidate
            href_match = re.search(r'href="(https?://(?!news\.google|googleusercontent|consent\.google)[^"]+)"', body)
            if href_match:
                return href_match.group(1)
    except Exception:
        pass
    # If all methods fail, return original (validator will catch it)
    print(f"  WARNING: Could not resolve Google News URL: {google_url[:80]}...")
    return google_url

def is_publisher_url(url):
    """Return True if url is safe to ship as an article link.
    Rejects Google intermediates (news.google, consent, accounts, photos, googleusercontent CDN).
    Added 2026-04-19 after lh3.googleusercontent.com URLs slipped past resolve_google_news_url."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return False
    host = urlparse(url).netloc.lower()
    if host.endswith(".googleusercontent.com") or host == "googleusercontent.com":
        return False
    if host.endswith(".consent.google.com") or host == "consent.google.com":
        return False
    if host in ("news.google.com", "accounts.google.com", "photos.google.com"):
        return False
    return True


# === CONFIGURATION ===
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")
EST = timezone(timedelta(hours=-5))
NOW = datetime.now(EST)
TODAY = NOW.strftime("%Y-%m-%d")
TODAY_DISPLAY = NOW.strftime("%B %d, %Y").replace(" 0", " ")  # "April 14, 2026" not "April 04"

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

# Team configuration
TEAMS = {
    "leafs": {
        "full_name": "Toronto Maple Leafs",
        "league": "NHL",
        "sport": "hockey",
        "espn_sport": "hockey",
        "espn_league": "nhl",
        "espn_team_id": "21",  # Toronto Maple Leafs (verified: 17=Colorado Avalanche)
        "espn_abbr": "TOR",
        "logo": "https://a.espncdn.com/i/teamlogos/nhl/500/tor.png",
        "youtube_channel": "@NHL",
        "youtube_search_name": "Maple Leafs",
    },
    "jays": {
        "full_name": "Toronto Blue Jays",
        "league": "MLB",
        "sport": "baseball",
        "espn_sport": "baseball",
        "espn_league": "mlb",
        "espn_team_id": "14",  # Toronto Blue Jays
        "espn_abbr": "TOR",
        "logo": "https://a.espncdn.com/i/teamlogos/mlb/500/tor.png",
        "youtube_channel": "@MLB",
        "youtube_search_name": "Blue Jays",
    },
    "raptors": {
        "full_name": "Toronto Raptors",
        "league": "NBA",
        "sport": "basketball",
        "espn_sport": "basketball",
        "espn_league": "nba",
        "espn_team_id": "28",  # Toronto Raptors
        "espn_abbr": "TOR",
        "logo": "https://a.espncdn.com/i/teamlogos/nba/500/tor.png",
        "youtube_channel": "@NBA",
        "youtube_search_name": "Raptors",
    },
    "commanders": {
        "full_name": "Washington Commanders",
        "league": "NFL",
        "sport": "football",
        "espn_sport": "football",
        "espn_league": "nfl",
        "espn_team_id": "28",  # Washington Commanders
        "espn_abbr": "WSH",
        "logo": "https://a.espncdn.com/i/teamlogos/nfl/500/wsh.png",
        "youtube_channel": "@NFL",
        "youtube_search_name": "Commanders",
    },
}

# === ASCII SANITIZATION ===
# Banned dashes per Section 0.3 — replaced with ASCII equivalents.
# Everything else (curly quotes, accented chars, middle-dot, etc.) is kept as
# Unicode so it renders correctly as textContent in the frontend.
BANNED_DASH_REPLACEMENTS = {
    "\u2014": " - ",   # em-dash —
    "\u2013": " - ",   # en-dash –
}

def sanitize_ascii(text):
    """Normalize text for JSON output.

    Historical name kept for compatibility. Output is now clean UTF-8
    (not ASCII) — em-dash / en-dash are stripped per Section 0.3,
    any HTML entities in upstream text are decoded back to Unicode,
    and the result is trimmed of double-spaces.
    """
    if not isinstance(text, str):
        return text
    # Decode HTML entities from AI output / RSS (incl. numeric and named)
    text = html.unescape(text)
    # Strip banned dashes to ASCII per Section 0.3
    for ch, replacement in BANNED_DASH_REPLACEMENTS.items():
        text = text.replace(ch, replacement)
    # Collapse double-spaces introduced by dash replacement
    text = re.sub(r"  +", " ", text).strip()
    return text

def sanitize_entry(entry):
    if isinstance(entry, dict):
        return {k: sanitize_entry(v) for k, v in entry.items()}
    elif isinstance(entry, list):
        return [sanitize_entry(item) for item in entry]
    elif isinstance(entry, str):
        return sanitize_ascii(entry)
    return entry


# === HELPER FUNCTIONS ===

def is_recent_enough(recent_games, max_days=30):
    """Check if the most recent game is within max_days. Used to suppress stale offseason data."""
    if not recent_games:
        return False
    try:
        last_game_date = datetime.strptime(recent_games[0].get("game_date", ""), "%Y-%m-%d")
        days_since = (NOW.replace(tzinfo=None) - last_game_date).days
        return days_since <= max_days
    except:
        return True  # If we can't parse the date, assume it's recent


def detect_season_phase(team_key, recent, upcoming, standings=None):
    """Detect what phase of the season a team is in, based on schedule data, calendar,
    and standings data (playoff seed, clinch status).
    Returns a dict with 'phase', 'label', 'editorial_direction', and 'recency_days'."""
    cfg = TEAMS[team_key]
    league = cfg["league"]
    month = NOW.month
    day = NOW.day

    has_upcoming = bool(upcoming)
    has_recent_game = is_recent_enough(recent, max_days=14)
    last_game_days_ago = 999
    if recent:
        try:
            last_game_date = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
            last_game_days_ago = (NOW.replace(tzinfo=None) - last_game_date).days
        except:
            pass

    # Check for playoff indicators from ESPN standings
    has_playoff_seed = False
    has_clinched = False
    is_eliminated = False
    if standings:
        seed = standings.get("playoffSeed", "")
        clincher = standings.get("clincher", "")
        # NOTE: ESPN's playoffSeed is a RANKING assigned to ALL teams, not just
        # playoff qualifiers. A team 14th in the conference still gets a playoffSeed.
        # The CLINCHER field is the authoritative signal:
        #   "e" = eliminated, "x" = clinched berth, "y" = clinched division, "z" = best record
        if clincher:
            cl = str(clincher).lower().strip()
            if "e" in cl:
                is_eliminated = True
            else:
                # Positive clinch indicator (x, y, z, etc.)
                has_clinched = True
        if seed and str(seed) not in ("", "0"):
            has_playoff_seed = True
            # Override: eliminated teams' playoff seeds are just rankings, not berths
            if is_eliminated:
                has_playoff_seed = False
        print(f"    Phase inputs: playoffSeed={seed}, clincher='{clincher}', "
              f"has_clinched={has_clinched}, is_eliminated={is_eliminated}, "
              f"has_playoff_seed={has_playoff_seed}")

    if league == "NHL":
        if month >= 4 and month <= 6:
            # April-June: NHL playoff window.
            # CRITICAL: Only tag as "playoffs" if the team ACTUALLY qualified.
            # Use the clincher field as the definitive signal, NOT playoffSeed alone.
            if has_clinched:
                return _phase("playoffs", league, cfg)
            elif has_upcoming and has_recent_game:
                # Has games but no playoff seed ‚Äî still in late regular season
                return _phase("regular_season", league, cfg)
            elif has_recent_game and not has_upcoming:
                # Season just ended, no playoff berth
                return _phase("eliminated", league, cfg)
            elif last_game_days_ago <= 14:
                # Recently played but no upcoming ‚Äî season over
                return _phase("eliminated", league, cfg)
            else:
                return _phase("season_ended", league, cfg)
        elif has_upcoming and has_recent_game:
            return _phase("regular_season", league, cfg)
        elif has_recent_game and not has_upcoming:
            # Season just ended
            return _phase("season_ended", league, cfg)
        elif month >= 6 and month <= 7:
            return _phase("draft_free_agency", league, cfg)
        elif month >= 7 and month <= 9:
            return _phase("deep_offseason", league, cfg)
        elif month >= 9 and month <= 10 and not has_upcoming:
            return _phase("preseason", league, cfg)
        else:
            return _phase("offseason", league, cfg)

    elif league == "MLB":
        if has_upcoming and has_recent_game:
            return _phase("regular_season", league, cfg)
        elif month >= 2 and month <= 3:
            return _phase("spring_training", league, cfg)
        elif month >= 10 and month <= 11:
            return _phase("postseason_offseason", league, cfg)
        else:
            return _phase("offseason", league, cfg)

    elif league == "NBA":
        # NBA playoff detection: use the clincher field as the authoritative signal.
        # CRITICAL: playoffSeed is a RANKING for all teams, not a qualification flag.
        # Only use has_clinched (positive clinch indicator) to determine playoff status.
        if month >= 4 and month <= 6:
            if has_clinched:
                # Team qualified ‚Äî they're in the playoffs
                return _phase("playoffs", league, cfg)
            elif has_upcoming and has_recent_game:
                # Has games but no playoff seed ‚Äî late regular season or play-in
                return _phase("regular_season", league, cfg)
            elif has_recent_game and not has_upcoming:
                # Season just ended, no playoff berth
                return _phase("eliminated", league, cfg)
            elif last_game_days_ago <= 14:
                return _phase("eliminated", league, cfg)
            else:
                return _phase("season_ended", league, cfg)
        elif has_upcoming and has_recent_game:
            return _phase("regular_season", league, cfg)
        elif has_recent_game and not has_upcoming:
            return _phase("regular_season", league, cfg)
        elif month >= 6 and month <= 7:
            return _phase("draft_free_agency", league, cfg)
        elif month >= 7 and month <= 10:
            return _phase("deep_offseason", league, cfg)
        else:
            return _phase("offseason", league, cfg)

    elif league == "NFL":
        if has_upcoming and has_recent_game:
            if month == 1 or month == 2:
                return _phase("playoffs", league, cfg)
            return _phase("regular_season", league, cfg)
        elif month >= 2 and month <= 3:
            return _phase("combine_free_agency", league, cfg)
        elif month == 4:
            return _phase("pre_draft", league, cfg)
        elif month >= 5 and month <= 6:
            return _phase("otas", league, cfg)
        elif month >= 7 and month <= 8:
            return _phase("training_camp", league, cfg)
        else:
            return _phase("offseason", league, cfg)

    return _phase("unknown", league, cfg)


def _phase(phase_id, league, cfg):
    """Build the phase context dict with editorial direction for Perplexity prompts."""
    team_name = cfg["full_name"]
    today_str = NOW.strftime("%B %d, %Y")

    PHASE_MAP = {
        # === IN-SEASON PHASES ===
        "regular_season": {
            "label": "Regular Season",
            "recency_days": 3,
            "editorial_direction": (
                f"The {team_name} are in their regular season. "
                f"Focus on: last night's game result (if they played), current win/loss trajectory, "
                f"key player performances, injury updates, and the next upcoming game. "
                f"The tone should match where they are in the standings ‚Äî contender energy if they're in the hunt, "
                f"honest assessment if they're struggling."
            ),
        },
        "regular_season_late": {
            "label": "Late Regular Season / Playoff Push",
            "recency_days": 2,
            "editorial_direction": (
                f"The {team_name} are in the final stretch of the regular season. "
                f"Focus on: playoff implications of every game, magic numbers or elimination scenarios, "
                f"last night's result and what it means for standings, and the next game's stakes. "
                f"Urgency should be HIGH ‚Äî every game matters now."
            ),
        },
        "playoffs": {
            "label": "Playoffs",
            "recency_days": 2,
            "editorial_direction": (
                f"The {team_name} are in the PLAYOFFS. This is the highest-urgency content mode. "
                f"Focus on: last game result and series score, key player performances, "
                f"what went right/wrong, and when the next game is. "
                f"The tone should be electric ‚Äî this is what the whole season was building toward."
            ),
        },
        # === OFFSEASON PHASES ===
        "season_ended": {
            "label": "Season Over",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name}'s season has just ended. "
                f"Focus on: season recap and assessment, what went wrong or right, "
                f"key decisions ahead (coaching changes, free agency, draft positioning), "
                f"and the overall outlook. Do NOT write about individual game results as if they just happened ‚Äî "
                f"the season is OVER. Look forward, not backward."
            ),
        },
        "eliminated": {
            "label": "Eliminated / Season Over",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name} have been eliminated or their season is over. "
                f"Focus on: what went wrong, offseason priorities, draft positioning, "
                f"coaching/GM job security, and upcoming roster decisions. "
                f"Do NOT recap old game results. The page has turned ‚Äî write about what comes next."
            ),
        },
        "draft_free_agency": {
            "label": "Draft & Free Agency",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name} are in draft and free agency season. "
                f"Focus on: draft picks and analysis, free agent signings and departures, "
                f"roster construction, cap space moves, and how the offseason is reshaping the team. "
                f"Do NOT reference regular season game results ‚Äî that was months ago."
            ),
        },
        "deep_offseason": {
            "label": "Deep Offseason",
            "recency_days": 14,
            "editorial_direction": (
                f"The {team_name} are in the deep offseason ‚Äî it's quiet. "
                f"Focus on: any recent news (trades, signings, injuries), training camp timelines, "
                f"roster projections, or feature-style content about the team's direction. "
                f"Keep it brief and forward-looking. It's OK if there isn't much to say ‚Äî "
                f"a short, honest paragraph is better than padding with stale content."
            ),
        },
        "offseason": {
            "label": "Offseason",
            "recency_days": 10,
            "editorial_direction": (
                f"The {team_name} are in the offseason. "
                f"Focus on: the most recent offseason moves, upcoming events (draft, free agency, camp), "
                f"roster outlook, and what fans should be watching for. "
                f"Do NOT write about regular season game results ‚Äî the season ended months ago. "
                f"Every sentence should be about the present or future, never the past season's games."
            ),
        },
        # === NFL-SPECIFIC PHASES ===
        "pre_draft": {
            "label": "Pre-Draft",
            "recency_days": 5,
            "editorial_direction": (
                f"The NFL Draft is approaching (late April). The {team_name} are in pre-draft mode. "
                f"Today's date is {today_str}. "
                f"Focus on: mock draft analysis, the team's draft position and needs, "
                f"potential trade scenarios, free agency moves already made, "
                f"and what positions the team is targeting. "
                f"Do NOT write about regular season game results ‚Äî the NFL season ended in January. "
                f"This is entirely about the draft and roster building for next season."
            ),
        },
        "combine_free_agency": {
            "label": "Combine & Free Agency",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name} are in the NFL Combine and free agency period. "
                f"Focus on: free agent signings and departures, combine standouts, "
                f"cap space management, and early draft board positioning. "
                f"Do NOT reference game results from the previous season."
            ),
        },
        "otas": {
            "label": "OTAs & Minicamp",
            "recency_days": 10,
            "editorial_direction": (
                f"The {team_name} are in OTAs and minicamp. "
                f"Focus on: roster battles, new player integration, scheme changes, "
                f"draft pick development, and early-season storylines forming. "
                f"Do NOT reference game results from the previous season."
            ),
        },
        "training_camp": {
            "label": "Training Camp",
            "recency_days": 5,
            "editorial_direction": (
                f"The {team_name} are in training camp. "
                f"Focus on: position battles, injury updates, rookie performances, "
                f"depth chart projections, and storylines heading into the preseason. "
                f"Do NOT reference game results from the previous season."
            ),
        },
        # === OTHER ===
        "spring_training": {
            "label": "Spring Training",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name} are in Spring Training. "
                f"Focus on: roster battles, new acquisitions getting reps, "
                f"injury updates, and storylines heading into Opening Day."
            ),
        },
        "preseason": {
            "label": "Preseason",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name} are in preseason. "
                f"Focus on: roster cuts, lineup projections, new player integration, "
                f"and storylines heading into the regular season."
            ),
        },
        "postseason_offseason": {
            "label": "Postseason / Early Offseason",
            "recency_days": 7,
            "editorial_direction": (
                f"The {team_name}'s season is over. "
                f"Focus on: season assessment, offseason priorities, "
                f"front office moves, and what needs to change for next year. "
                f"Do NOT write about regular season game results as current news."
            ),
        },
        "unknown": {
            "label": "Unknown Phase",
            "recency_days": 7,
            "editorial_direction": f"Write about the {team_name}'s current situation with recent, relevant news.",
        },
    }

    phase_data = PHASE_MAP.get(phase_id, PHASE_MAP["unknown"])
    return {
        "phase": phase_id,
        "label": phase_data["label"],
        "recency_days": phase_data["recency_days"],
        "editorial_direction": phase_data["editorial_direction"],
    }


def validate_url(url, timeout=10):
    """Check if a URL returns HTTP 200. Returns True if valid, False otherwise."""
    if not url or not url.startswith("http"):
        return False
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "TheMorningSkate/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except:
        # Some servers block HEAD, try GET with minimal download
        try:
            req = Request(url, headers={"User-Agent": "TheMorningSkate/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                resp.read(1024)  # Only read first 1KB
                return resp.status == 200
        except:
            return False


def is_perplexity_failure(text):
    """Detect when Perplexity returned an apology/failure instead of real content."""
    if not text:
        return True
    failure_phrases = [
        "i don't have sufficient",
        "i cannot find",
        "i'm unable to",
        "i couldn't find",
        "no relevant search results",
        "i was unable to find",
        "insufficient information",
        "i don't have enough",
        "based on the search results provided",
        "the search results do not",
        "i cannot write this column",
        "i need more information",
        "to write a proper",
        "the single result provided",
    ]
    text_lower = text.lower()
    for phrase in failure_phrases:
        if phrase in text_lower:
            return True
    # Also catch if it's way too short (less than 80 chars of actual content)
    clean = re.sub(r'<[^>]+>', '', text).strip()
    if len(clean) < 80:
        return True
    return False


def generate_espn_fallback_lotl(team_key, team_info, recent, upcoming, phase_info):
    """Generate a basic LOTL paragraph from ESPN facts alone when Perplexity fails.
    Not as colorful as AI-generated content, but always accurate and never empty."""
    cfg = TEAMS[team_key]
    name = cfg["full_name"]
    short = name.split()[-1]  # "Leafs", "Jays", etc.
    record = team_info.get("record", "")
    standing = team_info.get("standing_summary", "")
    phase = phase_info.get("label", "")

    parts = []

    # Opening based on phase
    if "offseason" in phase_info["phase"].lower() or "draft" in phase_info["phase"].lower() or "ended" in phase_info["phase"].lower():
        parts.append(f"The {short} ({record}) finished {standing.lower() if standing else 'their season'}.")
        if phase_info["phase"] == "pre_draft":
            parts.append(f"With the NFL Draft approaching, all eyes turn to roster building and draft strategy.")
        else:
            parts.append(f"The offseason is underway, and the front office is mapping out what comes next.")
    elif recent:
        g = recent[0]
        result_word = "beat" if g["result"] == "W" else "fell to"
        parts.append(f"<strong>The {short} {result_word} the {g['opp_name']} {g['team_score']}–{g['opp_score']}</strong>, moving to {record} on the season.")
        if standing:
            parts.append(f"They sit {standing.lower()}.")
    else:
        parts.append(f"The {short} sit at {record}, {standing.lower() if standing else ''}.")

    # Streak info
    if recent and len(recent) >= 2:
        streak_type = recent[0]["result"]
        streak_count = 0
        for g in recent:
            if g["result"] == streak_type:
                streak_count += 1
            else:
                break
        if streak_count >= 2:
            word = "wins" if streak_type == "W" else "losses"
            parts.append(f"That’s {streak_count} straight {word}.")

    # Next game
    if upcoming:
        ng = upcoming[0]
        parts.append(f"<strong>Next up: {ng['opp']} on {ng['day']} at {ng['time']}.</strong>")
    elif "offseason" in phase_info["phase"].lower() or "draft" in phase_info["phase"].lower():
        parts.append(f"<strong>Stay tuned as the offseason develops.</strong>")

    return " ".join(parts)


# === ESPN API HELPERS ===

def espn_fetch(url):
    """Fetch from ESPN's public API."""
    try:
        req = Request(url, headers={"User-Agent": "TheMorningSkate/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError) as e:
        print(f"  WARNING: ESPN fetch failed for {url}: {e}")
        return None


def get_team_info(team_key):
    """Fetch team record and standing summary from ESPN team endpoint.
    Returns dict with 'record', 'standing_summary', and raw 'record_stats'."""
    cfg = TEAMS[team_key]
    url = f"https://site.api.espn.com/apis/site/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/teams/{cfg['espn_team_id']}"
    data = espn_fetch(url)
    if not data:
        return {}

    team = data.get("team", {})
    result = {}

    # Extract record summary (e.g., "32-36-14" for NHL, "8-5" for MLB)
    record_items = team.get("record", {}).get("items", [])
    for item in record_items:
        if item.get("type") == "total":
            result["record"] = item.get("summary", "")
            # Extract individual stats
            stats = {}
            for s in item.get("stats", []):
                stats[s.get("name", "")] = s.get("value", "")
                stats[s.get("name", "") + "_display"] = s.get("displayValue", "")
            result["record_stats"] = stats
            break

    # Standing summary (e.g., "4th in Atlantic Division")
    result["standing_summary"] = team.get("standingSummary", "")

    return result


def get_team_schedule(team_key):
    """Get recent and upcoming games from ESPN."""
    cfg = TEAMS[team_key]
    url = f"https://site.api.espn.com/apis/site/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/teams/{cfg['espn_team_id']}/schedule"
    data = espn_fetch(url)
    if not data:
        return [], []

    recent = []
    upcoming = []
    today_str = NOW.strftime("%Y-%m-%dT")

    for event in data.get("events", []):
        game_date_str = event.get("date", "")
        game_date = game_date_str[:10] if game_date_str else ""

        comp = event.get("competitions", [{}])[0]
        status_type = comp.get("status", {}).get("type", {}).get("name", "")

        if status_type == "STATUS_FINAL":
            # Parse completed game
            teams_data = comp.get("competitors", [])
            our_team = None
            opp_team = None
            for t in teams_data:
                if t.get("id") == cfg["espn_team_id"] or t.get("team", {}).get("abbreviation") == cfg["espn_abbr"]:
                    our_team = t
                else:
                    opp_team = t

            if our_team and opp_team:
                our_score = int(our_team.get("score", {}).get("value", our_team.get("score", 0)))
                opp_score = int(opp_team.get("score", {}).get("value", opp_team.get("score", 0)))
                result = "W" if our_score > opp_score else "L"

                opp_abbr = opp_team.get("team", {}).get("abbreviation", "???").lower()
                opp_name = opp_team.get("team", {}).get("shortDisplayName", opp_team.get("team", {}).get("displayName", "???"))

                # Format date as "Apr 13"
                try:
                    gd = datetime.strptime(game_date, "%Y-%m-%d")
                    date_display = gd.strftime("%b %d").replace(" 0", " ")
                except:
                    date_display = game_date

                recent.append({
                    "date": date_display,
                    "result": result,
                    "team_score": our_score,
                    "opp_name": opp_name,
                    "opp_score": opp_score,
                    "opp_logo": opp_abbr,
                    "league": cfg["espn_league"],
                    "game_date": game_date,
                    "game_id": event.get("id", ""),
                })

        elif status_type in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS"):
            # Parse upcoming game
            teams_data = comp.get("competitors", [])
            opp_team = None
            home_away = "vs."
            for t in teams_data:
                is_us = t.get("id") == cfg["espn_team_id"] or t.get("team", {}).get("abbreviation") == cfg["espn_abbr"]
                if is_us:
                    if t.get("homeAway") == "away":
                        home_away = "at"
                else:
                    opp_team = t

            if opp_team:
                opp_name = opp_team.get("team", {}).get("shortDisplayName", "???")
                broadcast = ""
                for b in comp.get("broadcasts", []):
                    names = b.get("names", [])
                    if names:
                        broadcast = names[0]
                        break

                # Parse game time
                try:
                    game_dt = datetime.fromisoformat(game_date_str.replace("Z", "+00:00")).astimezone(EST)
                    time_str = game_dt.strftime("%-I:%M %p").replace("AM", "AM").replace("PM", "PM")
                except:
                    time_str = ""

                # Day of week
                try:
                    gd = datetime.strptime(game_date, "%Y-%m-%d")
                    dow = gd.strftime("%a")
                    month_day = gd.strftime("%-m/%-d")
                    day_display = f"{dow} {month_day}"
                except:
                    day_display = game_date

                upcoming.append({
                    "day": day_display,
                    "team": team_key,
                    "logo": cfg["logo"],
                    "name": cfg["full_name"].split()[-1],  # "Leafs", "Jays", etc.
                    "opp": f"{home_away} {opp_name}",
                    "time": f"{time_str} ET" if time_str else "TBD",
                    "tv": broadcast or "TBD",
                    "game_date": game_date,
                })

    # Sort recent by date descending, take last 4
    recent.sort(key=lambda x: x.get("game_date", ""), reverse=True)
    recent = recent[:4]

    # Sort upcoming by date ascending, take next 7 days
    upcoming.sort(key=lambda x: x.get("game_date", ""))
    week_from_now = (NOW + timedelta(days=7)).strftime("%Y-%m-%d")
    upcoming = [g for g in upcoming if g.get("game_date", "") <= week_from_now]

    return recent, upcoming


def get_standings(team_key):
    """Get standings from ESPN and extract this team's entry."""
    cfg = TEAMS[team_key]
    url = f"https://site.api.espn.com/apis/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/standings?level=3"
    data = espn_fetch(url)
    if not data:
        return {}

    team_id = cfg["espn_team_id"]
    team_abbr = cfg["espn_abbr"]

    # Navigate the standings structure: children > standings > entries
    def _iter_groups(node):
        if node.get("standings", {}).get("entries"):
            yield node
        for c in node.get("children", []):
            yield from _iter_groups(c)
    for group in _iter_groups(data):
        group_name = group.get("name", "")
        for subgroup in group.get("standings", {}).get("entries", []):
            entry_team = subgroup.get("team", {})
            entry_id = str(entry_team.get("id", ""))
            entry_abbr = entry_team.get("abbreviation", "")

            if entry_id == team_id or entry_abbr == team_abbr:
                # Found our team ‚Äî extract all stats into a dict
                stats = {}
                for s in subgroup.get("stats", []):
                    name = s.get("name", "")
                    if name:
                        stats[name] = s.get("displayValue", s.get("value", ""))
                stats["_group_name"] = group_name
                return stats

    # Try alternate structure (some leagues use a flat list)
    for entry in data.get("standings", {}).get("entries", []):
        entry_team = entry.get("team", {})
        entry_id = str(entry_team.get("id", ""))
        entry_abbr = entry_team.get("abbreviation", "")

        if entry_id == team_id or entry_abbr == team_abbr:
            stats = {}
            for s in entry.get("stats", []):
                name = s.get("name", "")
                if name:
                    stats[name] = s.get("displayValue", s.get("value", ""))
            return stats

    return {}


def build_full_standings(team_key):
    """Build complete standings tables from ESPN API for a team's panel.
    Returns a dict matching the data.json standings schema:
    { tabs: [...], panes: [{ headers: [...], rows: [...] }, ...] }
    Each team gets 2 tabs: division + wild card/playoff picture.
    The team's own row is always included with you=True, even if outside the cutoff."""
    cfg = TEAMS[team_key]
    league = cfg["league"]
    team_id = cfg["espn_team_id"]
    team_abbr = cfg["espn_abbr"]

    url = f"https://site.api.espn.com/apis/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/standings?level=3"
    data = espn_fetch(url)
    if not data:
        print(f"    WARNING: Could not fetch standings for {team_key}")
        return None

    # ESPN logo abbreviation mapping (lowercase for CSS class compatibility)
    LOGO_MAP = {
        # NHL
        "FLA": "fla", "TBL": "tb", "BOS": "bos", "DET": "det", "OTT": "ott",
        "TOR": "tor", "BUF": "buf", "MTL": "mtl", "CAR": "car", "NJD": "nj",
        "WSH": "wsh", "NYR": "nyr", "NYI": "nyi", "PHI": "phi", "CBJ": "cbj",
        "PIT": "pit", "WPG": "wpg", "DAL": "dal", "COL": "col", "MIN": "min",
        "NSH": "nsh", "STL": "stl", "CHI": "chi", "ARI": "ari", "UTA": "uta",
        "VGK": "vgk", "VAN": "van", "EDM": "edm", "CGY": "cgy", "LAK": "la",
        "SEA": "sea", "ANA": "ana", "SJS": "sj",
        # MLB
        "BAL": "bal", "NYY": "nyy", "TB": "tb", "BOS": "bos",
        "CLE": "cle", "DET": "det", "KC": "kc", "MIN": "min", "CWS": "cws",
        "HOU": "hou", "SEA": "sea", "TEX": "tex", "LAA": "laa", "OAK": "oak",
        "ATL": "atl", "NYM": "nym", "PHI": "phi", "MIA": "mia", "WSH": "wsh",
        "MIL": "mil", "CHC": "chc", "STL": "stl", "PIT": "pit", "CIN": "cin",
        "LAD": "lad", "SD": "sd", "AZ": "ari", "SF": "sf", "COL": "col",
        # NBA
        "CLE": "cle", "BOS": "bos", "NYK": "ny", "IND": "ind", "MIL": "mil",
        "MIA": "mia", "ORL": "orl", "PHI": "phi", "CHI": "chi", "ATL": "atl",
        "BKN": "bkn", "DET": "det", "CHA": "cha", "WAS": "wsh",
        "OKC": "okc", "DEN": "den", "MIN": "min", "LAC": "lac", "DAL": "dal",
        "PHX": "phx", "MEM": "mem", "SAC": "sac", "GSW": "gs", "HOU": "hou",
        "SAS": "sa", "LAL": "lal", "POR": "por", "UTA": "uta", "NOP": "no",
        # NFL
        "PHI": "phi", "DAL": "dal", "WAS": "wsh", "NYG": "nyg",
        "SF": "sf", "SEA": "sea", "LAR": "lar", "ARI": "ari",
        "GB": "gb", "DET": "det", "CHI": "chi", "MIN": "min",
        "TB": "tb", "NO": "no", "ATL": "atl", "CAR": "car",
        "KC": "kc", "BUF": "buf", "MIA": "mia", "NE": "ne", "NYJ": "nyj",
        "BAL": "bal", "PIT": "pit", "CIN": "cin", "CLE": "cle",
        "HOU": "hou", "IND": "ind", "JAX": "jax", "TEN": "ten",
        "DEN": "den", "LV": "lv", "LAC": "lac",
    }

    def get_logo(abbr):
        return LOGO_MAP.get(abbr, abbr.lower())

    def extract_stat(entry, stat_name):
        for s in entry.get("stats", []):
            if s.get("name") == stat_name:
                return s.get("displayValue", s.get("value", ""))
        return ""

    def is_our_team(entry):
        eid = str(entry.get("team", {}).get("id", ""))
        eabbr = entry.get("team", {}).get("abbreviation", "")
        return eid == team_id or eabbr == team_abbr

    # Parse the full standings structure: children > [groups] > standings > entries
    all_groups = {}  # group_name -> list of entries
    def _collect(node):
        entries = node.get("standings", {}).get("entries", [])
        if entries:
            all_groups[node.get("name", "")] = entries
        for child in node.get("children", []):
            _collect(child)
    for top in data.get("children", []):
        _collect(top)

    if not all_groups:
        # Flat structure fallback
        entries = data.get("standings", {}).get("entries", [])
        if entries:
            all_groups["League"] = entries

    if not all_groups:
        return None

    # Find which group our team belongs to
    our_group = None
    our_conference = None
    for group_name, entries in all_groups.items():
        for entry in entries:
            if is_our_team(entry):
                our_group = group_name
                # Infer conference from group name
                if "Atlantic" in group_name or "Metropolitan" in group_name or "Eastern" in group_name:
                    our_conference = "Eastern"
                elif "Central" in group_name or "Pacific" in group_name or "Western" in group_name:
                    our_conference = "Western"
                elif "AL" in group_name or "American" in group_name:
                    our_conference = "AL"
                elif "NL" in group_name or "National" in group_name:
                    our_conference = "NL"
                elif "NFC" in group_name:
                    our_conference = "NFC"
                elif "AFC" in group_name:
                    our_conference = "AFC"
                break
        if our_group:
            break

    if not our_group:
        return None

    def build_row(entry, league_key):
        """Build a standings row from an ESPN entry."""
        t = entry.get("team", {})
        abbr = t.get("abbreviation", "")
        display = t.get("displayName", t.get("shortDisplayName", abbr))
        # Shorten long names
        name_map = {"Tampa Bay Lightning": "Tampa Bay", "Tampa Bay Rays": "Tampa Bay",
                     "Tampa Bay Buccaneers": "Tampa Bay", "New York Yankees": "NY Yankees",
                     "New York Mets": "NY Mets", "New York Rangers": "NY Rangers",
                     "New York Islanders": "NY Islanders", "New York Knicks": "New York",
                     "New York Giants": "NY Giants", "Los Angeles Dodgers": "LA Dodgers",
                     "Los Angeles Angels": "LA Angels", "Los Angeles Lakers": "LA Lakers",
                     "Los Angeles Clippers": "LA Clippers", "Los Angeles Rams": "LA Rams",
                     "Los Angeles Chargers": "LA Chargers", "San Francisco 49ers": "San Francisco",
                     "San Francisco Giants": "San Francisco", "Chicago White Sox": "Chi White Sox",
                     "Golden State Warriors": "Golden State", "San Antonio Spurs": "San Antonio",
                     "New Orleans Pelicans": "New Orleans", "Oklahoma City Thunder": "Oklahoma City",
                     "Portland Trail Blazers": "Portland", "Sacramento Kings": "Sacramento",
                     "Minnesota Timberwolves": "Minnesota", "Las Vegas Raiders": "Las Vegas",
                     "New England Patriots": "New England", "Jacksonville Jaguars": "Jacksonville"}
        short_name = name_map.get(display, display.split()[-1] if len(display.split()) > 1 else display)

        row = {
            "team": short_name,
            "logo": get_logo(abbr),
            "league": league_key,
            "you": is_our_team(entry),
        }
        return row, entry

    # === BUILD PANE 1: DIVISION STANDINGS ===
    division_entries = all_groups.get(our_group, [])

    if league == "NHL":
        headers_div = ["Team", "W", "L", "OTL", "PTS"]
        stat_keys = ["wins", "losses", "otLosses", "points"]
        sort_key = "points"
        league_key = "nhl"
    elif league == "MLB":
        headers_div = ["Team", "W", "L", "PCT", "GB"]
        stat_keys = ["wins", "losses", "winPercent", "gamesBehind"]
        sort_key = "winPercent"
        league_key = "mlb"
    elif league == "NBA":
        headers_div = ["Team", "W", "L", "PCT"]
        stat_keys = ["wins", "losses", "winPercent"]
        sort_key = "winPercent"
        league_key = "nba"
    elif league == "NFL":
        headers_div = ["Team", "W", "L", "PCT", "DIV"]
        stat_keys = ["wins", "losses", "winPercent", "divisionRecord"]
        sort_key = "winPercent"
        league_key = "nfl"
    else:
        return None

    # Sort entries by the appropriate key
    def sort_val(entry):
        val = extract_stat(entry, sort_key)
        try:
            return -float(val)  # Negative for descending
        except:
            return 0
    division_entries_sorted = sorted(division_entries, key=sort_val)

    div_rows = []
    for entry in division_entries_sorted:
        row, _ = build_row(entry, league_key)
        vals = [extract_stat(entry, sk) for sk in stat_keys]
        row["vals"] = vals
        div_rows.append(row)

    pane_div = {"headers": headers_div, "rows": div_rows}

    # === BUILD PANE 2: WILD CARD / PLAYOFF PICTURE ===
    if league == "NHL":
        # Eastern/Western Wild Card: Top 3 from each division + 2 wild card spots
        tab2_name = f"{our_conference} Wild Card" if our_conference else "Wild Card"
        conf_groups = {gn: es for gn, es in all_groups.items()
                       if our_conference and our_conference.lower() in gn.lower()
                       or (our_conference == "Eastern" and any(d in gn for d in ["Atlantic", "Metropolitan"]))
                       or (our_conference == "Western" and any(d in gn for d in ["Central", "Pacific"]))}

        wc_rows = []
        our_team_in_wc = False
        for div_name, entries in sorted(conf_groups.items()):
            sorted_entries = sorted(entries, key=sort_val)
            div_prefix = div_name[0] if div_name else "?"
            for i, entry in enumerate(sorted_entries[:3], 1):
                row, _ = build_row(entry, league_key)
                row["seed"] = f"{div_prefix}{i}"
                row["vals"] = [extract_stat(entry, sk) for sk in stat_keys]
                wc_rows.append(row)
                if row["you"]:
                    our_team_in_wc = True

        # Wild card spots: teams 4+ from each division, sorted by points
        wc_contenders = []
        for div_name, entries in conf_groups.items():
            sorted_entries = sorted(entries, key=sort_val)
            wc_contenders.extend(sorted_entries[3:])
        wc_contenders_sorted = sorted(wc_contenders, key=sort_val)

        for i, entry in enumerate(wc_contenders_sorted):
            row, _ = build_row(entry, league_key)
            if i < 2:
                row["seed"] = f"WC{i+1}"
            else:
                row["seed"] = ""
                row["seed_style"] = "faint"
            row["vals"] = [extract_stat(entry, sk) for sk in stat_keys]
            wc_rows.append(row)
            if row["you"]:
                our_team_in_wc = True
            # Show a few teams beyond the cutoff, but always include our team
            if i >= 3 and not row["you"] and our_team_in_wc:
                continue  # Stop after showing a couple below the line

        # If our team still isn't in the wild card pane, add them
        if not our_team_in_wc:
            for entry in wc_contenders_sorted:
                if is_our_team(entry):
                    row, _ = build_row(entry, league_key)
                    row["seed"] = ""
                    row["seed_style"] = "faint"
                    row["vals"] = [extract_stat(entry, sk) for sk in stat_keys]
                    wc_rows.append(row)
                    break

        wc_headers = ["", "Team"] + headers_div[1:]
        pane_wc = {"headers": wc_headers, "has_seed_col": True, "rows": wc_rows}

    elif league == "MLB":
        # AL/NL Wild Card
        tab2_name = f"{our_conference} Wild Card" if our_conference else "Wild Card"
        # NOTE: Do NOT use `our_conference.lower() in gn.lower()` for MLB ‚Äî
        # "al" is a substring of "Nation*al*", causing NL teams to leak into AL.
        # Use explicit full-word matching only.
        conf_groups = {gn: es for gn, es in all_groups.items()
                       if (our_conference == "AL" and "American" in gn)
                       or (our_conference == "NL" and "National" in gn)}

        wc_rows = []
        our_team_in_wc = False
        # Division leaders
        for div_name, entries in sorted(conf_groups.items()):
            sorted_entries = sorted(entries, key=sort_val)
            if sorted_entries:
                entry = sorted_entries[0]
                row, _ = build_row(entry, league_key)
                row["seed"] = "DIV"
                row["seed_style"] = "faint"
                row["vals"] = [extract_stat(entry, "wins"), extract_stat(entry, "losses"),
                               extract_stat(entry, "winPercent"), "—"]
                wc_rows.append(row)
                if row["you"]:
                    our_team_in_wc = True

        # Wild card contenders: non-leaders sorted by record
        wc_contenders = []
        for div_name, entries in conf_groups.items():
            sorted_entries = sorted(entries, key=sort_val)
            wc_contenders.extend(sorted_entries[1:])
        wc_contenders_sorted = sorted(wc_contenders, key=sort_val)

        for i, entry in enumerate(wc_contenders_sorted):
            row, _ = build_row(entry, league_key)
            if i < 3:
                row["seed"] = f"WC{i+1}"
            else:
                row["seed"] = ""
                row["seed_style"] = "faint"
            # Calculate WC games behind
            wc_leader_pct = 1.0
            if wc_contenders_sorted:
                try:
                    wc_leader_pct = float(extract_stat(wc_contenders_sorted[0], "winPercent"))
                except:
                    pass
            try:
                team_pct = float(extract_stat(entry, "winPercent"))
                gb = extract_stat(entry, "gamesBehind")
            except:
                gb = ""
            row["vals"] = [extract_stat(entry, "wins"), extract_stat(entry, "losses"),
                           extract_stat(entry, "winPercent"), gb or "—"]
            wc_rows.append(row)
            if row["you"]:
                our_team_in_wc = True
            # Show at least 8 teams, or until our team is included
            if i >= 7 and our_team_in_wc:
                break

        # If our team still not shown, append them
        if not our_team_in_wc:
            for entry in wc_contenders_sorted:
                if is_our_team(entry):
                    row, _ = build_row(entry, league_key)
                    row["seed"] = ""
                    row["seed_style"] = "faint"
                    row["vals"] = [extract_stat(entry, "wins"), extract_stat(entry, "losses"),
                                   extract_stat(entry, "winPercent"),
                                   extract_stat(entry, "gamesBehind") or "—"]
                    wc_rows.append(row)
                    break

        wc_headers = ["", "Team", "W", "L", "PCT", "WCGB"]
        pane_wc = {"headers": wc_headers, "has_seed_col": True, "rows": wc_rows}

    elif league == "NBA":
        # Conference standings + playoff bracket
        tab2_name = "Playoff Bracket"
        conf_groups = {gn: es for gn, es in all_groups.items()
                       if our_conference and our_conference.lower() in gn.lower()}

        # Build conference table for tab 1 (replace division with conference)
        conf_entries = []
        for gn, entries in conf_groups.items():
            conf_entries.extend(entries)
        conf_entries_sorted = sorted(conf_entries, key=sort_val)

        # Override pane_div with full conference standings
        conf_rows = []
        for i, entry in enumerate(conf_entries_sorted, 1):
            row, _ = build_row(entry, league_key)
            row["vals"] = [extract_stat(entry, "wins"), extract_stat(entry, "losses"),
                           extract_stat(entry, "winPercent")]
            conf_rows.append(row)
        pane_div = {"headers": ["Seed", "Team", "W", "L", "PCT"],
                    "has_seed_col": True,
                    "rows": []}
        for i, row in enumerate(conf_rows, 1):
            row["seed"] = str(i)
            pane_div["rows"].append(row)
            if i >= 10:  # Show top 10 + our team
                break

        # If our team not in top 10, add them
        our_in_conf = any(r["you"] for r in pane_div["rows"])
        if not our_in_conf:
            for i, row in enumerate(conf_rows):
                if row["you"]:
                    row["seed"] = str(i + 1)
                    pane_div["rows"].append(row)
                    break

        headers_div = pane_div["headers"]
        tab1_name = f"{our_conference} Conference" if our_conference else "Conference"

        # Playoff bracket: top 8 seeds with matchups
        bracket_rows = []
        for i in range(0, min(8, len(conf_entries_sorted)), 2):
            if i + 1 < len(conf_entries_sorted):
                t1 = conf_entries_sorted[i]
                t2 = conf_entries_sorted[i + 1]
                t1_name = t1.get("team", {}).get("shortDisplayName", "TBD")
                t2_name = t2.get("team", {}).get("shortDisplayName", "TBD")
                t1_w = extract_stat(t1, "wins")
                t1_l = extract_stat(t1, "losses")
                t2_w = extract_stat(t2, "wins")
                t2_l = extract_stat(t2, "losses")
                bracket_rows.append({
                    "matchup": f"({i+1}) {t1_name} vs. ({i+2}) {t2_name}",
                    "detail": f"Sat Apr 18"  # Placeholder ‚Äî ideally from schedule
                })

        # Playoff bracket: use bracket format (matchup + series fields)
        # so index.html renders via the bracket path (no logo column needed)
        bracket_display_rows = []
        for i in range(0, min(8, len(conf_entries_sorted)), 2):
            if i + 1 < len(conf_entries_sorted):
                t1 = conf_entries_sorted[i]
                t2 = conf_entries_sorted[i + 1]
                row1, _ = build_row(t1, league_key)
                row2, _ = build_row(t2, league_key)
                bracket_display_rows.append({
                    "matchup": f"({i+1}) {row1['team']} vs. ({i+2}) {row2['team']}",
                    "series": "Sat Apr 18",
                    "you": row1["you"] or row2["you"],
                })

        pane_wc = {"headers": ["Matchup", "Series"], "bracket": True,
                   "rows": bracket_display_rows}

    elif league == "NFL":
        # NFC/AFC Division + Playoff Picture
        tab2_name = f"2025 {our_conference} Playoffs" if our_conference else "Playoffs"

        # Playoff results ‚Äî this is historical data, keep from existing
        pane_wc = {"headers": ["Seed", "Team", "W", "L", "Result"],
                   "has_seed_col": True,
                   "rows": []}
        # For NFL offseason, we show last season's playoff results
        # These are better kept from existing data since they're historical
        # Return None for pane_wc to signal "keep existing"
        pane_wc = None

    else:
        pane_wc = None

    # Assemble the result
    if league == "NBA":
        tab1_name = f"{our_conference} Conference"
    else:
        tab1_name = our_group if our_group else "Division"

    if pane_wc is None:
        # Only return division pane if wild card couldn't be built
        return {
            "tabs": [tab1_name],
            "panes": [pane_div],
        }

    return {
        "tabs": [tab1_name, tab2_name],
        "panes": [pane_div, pane_wc],
    }


# === ARTICLE DISCOVERY (API-FIRST ARCHITECTURE) ===
# The key insight: ESPN's news API returns REAL, working article URLs every time.
# League APIs (NHL.com, MLB.com) add source diversity.
# Perplexity is used ONLY for editorial writing, never for URL discovery.

def fetch_espn_articles(team_key, limit=8):
    """Fetch real article URLs from ESPN's news API. These are GUARANTEED valid."""
    cfg = TEAMS[team_key]
    url = f"https://site.api.espn.com/apis/site/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/news?team={cfg['espn_team_id']}&limit={limit}"
    data = espn_fetch(url)
    if not data:
        return []

    articles = []
    for item in data.get("articles", []):
        link = item.get("links", {}).get("web", {}).get("href", "")
        if not link:
            # Try alternate link paths
            link = item.get("links", {}).get("api", {}).get("news", {}).get("href", "")
        if not link:
            continue

        # Parse published date
        pub_date = item.get("published", "")
        date_display = ""
        days_old = 999
        if pub_date:
            try:
                # ESPN dates: "2026-04-15T23:45:00Z"
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                date_display = dt.strftime("%B %d, %Y").replace(" 0", " ")
                days_old = (NOW - dt.astimezone(EST)).days
            except:
                date_display = pub_date[:10]

        articles.append({
            "source": "ESPN",
            "source_class": "espn",
            "headline": item.get("headline", ""),
            "dek": item.get("description", "")[:200],
            "date": date_display,
            "link": link,
            "days_old": days_old,
            "type": item.get("type", ""),
        })

    return articles


def fetch_espn_game_recap_urls(team_key, recent):
    """Get direct game recap URLs from ESPN for recent games.
    These are the most valuable articles ‚Äî specific game recaps with real URLs."""
    cfg = TEAMS[team_key]
    recaps = []

    for game in recent[:3]:
        game_id = game.get("game_id", "")
        if not game_id:
            continue

        # ESPN recap URL pattern (verified)
        recap_url = f"https://www.espn.com/{cfg['espn_league']}/recap?gameId={game_id}"

        opp = game.get("opp_name", "???")
        result = game.get("result", "")
        ts = game.get("team_score", 0)
        os_score = game.get("opp_score", 0)
        result_word = "beat" if result == "W" else "fell to"

        recaps.append({
            "source": "ESPN",
            "source_class": "espn",
            "headline": f"{cfg['full_name'].split()[-1]} {result_word} {opp} {ts}–{os_score}",
            "dek": f"Full game recap and box score.",
            "date": game.get("date", ""),
            "link": recap_url,
            "days_old": 0,
            "type": "recap",
            "game_id": game_id,
        })

    return recaps


def fetch_league_articles(team_key, limit=5):
    """Fetch articles from league-specific APIs for source diversity.
    These supplement ESPN articles with NHL.com, MLB.com, etc."""
    cfg = TEAMS[team_key]
    league = cfg["league"]
    articles = []

    try:
        if league == "NHL":
            # NHL.com API (api-web.nhle.com ‚Äî current working endpoint)
            url = "https://api-web.nhle.com/v1/content/en-us/stories?tags.slug=torontomapleleafs&context=slug&$limit=5"
            data = espn_fetch(url)
            if data and isinstance(data, list):
                for item in data[:limit]:
                    slug = item.get("slug", "")
                    headline = item.get("title", item.get("headline", ""))
                    summary = item.get("summary", item.get("fields", {}).get("description", ""))
                    if slug and headline:
                        link = f"https://www.nhl.com/news/{slug}"
                        articles.append({
                            "source": "NHL.com",
                            "source_class": "web",
                            "headline": headline,
                            "dek": summary[:200] if summary else "",
                            "date": "",
                            "link": link,
                            "days_old": 0,
                            "type": "news",
                        })
            # Fallback: try alternate endpoint structure
            if not articles:
                url2 = "https://forge-dapi.d3.nhle.com/v2/content/en-us/stories?context.slug=toronto-maple-leafs&$limit=5"
                data2 = espn_fetch(url2)
                if data2:
                    items = data2.get("items", data2.get("stories", []))
                    for item in items[:limit]:
                        slug = item.get("slug", "")
                        headline = item.get("headline", item.get("title", ""))
                        summary = item.get("summary", "")
                        if slug and headline:
                            link = f"https://www.nhl.com/news/{slug}"
                            articles.append({
                                "source": "NHL.com",
                                "source_class": "web",
                                "headline": headline,
                                "dek": summary[:200] if summary else "",
                                "date": "",
                                "link": link,
                                "days_old": 0,
                                "type": "news",
                            })

        elif league == "MLB":
            # MLB content API (newer endpoint)
            url = "https://www.mlb.com/feeds/news/rss/141"
            try:
                req = Request(url, headers={"User-Agent": "TheMorningSkate/1.0"})
                with urlopen(req, timeout=15) as resp:
                    xml_data = resp.read().decode("utf-8")
                root = ET.fromstring(xml_data)
                for item in root.findall(".//item")[:limit]:
                    headline = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    if headline and link:
                        articles.append({
                            "source": "MLB.com",
                            "source_class": "web",
                            "headline": headline,
                            "dek": desc[:200] if desc else "",
                            "date": "",
                            "link": link,
                            "days_old": 0,
                            "type": "news",
                        })
            except Exception:
                # Fallback: Stats API
                url2 = "https://statsapi.mlb.com/api/v1/news?teamId=141&limit=5"
                data = espn_fetch(url2)
                if data and "articles" in data:
                    for item in data.get("articles", [])[:limit]:
                        headline = item.get("headline", "")
                        summary = item.get("subhead", item.get("blurb", ""))
                        slug = item.get("slug", "")
                        link = item.get("url", "")
                        if not link and slug:
                            link = f"https://www.mlb.com/news/{slug}"
                        if link and headline:
                            articles.append({
                                "source": "MLB.com",
                                "source_class": "web",
                                "headline": headline,
                                "dek": summary[:200] if summary else "",
                                "date": "",
                                "link": link,
                                "days_old": 0,
                                "type": "news",
                            })

        elif league == "NBA":
            pass  # NBA articles come from Google News + ESPN

        elif league == "NFL":
            pass  # NFL articles come from Google News + ESPN

    except Exception as e:
        print(f"  WARNING: League API fetch failed for {team_key}: {e}")

    return articles


# === SOURCE CLASSIFICATION ===
# Maps known domains to human-readable source names and CSS classes
SOURCE_MAP = {
    "tsn.ca": ("TSN", "tsn"),
    "sportsnet.ca": ("Sportsnet", "sportsnet"),
    "theathletic.com": ("The Athletic", "athletic"),
    "nhl.com": ("NHL.com", "web"),
    "nba.com": ("NBA.com", "web"),
    "mlb.com": ("MLB.com", "web"),
    "nfl.com": ("NFL.com", "web"),
    "thescore.com": ("theScore", "web"),
    "thestar.com": ("Toronto Star", "web"),
    "torontosun.com": ("Toronto Sun", "web"),
    "theglobeandmail.com": ("Globe and Mail", "web"),
    "washingtonpost.com": ("Washington Post", "web"),
    "nbcsports.com": ("NBC Sports", "web"),
    "si.com": ("SI", "web"),
    "cbc.ca": ("CBC Sports", "web"),
    "hogshaven.com": ("Hogs Haven", "web"),
    "commanders.com": ("Commanders.com", "web"),
    "thehockeynews.com": ("The Hockey News", "web"),
    "raptorsrepublic.com": ("Raptors Republic", "web"),
    "bluejaysnation.com": ("Blue Jays Nation", "web"),
    "mapleleafshotstove.com": ("Leafs Hot Stove", "web"),
    "yahoo.com": ("Yahoo Sports", "web"),
    "foxsports.com": ("Fox Sports", "web"),
    "reuters.com": ("Reuters", "web"),
    "apnews.com": ("AP News", "web"),
}

def classify_source(source_name, url):
    """Determine the display source name and CSS class from a URL or source name."""
    url_lower = url.lower() if url else ""
    for domain, (name, css_class) in SOURCE_MAP.items():
        if domain in url_lower:
            return name, css_class
    # If source_name from RSS is available, use it
    if source_name:
        return source_name, "web"
    return "News", "web"


def fetch_google_news_articles(team_key, limit=10):
    """Fetch articles from Google News RSS for multi-source diversity.
    Returns articles from TSN, Sportsnet, The Athletic, Toronto Star,
    Washington Post, league sites, theScore, and more.
    Google News RSS aggregates all these sources without needing individual APIs."""
    cfg = TEAMS[team_key]
    team_name = cfg["full_name"]  # "Toronto Maple Leafs"

    # Build search query ‚Äî use exact match for team name
    query = quote_plus(f'"{team_name}"')

    # Use Canadian locale for Toronto teams, US for Commanders
    if team_key == "commanders":
        rss_url = f"https://news.google.com/rss/search?q={query}+when:3d&hl=en-US&gl=US&ceid=US:en"
    else:
        rss_url = f"https://news.google.com/rss/search?q={query}+when:3d&hl=en-CA&gl=CA&ceid=CA:en"

    try:
        req = Request(rss_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TheMorningSkate/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        })
        with urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")
    except Exception as e:
        print(f"    WARNING: Google News RSS failed: {e}")
        return []

    articles = []
    try:
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item"):
            title_raw = item.findtext("title", "")
            link_raw = item.findtext("link", "")
            link = resolve_google_news_url(link_raw)
            pub_date = item.findtext("pubDate", "")
            source_el = item.find("source")
            source_name = source_el.text if source_el is not None else ""

            if not title_raw or not link:
                continue
            if not is_publisher_url(link):
                continue

            # Skip ESPN articles (we already have those from the API)
            if "espn.com" in link.lower():
                continue

            # Google News title format: "Headline - Source Name"
            # Strip the " - Source" suffix
            if " - " in title_raw:
                headline = title_raw.rsplit(" - ", 1)[0].strip()
            else:
                headline = title_raw

            # Classify the source
            display_source, source_class = classify_source(source_name, link)

            # Parse publication date
            date_display = ""
            days_old = 999
            if pub_date:
                try:
                    dt = parsedate_to_datetime(pub_date)
                    date_display = dt.strftime("%B %d, %Y").replace(" 0", " ")
                    days_old = max(0, (NOW - dt.astimezone(EST)).days)
                except Exception:
                    pass

            # Only include articles from last 3 days
            if days_old > 3:
                continue

            articles.append({
                "source": display_source,
                "source_class": source_class,
                "headline": headline,
                "dek": "",  # Google News RSS doesn't include full descriptions
                "date": date_display or TODAY_DISPLAY,
                "link": link,
                "days_old": days_old,
                "type": "news",
            })

            if len(articles) >= limit:
                break

    except ET.ParseError as e:
        print(f"    WARNING: Google News RSS parse error: {e}")

    return articles


# === SOURCE DIVERSITY ENFORCEMENT ===
# Hard cap: no single source can fill more than this many slots per team section
MAX_SAME_SOURCE_PER_TEAM = 2
# Hard cap: no single source can appear more than this many times across ALL homepage stories
MAX_SAME_SOURCE_HOMEPAGE = 1

# Dedicated RSS feeds for Tier 1 Canadian sources (not just Google News)
TIER1_RSS_FEEDS = {
    "leafs": [
        ("https://www.tsn.ca/rss/nhl/maple-leafs", "TSN", "tsn"),
        ("https://www.sportsnet.ca/feed/", "Sportsnet", "sportsnet"),  # main feed, filtered below
    ],
    "jays": [
        ("https://www.tsn.ca/rss/mlb", "TSN", "tsn"),
        ("https://www.sportsnet.ca/feed/", "Sportsnet", "sportsnet"),
        ("https://www.cbc.ca/cmlink/rss-sports-mlb", "CBC Sports", "web"),
    ],
    "raptors": [
        ("https://www.tsn.ca/rss/nba", "TSN", "tsn"),
        ("https://www.sportsnet.ca/feed/", "Sportsnet", "sportsnet"),
    ],
    "commanders": [
        # US sources ‚Äî no Canadian feeds needed
    ],
}


def fetch_tier1_rss_articles(team_key, limit=5):
    """Fetch articles from dedicated Tier 1 RSS feeds for guaranteed source diversity.
    This supplements Google News ‚Äî even if Google News returns only Sportsnet articles,
    we'll still have TSN, CBC, etc. from their own feeds."""
    cfg = TEAMS[team_key]
    team_name_lower = cfg["full_name"].lower()
    # Keywords to filter feed items by relevance to this team
    team_keywords = [w.lower() for w in cfg["full_name"].split() if len(w) > 3]
    articles = []

    for feed_url, source_name, source_class in TIER1_RSS_FEEDS.get(team_key, []):
        try:
            req = Request(feed_url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; TheMorningSkate/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            })
            with urlopen(req, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8", errors="replace")

            root = ET.fromstring(xml_data)
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                description = item.findtext("description", "")

                if not title or not link:
                    continue
                if not is_publisher_url(link):
                    continue

                # Filter: must mention the team (for general feeds like Sportsnet)
                combined = (title + " " + description).lower()
                if not any(kw in combined for kw in team_keywords):
                    continue

                # Skip ESPN articles (we already have those)
                if "espn.com" in link.lower():
                    continue

                # Parse date
                days_old = 999
                date_display = ""
                if pub_date:
                    try:
                        dt = parsedate_to_datetime(pub_date)
                        date_display = dt.strftime("%B %d, %Y").replace(" 0", " ")
                        days_old = max(0, (NOW - dt.astimezone(EST)).days)
                    except Exception:
                        pass

                # Only last 3 days
                if days_old > 3:
                    continue

                # Clean HTML from description
                clean_desc = re.sub(r'<[^>]+>', '', description)[:200].strip()

                articles.append({
                    "source": source_name,
                    "source_class": source_class,
                    "headline": title.strip(),
                    "dek": clean_desc,
                    "date": date_display or TODAY_DISPLAY,
                    "link": link,
                    "days_old": days_old,
                    "type": "news",
                })

                if len(articles) >= limit:
                    break

        except Exception as e:
            print(f"    WARNING: {source_name} RSS failed: {e}")

    return articles


def discover_articles_for_team(team_key, recent, phase_info):
    """Master article discovery: ESPN API + game recaps + league APIs + Tier 1 RSS + Google News RSS.
    Returns a deduplicated, validated list with maximum source diversity."""
    cfg = TEAMS[team_key]
    all_articles = []

    # Layer 1: ESPN News API (most reliable ‚Äî guaranteed working URLs)
    print(f"    Fetching ESPN articles...")
    espn_articles = fetch_espn_articles(team_key)
    print(f"    Found {len(espn_articles)} ESPN articles")
    all_articles.extend(espn_articles)

    # Layer 2: Game recaps (direct URLs, highest value for in-season)
    if recent and is_recent_enough(recent, max_days=7):
        print(f"    Fetching game recap URLs...")
        recaps = fetch_espn_game_recap_urls(team_key, recent)
        print(f"    Found {len(recaps)} game recaps")
        all_articles.extend(recaps)

    # Layer 3: League-specific APIs (NHL.com, MLB.com, etc.)
    print(f"    Fetching league articles...")
    league_articles = fetch_league_articles(team_key)
    print(f"    Found {len(league_articles)} league articles")
    all_articles.extend(league_articles)

    # Layer 4: Dedicated Tier 1 RSS feeds (TSN, Sportsnet, CBC ‚Äî guaranteed sources)
    print(f"    Fetching Tier 1 RSS articles...")
    tier1_articles = fetch_tier1_rss_articles(team_key)
    print(f"    Found {len(tier1_articles)} Tier 1 RSS articles")
    all_articles.extend(tier1_articles)

    # Layer 5: Google News RSS (multi-source: The Athletic,
    # Toronto Star, Globe & Mail, Washington Post, theScore, NBC Sports, etc.)
    print(f"    Fetching Google News articles...")
    gnews_articles = fetch_google_news_articles(team_key)
    print(f"    Found {len(gnews_articles)} Google News articles")
    all_articles.extend(gnews_articles)

    # Deduplicate by URL (normalize ‚Äî strip trailing slashes, query params for comparison)
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        url = article.get("link", "")
        # Normalize for dedup: lowercase, strip trailing slash
        url_norm = url.lower().rstrip("/") if url else ""
        if url_norm and url_norm not in seen_urls:
            seen_urls.add(url_norm)
            unique_articles.append(article)

    # Sort: game recaps first, then non-ESPN sources, then by recency
    def sort_key(a):
        type_priority = 0 if a.get("type") == "recap" else 1
        # Boost non-ESPN sources to get diversity
        source_priority = 0 if a.get("source", "ESPN") != "ESPN" else 1
        days = a.get("days_old", 999)
        return (type_priority, source_priority, days)

    unique_articles.sort(key=sort_key)

    # Validate URLs (ESPN URLs are guaranteed; others need checking)
    validated = []
    validation_count = 0
    max_validations = 15  # Cap to keep runtime reasonable
    for article in unique_articles:
        url = article.get("link", "")
        if "espn.com" in url:
            # ESPN URLs from the API are guaranteed valid
            validated.append(article)
        elif validation_count < max_validations:
            validation_count += 1
            if validate_url(url):
                validated.append(article)
                print(f"    [OK] {article.get('source', '?')}: {url[:70]}")
            else:
                print(f"    [DEAD] {article.get('source', '?')}: {url[:70]}")
        else:
            # Over validation budget ‚Äî skip non-ESPN articles to stay fast
            pass

    # Log source diversity
    sources = set(a.get("source", "?") for a in validated)
    print(f"    Total verified articles: {len(validated)} from {len(sources)} sources: {', '.join(sorted(sources))}")

    return validated


def select_the_latest(all_articles, count=4):
    """Select the best 3-4 articles for 'The Latest' section.
    ENFORCES source diversity ‚Äî no single source can appear more than
    MAX_SAME_SOURCE_PER_TEAM times. The whole point of the app is that
    your dad gets articles from TSN, Sportsnet, The Athletic,
    Toronto Star, NHL.com, etc. ‚Äî not just ESPN 4 times."""
    if not all_articles:
        return []

    def make_entry(article):
        return {
            "source": article.get("source", "ESPN"),
            "source_class": article.get("source_class", "web"),
            "headline": article.get("headline", ""),
            "dek": article.get("dek", ""),
            "date": article.get("date", TODAY_DISPLAY),
            "link": article.get("link", "#"),
        }

    selected = []
    source_counts = {}  # Track how many times each source is used
    used_urls = set()

    def can_use_source(source):
        """Check if we haven't exceeded the per-source cap."""
        return source_counts.get(source, 0) < MAX_SAME_SOURCE_PER_TEAM

    def add_article(article):
        entry = make_entry(article)
        selected.append(entry)
        source = article.get("source", "Unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        used_urls.add(article.get("link"))

    # Separate into ESPN and non-ESPN buckets
    espn_articles = [a for a in all_articles if a.get("source") == "ESPN"]
    non_espn_articles = [a for a in all_articles if a.get("source") != "ESPN"]

    # Pass 1: Pick the BEST game recap (if any) ‚Äî ESPN recaps are great for this
    for article in all_articles:
        if len(selected) >= 1:
            break
        if article.get("type") == "recap":
            add_article(article)

    # Pass 2: Fill with NON-ESPN articles, one per source (maximize diversity)
    for article in non_espn_articles:
        if len(selected) >= count:
            break
        source = article.get("source", "")
        url = article.get("link", "")
        if url in used_urls:
            continue
        if not can_use_source(source):
            continue
        # First pass: strictly one per source for max diversity
        if source_counts.get(source, 0) >= 1 and len(non_espn_articles) > count:
            continue
        add_article(article)

    # Pass 3: Fill remaining with ESPN (respecting cap)
    for article in espn_articles:
        if len(selected) >= count:
            break
        url = article.get("link", "")
        if url in used_urls:
            continue
        if not can_use_source("ESPN"):
            continue
        add_article(article)

    # Pass 4: If still not enough, relax the one-per-source constraint
    # but still enforce the hard MAX_SAME_SOURCE_PER_TEAM cap
    for article in all_articles:
        if len(selected) >= count:
            break
        url = article.get("link", "")
        source = article.get("source", "Unknown")
        if url in used_urls:
            continue
        if not can_use_source(source):
            continue
        add_article(article)

    # Log source diversity
    unique_sources = len(source_counts)
    print(f"    The Latest: {len(selected)} articles from {unique_sources} sources: {', '.join(sorted(source_counts.keys()))}")

    return selected


def build_homepage_stories_from_articles(all_team_facts, all_team_articles):
    """Build featured + two-up + extra stories from REAL discovered articles.
    Uses verified ESPN data for accuracy, real article URLs for links.
    Optionally uses Perplexity to write better editorial headlines."""
    today_str = NOW.strftime("%B %d, %Y")
    stories = []

    # Score each team's top article by newsworthiness
    team_candidates = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        facts = all_team_facts.get(team_key, {})
        articles = all_team_articles.get(team_key, [])
        phase_info = facts.get("phase_info", {})
        phase_id = phase_info.get("phase", "")
        recent = facts.get("recent", [])
        team_info = facts.get("team_info", {})
        upcoming = facts.get("upcoming", [])

        if not articles:
            continue

        # Priority scoring
        score = 0
        if "playoffs" in phase_id:
            score += 100  # Playoff teams always top priority
        if recent:
            try:
                gd = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
                days_ago = (NOW.replace(tzinfo=None) - gd).days
                if days_ago <= 1:
                    score += 50  # Played last night
                elif days_ago <= 2:
                    score += 30
            except:
                pass
        if "regular_season" in phase_id:
            score += 20
        # Offseason teams get low priority
        if "offseason" in phase_id or "draft" in phase_id or "ended" in phase_id:
            score += 5

        # Pick best article: prefer a non-ESPN source for homepage diversity,
        # and try to avoid repeating sources already used by earlier candidates.
        # Fall back to ESPN (which has guaranteed-working URLs)
        homepage_used_sources = set(c["article"].get("source") for c in team_candidates)
        best_article = articles[0]
        for a in articles[:8]:
            src = a.get("source", "ESPN")
            if src != "ESPN" and src not in homepage_used_sources and a.get("type") != "recap":
                best_article = a
                break
        else:
            # Fallback: any non-ESPN source even if already used
            for a in articles[:6]:
                if a.get("source", "ESPN") != "ESPN" and a.get("type") != "recap":
                    best_article = a
                    break

        team_candidates.append({
            "team_key": team_key,
            "score": score,
            "article": best_article,
            "phase_info": phase_info,
            "team_info": team_info,
            "recent": recent,
            "upcoming": upcoming,
        })

    # Sort by score (highest first)
    team_candidates.sort(key=lambda x: x["score"], reverse=True)

    # Build editorial stories from top candidates
    for candidate in team_candidates[:4]:
        team_key = candidate["team_key"]
        article = candidate["article"]
        cfg = TEAMS[team_key]
        phase_info = candidate["phase_info"]
        team_info = candidate["team_info"]
        recent = candidate["recent"]
        upcoming = candidate["upcoming"]
        phase_id = phase_info.get("phase", "")
        record = team_info.get("record", "")
        standing = team_info.get("standing_summary", "")

        # Build editorial headline and dek
        topic = "Playoffs" if "playoffs" in phase_id else phase_info.get("label", "Update")
        if recent and article.get("type") == "recap":
            topic = "Game Recap"

        # Use the real article headline, but enhance the dek with team context
        headline = article.get("headline", "")
        dek = article.get("dek", "")

        # Add team context to dek if it's too generic
        if record and record not in dek:
            if recent and is_recent_enough(recent, max_days=3):
                g = recent[0]
                context = f"The {cfg['full_name']} ({record}) "
                if upcoming:
                    context += f"face {upcoming[0]['opp']} next on {upcoming[0]['day']}."
                else:
                    context += f"sit {standing.lower() if standing else ''}."
                if len(dek) > 10:
                    dek = dek + " " + context
                else:
                    dek = context
            else:
                dek = f"The {cfg['full_name']} ({record}). {standing}. " + dek

        # Ensure dek isn't too long
        if len(dek) > 250:
            dek = dek[:247] + "…"

        stories.append({
            "team": team_key,
            "kicker": f"{cfg['full_name']} &middot; {topic}",
            "headline": headline,
            "dek": dek,
            "source": article.get("source", "ESPN"),
            "link": article.get("link", _get_fallback_url(team_key)),
            "date": TODAY_DISPLAY,
        })

    return stories


def build_verified_facts(team_key, team_info, standings, recent, upcoming, phase_info=None):
    """Build a verified facts block from ESPN data to inject into AI prompts.
    This prevents Perplexity from hallucinating records, standings, or results."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")
    facts = []

    facts.append(f"TEAM: {cfg['full_name']} ({cfg['league']})")
    facts.append(f"TODAY'S DATE: {today_str}")

    # Season phase ‚Äî this is CRITICAL for editorial direction
    if phase_info:
        facts.append(f"SEASON PHASE: {phase_info['label']}")
        facts.append(f"EDITORIAL DIRECTION: {phase_info['editorial_direction']}")

    # Record
    record = team_info.get("record", "")
    if record:
        facts.append(f"CURRENT RECORD: {record}")

    # Standing summary
    standing = team_info.get("standing_summary", "")
    if standing:
        facts.append(f"STANDING: {standing}")

    # Standings details
    if standings:
        if "streak" in standings:
            facts.append(f"STREAK: {standings['streak']}")
        if "points" in standings:
            facts.append(f"POINTS: {standings['points']}")
        if "gamesBack" in standings:
            facts.append(f"GAMES BACK: {standings['gamesBack']}")
        if "gamesBehind" in standings:
            facts.append(f"GAMES BEHIND: {standings['gamesBehind']}")
        if "clincher" in standings:
            facts.append(f"CLINCH STATUS: {standings['clincher']}")
        if "playoffSeed" in standings:
            facts.append(f"PLAYOFF SEED: {standings['playoffSeed']}")

    # Recent results ‚Äî only include if they're actually recent
    if recent:
        last_game_days = 999
        try:
            last_game_date = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
            last_game_days = (NOW.replace(tzinfo=None) - last_game_date).days
        except:
            pass

        if last_game_days <= 14:
            facts.append(f"RECENT RESULTS (most recent first, last game was {last_game_days} day(s) ago):")
            for g in recent[:4]:
                facts.append(f"  {g['date']}: {g['result']} {g['team_score']}-{g['opp_score']} vs {g['opp_name']}")

            # Calculate recent streak from results
            if len(recent) >= 2:
                streak_type = recent[0]["result"]
                streak_count = 0
                for g in recent:
                    if g["result"] == streak_type:
                        streak_count += 1
                    else:
                        break
                facts.append(f"CURRENT RUN: {streak_count} game {'win' if streak_type == 'W' else 'loss'} streak")
        else:
            facts.append(f"LAST GAME: {last_game_days} days ago ‚Äî this team is NOT currently playing regular season games.")
            facts.append(f"DO NOT write about old game results as if they are current news.")

    # Upcoming
    if upcoming:
        next_game = upcoming[0]
        facts.append(f"NEXT GAME: {next_game['day']} {next_game['opp']} at {next_game['time']}")
    elif phase_info and ("offseason" in phase_info["phase"] or "draft" in phase_info["phase"] or "ended" in phase_info["phase"]):
        facts.append(f"NO UPCOMING GAMES ‚Äî this team is in the {phase_info['label']} phase.")

    # Season status indicators
    record_stats = team_info.get("record_stats", {})
    if record_stats:
        # Check for playoff elimination or clinch
        if record_stats.get("playoffSeed"):
            facts.append(f"PLAYOFF SEED: {record_stats['playoffSeed']}")

    # Explicit playoff series status from actual game data (CRITICAL for preventing hallucination)
    if phase_info and "playoffs" in phase_info.get("phase", ""):
        if upcoming:
            opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip()
            if opp:
                facts.append(f"PLAYOFF OPPONENT: {opp} (from ESPN schedule ‚Äî use THIS name, not any other)")

        # Derive playoff series status from ACTUAL recent results
        # Look for repeated matchups against the same opponent in recent results
        playoff_opp = None
        if upcoming:
            playoff_opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip()
        elif recent:
            playoff_opp = recent[0].get("opp_name", "")

        if playoff_opp and recent:
            # Count series games from recent results against this opponent
            series_wins = 0
            series_losses = 0
            for g in recent:
                if g.get("opp_name", "").lower() == playoff_opp.lower():
                    if g["result"] == "W":
                        series_wins += 1
                    else:
                        series_losses += 1
                else:
                    break  # Different opponent = not part of this series

            if series_wins == 0 and series_losses == 0:
                facts.append(f"PLAYOFF SERIES STATUS: The series against {playoff_opp} has NOT started yet. No games have been played.")
                facts.append(f"CRITICAL: Do NOT write about any playoff game results. The series has not begun.")
                if upcoming:
                    ng = upcoming[0]
                    facts.append(f"GAME 1: Scheduled for {ng['day']} at {ng['time']}")
            else:
                total_games = series_wins + series_losses
                facts.append(f"PLAYOFF SERIES STATUS: Series vs {playoff_opp} is {series_wins}-{series_losses} (Team leads)" if series_wins > series_losses else
                             f"PLAYOFF SERIES STATUS: Series vs {playoff_opp} is {series_wins}-{series_losses} (Trail)" if series_wins < series_losses else
                             f"PLAYOFF SERIES STATUS: Series vs {playoff_opp} is tied {series_wins}-{series_losses}")
                facts.append(f"SERIES GAMES PLAYED: {total_games}")
                facts.append(f"IMPORTANT: Only reference games that appear in the RECENT RESULTS above. Do NOT invent additional game results.")
        elif not recent or not is_recent_enough(recent, max_days=14):
            facts.append(f"PLAYOFF SERIES STATUS: Awaiting schedule. No playoff games have been played yet.")
            facts.append(f"CRITICAL: Do NOT fabricate or invent any playoff game results.")

        facts.append(f"NOTE: This team is in the PLAYOFFS. All content must reflect playoff urgency.")

    # For eliminated teams, add explicit guidance
    if phase_info and phase_info.get("phase") == "eliminated":
        facts.append(f"NOTE: This team has been ELIMINATED or missed the playoffs. Their season is OVER.")
        facts.append(f"DO NOT write about this team as if they are in the playoffs.")
        facts.append(f"Focus on: season recap, draft lottery, offseason outlook, what went wrong.")

    return "\n".join(facts)


def build_key_numbers(team_key, team_info, standings, recent):
    """Generate 4 key numbers from verified ESPN data."""
    cfg = TEAMS[team_key]
    league = cfg["league"]
    numbers = []
    record = team_info.get("record", "")
    record_stats = team_info.get("record_stats", {})
    standing_summary = team_info.get("standing_summary", "")

    # Number 1: Record
    if record:
        numbers.append({
            "number": record,
            "label": "Season Record",
            "note": standing_summary or f"{cfg['league']} {NOW.year} season",
        })

    # Number 2: League-specific key stat
    if league == "NHL":
        pts = standings.get("points", record_stats.get("points_display", ""))
        if pts:
            numbers.append({"number": str(pts), "label": "Points", "note": standing_summary or ""})
    elif league == "MLB":
        wp = record_stats.get("winPercent_display", record_stats.get("winPercent", ""))
        if wp:
            try:
                wp_fmt = f".{int(float(wp)*1000):03d}" if float(wp) < 1 else wp
            except:
                wp_fmt = wp
            numbers.append({"number": wp_fmt, "label": "Win Pct", "note": standing_summary or ""})
    elif league == "NBA":
        # Use playoff seed if available, else conference rank
        seed = standings.get("playoffSeed", "")
        if seed and str(seed) != "0":
            numbers.append({"number": f"#{seed}", "label": "Playoff Seed", "note": standing_summary or ""})
        else:
            ppg = standings.get("avgPointsFor", "")
            if ppg:
                numbers.append({"number": str(ppg), "label": "PPG", "note": "Points per game"})
    elif league == "NFL":
        wp = record_stats.get("winPercent_display", "")
        if wp:
            numbers.append({"number": wp, "label": "Win Pct", "note": standing_summary or ""})

    # Number 3: Streak
    streak = standings.get("streak", "")
    if streak:
        numbers.append({"number": streak, "label": "Streak", "note": "Current streak"})
    elif recent:
        # Calculate from recent results
        streak_type = recent[0]["result"]
        streak_count = 0
        for g in recent:
            if g["result"] == streak_type:
                streak_count += 1
            else:
                break
        streak_str = f"{'W' if streak_type == 'W' else 'L'}{streak_count}"
        numbers.append({"number": streak_str, "label": "Streak", "note": f"{'Won' if streak_type == 'W' else 'Lost'} last {streak_count}"})

    # Number 4: Games back or recent form
    is_first = standing_summary.startswith("1st") if standing_summary else False
    gb = standings.get("gamesBack", standings.get("gamesBehind", ""))
    if gb and gb != "0" and gb != "-" and not is_first:
        # Only show "Games Back" for teams NOT in 1st place
        numbers.append({"number": str(gb), "label": "Games Back", "note": standing_summary or "In division"})
    elif recent:
        # For 1st-place teams or when GB unavailable, show Last 4 record
        last_n = recent[:4]
        w = sum(1 for g in last_n if g["result"] == "W")
        l = len(last_n) - w
        numbers.append({"number": f"{w}-{l}", "label": "Last 4", "note": "Recent form"})

    # Pad to 4 if we don't have enough
    while len(numbers) < 4:
        if recent and len(numbers) < 4:
            # Last 10 record
            last_10 = recent[:10]
            w = sum(1 for g in last_10 if g["result"] == "W")
            l = len(last_10) - w
            numbers.append({"number": f"{w}-{l}", "label": f"Last {len(last_10)}", "note": "Recent form"})
        else:
            break

    return numbers[:4]


def generate_ticker(all_team_facts, all_team_articles=None):
    """Generate ticker items from verified ESPN data PLUS editorial news bites from articles.
    Each item must have: badge, badge_style, text (matching index.html renderTicker)."""
    ticker_items = []

    # Badge styles per team (CSS variable colors)
    BADGE_STYLES = {
        "leafs": "nhl",
        "jays": "mlb",
        "raptors": "nba",
        "commanders": "nfl",
    }

    for team_key, facts_dict in all_team_facts.items():
        cfg = TEAMS[team_key]
        team_name = cfg["full_name"].split()[-1]
        league = cfg["league"]
        badge_style = BADGE_STYLES.get(team_key, "muted")

        recent = facts_dict.get("recent", [])
        team_info = facts_dict.get("team_info", {})
        upcoming = facts_dict.get("upcoming", [])
        phase_info = facts_dict.get("phase_info", {})
        record = team_info.get("record", "")

        # Determine if team is in-season using PHASE detection (most reliable)
        phase_id = phase_info.get("phase", "")
        is_in_season = phase_id in ("regular_season", "regular_season_late", "playoffs",
                                     "preseason", "spring_training")
        if not is_in_season and bool(upcoming):
            is_in_season = True
        if not is_in_season and recent:
            try:
                last_game_date = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
                days_since = (NOW.replace(tzinfo=None) - last_game_date).days
                if days_since <= 14:
                    is_in_season = True
            except:
                pass

        # Most recent game result ‚Äî only if game was within the last 14 days
        if recent and is_in_season:
            try:
                last_game_date = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
                days_since = (NOW.replace(tzinfo=None) - last_game_date).days
            except:
                days_since = 999

            if days_since <= 14:
                g = recent[0]
                result_word = "beat" if g["result"] == "W" else "fell to"
                ticker_items.append({
                    "badge": league,
                    "badge_style": badge_style,
                    "text": f"{team_name} {result_word} {g['opp_name']} {g['team_score']}–{g['opp_score']}"
                })

        # Record + standing
        standing = team_info.get("standing_summary", "")
        if record and standing:
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} ({record}) — {standing}"
            })
        elif record:
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} record: {record}"
            })
        elif not is_in_season:
            phase_label = phase_info.get("label", "Offseason")
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} — {phase_label}"
            })

        # Next game (only if in-season / has upcoming)
        if upcoming:
            ng = upcoming[0]
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"Next: {team_name} {ng['opp']} — {ng['day']} {ng['time']}"
            })

        # === EDITORIAL NEWS BITE from discovered articles ===
        # Pull the top non-recap headline as a ticker-sized news item
        if all_team_articles and team_key in all_team_articles:
            articles = all_team_articles[team_key]
            for article in articles:
                headline = html.unescape(article.get("headline", "")).rstrip("…. \t").strip()
                source = article.get("source", "")
                # Skip generic game recaps (already covered by score ticker)
                if not headline:
                    continue
                headline_lower = headline.lower()
                skip_phrases = ["game story", "scores/highlights", "box score", "full game recap",
                                "game recap", "final score"]
                if any(sp in headline_lower for sp in skip_phrases):
                    continue
                # Found a non-recap editorial headline ‚Äî truncate for ticker
                # Ticker items should be under 60 chars
                if len(headline) > 55:
                    headline = headline[:52].rsplit(" ", 1)[0]
                ticker_items.append({
                    "badge": league,
                    "badge_style": badge_style,
                    "text": headline,
                })
                break  # Only one editorial bite per team

        # === PHASE-AWARE EDITORIAL ITEMS ===
        if phase_id == "playoffs" and not upcoming:
            # Playoff team waiting for schedule
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} Playoffs — schedule TBD"
            })
        elif phase_id == "eliminated":
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} season over — lottery watch begins"
            })
        elif phase_id == "pre_draft":
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} — NFL Draft Apr 23–25"
            })

    # Deduplicate ticker items by text content (keep first occurrence)
    seen_texts = set()
    deduped = []
    for item in ticker_items:
        if item["text"] not in seen_texts:
            seen_texts.add(item["text"])
            deduped.append(item)
    if len(deduped) < len(ticker_items):
        print(f"  Ticker dedup: removed {len(ticker_items) - len(deduped)} duplicate(s)")
    ticker_items = deduped

    # Cap all ticker item texts at 70 chars for readability
    for item in ticker_items:
        if len(item["text"]) > 70:
            item["text"] = item["text"][:67].rsplit(" ", 1)[0]

    return ticker_items


# === PERPLEXITY API ===

def perplexity_search(prompt, system_prompt=""):
    """Call Perplexity API with web search for fresh information."""
    if not PERPLEXITY_API_KEY:
        print("  WARNING: No Perplexity API key ‚Äî skipping AI generation")
        return None

    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": system_prompt} if system_prompt else None,
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.7,
        "search_recency_filter": "day",
    }
    # Remove None entries
    payload["messages"] = [m for m in payload["messages"] if m]

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        "https://api.perplexity.ai/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (URLError, HTTPError) as e:
        print(f"  WARNING: Perplexity API failed: {e}")
        return None


def generate_editorial_dek(headline, team_key, phase_info, team_info=None, existing_dek=""):
    """Generate a 2-sentence editorial dek via Perplexity.

    Used for featured / two_up / extra_story / the_latest items. Falls back
    to existing_dek on any failure. Phase 2 of the daily update: replaces
    templated deks with web-search-backed editorial context."""
    print("  DEBUG gen_dek:", team_key, "key=", bool(PERPLEXITY_API_KEY), "hlen=", len(headline or ""), "phase=", (phase_info or {}).get("phase") if phase_info else None, flush=True)
    if not PERPLEXITY_API_KEY:
        return existing_dek or ""
    if not headline:
        return existing_dek or ""

    cfg = TEAMS.get(team_key, {})
    team_full = cfg.get("full_name", team_key)
    league = cfg.get("league", "")
    record = (team_info or {}).get("record", "")
    standing = (team_info or {}).get("standing_summary", "")
    phase_label = phase_info.get("label", "") if phase_info else ""
    today_str = NOW.strftime("%B %d, %Y")

    system_prompt = (
        "You are a sharp sports editor writing subhead / dek copy for a daily briefing. "
        "Given a news headline about a specific team, write exactly TWO sentences (30-55 words total) "
        "that give the reader meaningful context for why the story matters today. "
        "Do not repeat the headline verbatim. Do not invent facts. Do not use em-dashes or en-dashes. "
        "Return only the two sentences, no quotes, no preamble, no markdown."
    )
    prompt = (
        f"TEAM: {team_full} ({league})\n"
        f"TODAY: {today_str}\n"
        f"SEASON PHASE: {phase_label}\n"
        f"RECORD: {record}\n"
        f"STANDING: {standing}\n"
        f"HEADLINE: {headline}\n"
        f"EXISTING DEK (may be generic, may repeat team name): {existing_dek}\n"
        "Write two sharp sentences of editorial context for this headline."
    )

    raw = perplexity_search(prompt, system_prompt=system_prompt)
    if not raw:
        return existing_dek or ""

    text = raw.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    elif text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    text = sanitize_ascii(text)
    if len(text) > 280:
        text = text[:277] + "..."
    if len(text) < 20:
        return existing_dek or text
    return text


def build_draft_board(team_key, phase_info, team_info=None):
    """Build Section 0.9 draft board for Leafs + Commanders during offseason.

    Returns dict or None if the team is not one of the two draft-board teams,
    the phase is not offseason-like, or Perplexity is unavailable / returns
    unparseable JSON. Output schema matches SKILL.md Section 0.9."""
    if team_key not in ("leafs", "commanders"):
        return None

    phase_id = (phase_info or {}).get("phase", "")
    print("  DEBUG draft_board:", team_key, "phase_id=", phase_id, "key=", bool(PERPLEXITY_API_KEY), flush=True)
    offseason_phases = (
        "eliminated", "season_ended", "deep_offseason", "offseason",
        "pre_draft", "draft_free_agency", "combine_free_agency", "otas",
        "postseason_offseason",
    )
    if phase_id not in offseason_phases:
        return None

    if not PERPLEXITY_API_KEY:
        return None

    cfg = TEAMS[team_key]
    league = cfg["league"]
    team_full = cfg["full_name"]
    today_str = NOW.strftime("%B %d, %Y")

    if league == "NHL":
        schema_hint = (
            'Return ONLY this JSON (no markdown, no prose):\n'
            '{\n'
            '  "projected_pick": "string like #5 overall",\n'
            '  "draft_date": "string like June 26-27, 2026",\n'
            '  "lottery": {"odds": "string like 8.5%", "outcome": "Pending or Won 4th overall"},\n'
            '  "prospects_watched": [\n'
            '    {"name": "string", "position": "string", "team": "junior or NCAA team", "note": "one sentence fit"}\n'
            '  ]\n'
            '}\n'
            "Include 3-5 prospects the team is most likely to take."
        )
    else:
        schema_hint = (
            'Return ONLY this JSON (no markdown, no prose):\n'
            '{\n'
            '  "projected_pick": "string like #3 overall",\n'
            '  "draft_date": "string like April 23-25, 2026",\n'
            '  "remaining_picks": ["Round 2 pick 35", "Round 3 pick 68"],\n'
            '  "prospects_watched": [\n'
            '    {"name": "string", "position": "string", "team": "college team", "note": "one sentence fit"}\n'
            '  ]\n'
            '}\n'
            "Include 3-5 prospects the team is most likely to take."
        )

    system_prompt = (
        "You are a sports analyst providing factual draft intel. "
        "Return ONLY a valid JSON object. No markdown fences, no prose, no commentary. "
        "If uncertain about a field, use the string 'TBD'."
    )
    prompt = (
        f"As of {today_str}, give the latest draft board for the {team_full} ({league}). "
        f"Use recent public mock drafts and beat reporting. {schema_hint}"
    )

    raw = perplexity_search(prompt, system_prompt=system_prompt)
    if not raw:
        return None

    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  WARNING: build_draft_board: bad JSON: {e}")
        return None

    if not isinstance(data, dict):
        return None

    result = {
        "team": team_full,
        "league": league,
        "projected_pick": sanitize_ascii(str(data.get("projected_pick", "TBD"))),
        "draft_date": sanitize_ascii(str(data.get("draft_date", "TBD"))),
        "prospects_watched": [],
    }

    prospects = data.get("prospects_watched") or []
    if isinstance(prospects, list):
        for p in prospects[:5]:
            if not isinstance(p, dict):
                continue
            result["prospects_watched"].append({
                "name": sanitize_ascii(str(p.get("name", ""))),
                "position": sanitize_ascii(str(p.get("position", ""))),
                "team": sanitize_ascii(str(p.get("team", ""))),
                "note": sanitize_ascii(str(p.get("note", ""))),
            })

    if league == "NHL":
        lottery = data.get("lottery") if isinstance(data.get("lottery"), dict) else {}
        result["lottery"] = {
            "odds": sanitize_ascii(str(lottery.get("odds", "TBD"))),
            "outcome": sanitize_ascii(str(lottery.get("outcome", "Pending"))),
        }
    else:
        picks = data.get("remaining_picks") if isinstance(data.get("remaining_picks"), list) else []
        result["remaining_picks"] = [sanitize_ascii(str(p)) for p in picks[:10]]

    return result


def generate_lotl(team_key, verified_facts="", phase_info=None, team_info=None, recent=None, upcoming=None):
    """Generate a Lay of the Land paragraph using Perplexity.
    verified_facts: pre-built string of ESPN-verified data that MUST be used for stats.
    phase_info: season phase context from detect_season_phase().
    Falls back to ESPN-only content if Perplexity fails or returns garbage."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")
    phase_label = phase_info["label"] if phase_info else "Unknown"
    editorial_dir = phase_info["editorial_direction"] if phase_info else ""

    system_prompt = f"""You are the lead sports columnist for "The Morning Skate," a daily briefing read by a 65-year-old father on his phone each morning. Write like a veteran newspaper columnist ‚Äî someone who's covered this beat for decades, knows the history, and isn't afraid of a sharp opinion.

TODAY IS: {today_str}
TEAM SEASON PHASE: {phase_label}

YOUR VOICE:
- Write like a broadsheet sports columnist: authoritative, opinionated, and vivid.
- Open with the narrative, not the team name. Lead with what matters.
- Use strong verbs and concrete images. "The bullpen imploded" not "the bullpen struggled."
- Don't hedge. If the season is over, say it plainly. If a player is carrying the team, give them their due.
- Use em dashes for dramatic pauses. Use short, punchy sentences between longer ones for rhythm.
- Include one or two telling statistics, woven naturally into the prose ‚Äî never a stats dump.
- End with a forward look: what's next, what to watch for, why it matters.

CRITICAL ACCURACY RULES:
- You will be given VERIFIED FACTS below from ESPN. These are CORRECT. Use these EXACT numbers.
- You MUST use the exact record, scores, and standings from the VERIFIED FACTS. Do NOT guess different numbers.
- You may search for additional COLOR and NARRATIVE details (player performances, quotes, storylines), but ALL statistics MUST come from the VERIFIED FACTS section.
- If the VERIFIED FACTS say the team is in the offseason, DO NOT write about old game results as current news.
- If the VERIFIED FACTS say the team is ELIMINATED, do NOT write about them as if they are in the playoffs.

ANTI-HALLUCINATION RULES (MANDATORY):
- You MUST NOT invent, fabricate, or assume ANY game results, scores, or series outcomes.
- You MUST NOT write about a playoff game happening unless it appears in the RECENT RESULTS in the VERIFIED FACTS.
- If the VERIFIED FACTS say "series has NOT started yet", you MUST NOT reference any game in that series.
- If the VERIFIED FACTS say the team is eliminated or their season is over, write about the offseason, not playoffs.
- ONLY reference specific game outcomes (scores, player stats from a game, series leads) that are explicitly listed in the RECENT RESULTS section of the VERIFIED FACTS.
- When in doubt about whether something happened, DO NOT include it. Omitting a fact is always better than inventing one.
- Your web search may return PREVIEW articles about upcoming games. Do NOT treat previews as if the games already happened.

CRITICAL CONTENT RULES:
- You MUST produce a finished column paragraph. Do NOT explain what you would need to write the column.
- Do NOT say "I don't have sufficient search results" or anything similar. Write the column with the facts you have.
- If your web search returns limited results, write the column using the VERIFIED FACTS provided ‚Äî they are more than enough.
- A column based on verified facts alone is infinitely better than no column at all.

FORMATTING:
- ONE paragraph, 150-200 words. Dense, polished prose ‚Äî no filler.
- Bold the single most important recent event with <strong> tags.
- Bold the forward-looking detail at the end with <strong> tags.
- Use literal Unicode characters (NOT HTML entities): — for em dashes, – for en dashes, ’ for apostrophes. Do NOT output tokens like &mdash; &ndash; or &rsquo;.
- Do NOT use markdown. No asterisks, no bullet points.
- Do NOT include citation numbers like [1], [2], etc. Write clean prose with no reference markers.
- Do NOT exceed 200 words."""

    facts_block = ""
    if verified_facts:
        facts_block = f"""

=== VERIFIED FACTS (from ESPN ‚Äî use these EXACT numbers) ===
{verified_facts}
=== END VERIFIED FACTS ===

"""

    prompt = f"""Write today's "Lay of the Land" column for the {cfg['full_name']} as of the morning of {today_str}.

SEASON PHASE: {phase_label}
EDITORIAL DIRECTION: {editorial_dir}
{facts_block}
Search for the LATEST {cfg['full_name']} news ‚Äî key player performances, quotes, storylines, injuries, trades, or offseason moves from the LAST 48 HOURS. But for ALL statistics (record, standings, scores, streaks), use ONLY the verified facts above.

CRITICAL CONSTRAINT: The VERIFIED FACTS section contains a PLAYOFF SERIES STATUS or SEASON STATUS field. You MUST respect it:
- If it says "series has NOT started yet" ‚Äî do NOT write about any playoff game results. Write about the upcoming series, matchup analysis, and what to watch for.
- If it says the team is "ELIMINATED" ‚Äî do NOT write about them being in the playoffs. Write about their season ending, draft lottery, and offseason outlook.
- If it lists specific series games (e.g., "Series is 1-0") ‚Äî ONLY reference games that appear in the RECENT RESULTS. Do NOT add games that aren't listed.
- Your web search may find PREVIEW articles about upcoming games. These are about games that HAVE NOT HAPPENED YET. Do not treat them as results.

Your paragraph must include:
1. An opening that captures the team's CURRENT narrative arc ‚Äî not "The [Team] are..." but something with edge and voice
2. The most important RECENT development (bold with <strong> tags) ‚Äî this must be something from the last 1-3 days, NOT old news
3. Context: record, standings from the VERIFIED FACTS, woven naturally into the prose
4. A key player thread ‚Äî who's hot, who's hurt, who's the story RIGHT NOW
5. A forward-looking close bolded with <strong> tags ‚Äî next game, next milestone, or what to watch for

IMPORTANT: Every fact you cite must be CURRENT as of {today_str}. Do not reference game results from weeks or months ago as if they just happened. Do NOT invent scores, player stats from games, or series outcomes.

Write 150-200 words of polished sports column prose. Every sentence should earn its place."""

    # === WORD COUNT ENFORCEMENT (120-200 words; retry up to 2x). Added 2026-04-19. ===
    result = None
    for attempt in range(3):
        candidate = perplexity_search(prompt, system_prompt)
        candidate = _clean_perplexity_prose(candidate)
        if is_perplexity_failure(candidate):
            continue
        # Strip HTML tags for an accurate visible word count
        plain = re.sub(r'<[^>]+>', '', candidate)
        word_count = len(plain.split())
        if 120 <= word_count <= 200:
            result = candidate
            break
        print(f"  WARNING: {team_key} LOTL attempt {attempt+1} word count {word_count} outside [120,200], retrying")

    if not result:
        print(f"  WARNING: Perplexity could not produce valid LOTL for {team_key}; using ESPN fallback")
        result = generate_espn_fallback_lotl(team_key, team_info or {}, recent or [], upcoming or [], phase_info or {"phase": "unknown", "label": "Unknown"})

    return result


def _clean_perplexity_prose(result):
    """Clean up Perplexity prose output ‚Äî remove markdown, citations, fix tags."""
    if not result:
        return result
    result = result.strip()
    result = re.sub(r'^\*\*.*?\*\*\s*\n*', '', result)  # Remove bold headers
    # Convert markdown bold **text** to <strong>text</strong>
    parts = result.split("**")
    if len(parts) > 1:
        rebuilt = parts[0]
        for i, part in enumerate(parts[1:], 1):
            tag = "<strong>" if i % 2 == 1 else "</strong>"
            rebuilt += tag + part
        result = rebuilt
    # Remove Perplexity citation markers like [1], [2], [1][2], etc.
    result = re.sub(r'\[\d+\]', '', result)
    # Clean up double spaces
    result = re.sub(r'  +', ' ', result)
    # Fix unclosed strong tags
    open_count = result.count("<strong>")
    close_count = result.count("</strong>")
    if open_count > close_count:
        result += "</strong>" * (open_count - close_count)
    return result


def fact_check_lotl(text, team_key, team_info, recent, upcoming, phase_info, standings):
    """Post-generation fact-checker for LOTL paragraphs.
    Verifies records, opponents, and phase-correctness against ESPN data.
    Returns corrected text, or None if the text is unsalvageable."""
    if not text:
        return text

    cfg = TEAMS[team_key]
    correct_record = team_info.get("record", "")
    phase_id = phase_info.get("phase", "") if phase_info else ""
    problems = []

    # 1. Check for wrong records (handles Unicode en-dash, em-dash, HTML entities, and ASCII hyphen)
    if correct_record:
        cparts = correct_record.split("-")
        if len(cparts) >= 2:
            try:
                cw, cl = int(cparts[0]), int(cparts[1].strip().split()[0])
                # Find all record-like patterns: "6-9", "6\u20139", "6\u20149", "6–9", "6—9"
                record_pattern = re.compile(r'(\d{1,3})\s*[-\u2013\u2014]\s*(\d{1,3})')
                html_pattern = re.compile(r'(\d{1,3})\s*&[nm]dash;\s*(\d{1,3})')

                for pat in [record_pattern, html_pattern]:
                    for m in pat.finditer(text):
                        w, l = int(m.group(1)), int(m.group(2))
                        total = w + l
                        ctotal = cw + cl
                        # Looks like a season record (not a game score) if total is close to correct total
                        if abs(total - ctotal) <= 6 and total > 10:
                            if w != cw or l != cl:
                                problems.append(f"Wrong record: found {m.group(0)}, correct is {correct_record}")
                                text = text[:m.start()] + correct_record.replace("-", "–") + text[m.end():]
            except (ValueError, IndexError):
                pass

    # 2. Check for offseason/eliminated content being written as if team is playing
    if "offseason" in phase_id or "ended" in phase_id or "draft" in phase_id or "eliminated" in phase_id:
        game_phrases = ["last night", "tonight's game", "yesterday's game", "beat the", "fell to the", "lost to the",
                        "playoff", "series", "first round", "postseason", "game 1", "game 2", "game 3", "game 4"]
        text_lower = text.lower()
        for phrase in game_phrases:
            if phrase in text_lower:
                # Only flag if there are no recent games within 14 days
                if "eliminated" in phase_id:
                    # Eliminated teams should NEVER reference playoffs
                    if phrase in ["playoff", "series", "first round", "postseason", "game 1", "game 2", "game 3", "game 4"]:
                        problems.append(f"Eliminated team has playoff language: '{phrase}'")
                        # This is unsalvageable ‚Äî use fallback
                        print(f"  FACT-CHECK [{team_key}]: Eliminated team references playoffs ‚Äî rejecting LOTL, using fallback")
                        return generate_espn_fallback_lotl(team_key, team_info, recent or [], upcoming or [],
                                                          phase_info or {"phase": "eliminated", "label": "Eliminated"})
                elif not recent or not is_recent_enough(recent, max_days=14):
                    problems.append(f"Offseason team has active game language: '{phrase}'")

    # 3. Check for fabricated playoff series results
    if "playoffs" in phase_id:
        text_lower = text.lower()
        # Check for specific "Game X" references and verify against actual results
        game_refs = re.findall(r'game\s+(\d)', text_lower)
        if game_refs and recent:
            # Count how many playoff games actually happened (against same opponent)
            playoff_opp = None
            if upcoming:
                playoff_opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip().lower()
            elif recent:
                playoff_opp = recent[0].get("opp_name", "").lower()

            actual_games = 0
            if playoff_opp:
                for g in recent:
                    if g.get("opp_name", "").lower() == playoff_opp:
                        actual_games += 1
                    else:
                        break

            for ref in game_refs:
                ref_num = int(ref)
                if ref_num > actual_games:
                    problems.append(f"References Game {ref_num} but only {actual_games} games have been played")
                    # This is a hallucination ‚Äî use fallback
                    print(f"  FACT-CHECK [{team_key}]: Fabricated Game {ref_num} reference (only {actual_games} played) ‚Äî rejecting LOTL")
                    return generate_espn_fallback_lotl(team_key, team_info, recent or [], upcoming or [],
                                                      phase_info or {"phase": "playoffs", "label": "Playoffs"})

        # Check for fabricated series scores like "1-1", "2-0", etc. in playoff context
        series_pattern = re.compile(r'(?:series|tied|leads?|trail)\w*\s+(?:at\s+)?(\d)\s*[-\u2013\u2014&]\s*(\d)', re.IGNORECASE)
        for m in series_pattern.finditer(text):
            claimed_w, claimed_l = int(m.group(1)), int(m.group(2))
            claimed_total = claimed_w + claimed_l
            # Verify against actual game count
            playoff_opp = None
            if upcoming:
                playoff_opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip().lower()
            elif recent:
                playoff_opp = recent[0].get("opp_name", "").lower()
            actual_games = 0
            if playoff_opp and recent:
                for g in recent:
                    if g.get("opp_name", "").lower() == playoff_opp:
                        actual_games += 1
                    else:
                        break
            if claimed_total > actual_games:
                problems.append(f"Claims series is {claimed_w}-{claimed_l} ({claimed_total} games) but only {actual_games} games played")
                print(f"  FACT-CHECK [{team_key}]: Fabricated series score ‚Äî rejecting LOTL")
                return generate_espn_fallback_lotl(team_key, team_info, recent or [], upcoming or [],
                                                  phase_info or {"phase": "playoffs", "label": "Playoffs"})

    # 4. Check for playoff opponent correctness (if in playoffs with upcoming games)
    if "playoffs" in phase_id and upcoming:
        next_opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip()
        if next_opp:
            text_lower = text.lower()
            # Check if the text mentions a DIFFERENT opponent in a playoff context
            playoff_phrases = ["playoff", "first round", "round 1", "series", "matchup", "postseason"]
            mentions_playoff = any(p in text_lower for p in playoff_phrases)
            if mentions_playoff and next_opp.lower() not in text_lower:
                problems.append(f"Playoff opponent mismatch: text doesn't mention {next_opp}")

    if problems:
        print(f"  FACT-CHECK [{team_key}]: {'; '.join(problems)}")

    return text


def fact_check_story(story, all_team_facts):
    """Post-generation fact-checker for a featured/two-up story.
    Returns True if the story passes, False if it should be rejected."""
    team_key = story.get("team", "")
    if not team_key or team_key not in all_team_facts:
        return True  # Can't check, let it through

    facts = all_team_facts[team_key]
    team_info = facts.get("team_info", {})
    phase_info = facts.get("phase_info", {})
    recent = facts.get("recent", [])
    correct_record = team_info.get("record", "")
    phase_id = phase_info.get("phase", "")

    headline = story.get("headline", "")
    dek = story.get("dek", "")
    combined = f"{headline} {dek}"

    # 1. Reject stories with stale records
    if correct_record:
        cparts = correct_record.split("-")
        if len(cparts) >= 2:
            try:
                cw, cl = int(cparts[0]), int(cparts[1].strip().split()[0])
                for pat in [re.compile(r'(\d{1,3})\s*[-\u2013\u2014]\s*(\d{1,3})'),
                            re.compile(r'(\d{1,3})\s*&[nm]dash;\s*(\d{1,3})')]:
                    for m in pat.finditer(combined):
                        w, l = int(m.group(1)), int(m.group(2))
                        total = w + l
                        ctotal = cw + cl
                        if abs(total - ctotal) <= 6 and total > 10:
                            if w != cw or l != cl:
                                print(f"    REJECTED story [{team_key}]: stale record {m.group(0)} vs correct {correct_record}")
                                return False
            except (ValueError, IndexError):
                pass

    # 2. Reject offseason stories that talk about recent games
    if "offseason" in phase_id or "ended" in phase_id:
        game_phrases = ["last night", "beat the", "fell to", "drop to", "drops to", "lose to", "defeat"]
        combined_lower = combined.lower()
        for phrase in game_phrases:
            if phrase in combined_lower:
                if not recent or not is_recent_enough(recent, max_days=7):
                    print(f"    REJECTED story [{team_key}]: offseason team with game language '{phrase}'")
                    return False

    # 3. Check story recency ‚Äî "Drop to X-Y" headlines are stale by definition if record is wrong
    combined_lower = combined.lower()
    if "drop to" in combined_lower or "drops to" in combined_lower or "fall to" in combined_lower:
        # These are game-result headlines. Check that the record in the headline matches current
        # (already covered by check 1, but being explicit)
        pass

    return True


def generate_espn_fallback_stories(all_team_facts):
    """Generate featured/two-up stories from pure ESPN data when Perplexity fails.
    Not as exciting, but always factually correct."""
    stories = []
    today_str = NOW.strftime("%B %d, %Y")

    # Priority: teams that played most recently, in-season teams first
    team_recency = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        facts = all_team_facts.get(team_key, {})
        recent = facts.get("recent", [])
        upcoming = facts.get("upcoming", [])
        phase_info = facts.get("phase_info", {})
        team_info = facts.get("team_info", {})

        days_since = 999
        if recent:
            try:
                gd = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
                days_since = (NOW.replace(tzinfo=None) - gd).days
            except:
                pass

        team_recency.append((team_key, days_since, recent, upcoming, phase_info, team_info))

    # Sort: most recent game first
    team_recency.sort(key=lambda x: x[1])

    for team_key, days_since, recent, upcoming, phase_info, team_info in team_recency:
        cfg = TEAMS[team_key]
        record = team_info.get("record", "")
        standing = team_info.get("standing_summary", "")
        phase_id = phase_info.get("phase", "")

        if recent and days_since <= 3:
            # Game recap story
            g = recent[0]
            result_word = "Beat" if g["result"] == "W" else "Fall to"
            headline = f"{cfg['full_name'].split()[-1]} {result_word} {g['opp_name']} {g['team_score']}–{g['opp_score']} — Record Moves to {record}"
            dek = f"The {cfg['full_name']} are now {record}"
            if standing:
                dek += f", {standing.lower()}"
            dek += "."
            if upcoming:
                dek += f" Next up: {upcoming[0]['opp']} on {upcoming[0]['day']}."

            topic = "Playoffs" if "playoffs" in phase_id else "Game Recap"
            stories.append({
                "team": team_key,
                "kicker": f"{cfg['full_name']} &middot; {topic}",
                "headline": headline,
                "dek": dek,
                "source": "ESPN",
                "link": _get_fallback_url(team_key),
            })
        elif "playoffs" in phase_id:
            # Playoff status story
            headline = f"{cfg['full_name'].split()[-1]} in the Playoffs — {record} Heading Into the Postseason"
            dek = f"The {cfg['full_name']} ({record}) are playoff-bound."
            if standing:
                dek += f" {standing}."
            if upcoming:
                dek += f" Next: {upcoming[0]['opp']} on {upcoming[0]['day']}."
            stories.append({
                "team": team_key,
                "kicker": f"{cfg['full_name']} &middot; Playoffs",
                "headline": headline,
                "dek": dek,
                "source": "ESPN",
                "link": _get_fallback_url(team_key),
            })
        elif record:
            # Status update story
            topic = phase_info.get("label", "Update")
            headline = f"{cfg['full_name'].split()[-1]} Update — {record}, {standing or topic}"
            dek = f"The {cfg['full_name']} sit at {record}."
            if standing:
                dek += f" {standing}."
            if upcoming:
                dek += f" Next game: {upcoming[0]['opp']} on {upcoming[0]['day']}."
            stories.append({
                "team": team_key,
                "kicker": f"{cfg['full_name']} &middot; {topic}",
                "headline": headline,
                "dek": dek,
                "source": "ESPN",
                "link": _get_fallback_url(team_key),
            })

        if len(stories) >= 4:
            break

    return {"stories": stories} if stories else None


def generate_featured_and_stories(all_team_facts):
    """Use Perplexity to identify the top 3-4 stories across all four teams.
    all_team_facts: dict of verified ESPN data per team to prevent hallucination."""
    today_str = NOW.strftime("%B %d, %Y")
    yesterday = (NOW - timedelta(days=1)).strftime("%B %d, %Y")

    # Build a detailed summary of verified facts for all teams, including phase
    facts_summary = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        fd = all_team_facts.get(team_key, {})
        cfg = TEAMS[team_key]
        ti = fd.get("team_info", {})
        recent = fd.get("recent", [])
        upcoming = fd.get("upcoming", [])
        phase = fd.get("phase_info", {})
        phase_label = phase.get("label", "Unknown")

        line = f"- {cfg['full_name']} ({cfg['league']}) ‚Äî PHASE: {phase_label} ‚Äî EXACT Record: {ti.get('record', 'N/A')}, {ti.get('standing_summary', 'N/A')}"

        # Only include game results if recent (within 3 days)
        if recent:
            try:
                last_game_date = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
                days_ago = (NOW.replace(tzinfo=None) - last_game_date).days
            except:
                days_ago = 999
            if days_ago <= 3:
                g = recent[0]
                line += f". Last game ({days_ago}d ago): {'W' if g['result'] == 'W' else 'L'} {g['team_score']}-{g['opp_score']} vs {g['opp_name']}"
            else:
                line += f". Last game was {days_ago} days ago ‚Äî DO NOT feature old game results"

        if upcoming:
            ng = upcoming[0]
            line += f". Next: {ng['opp']} {ng['day']}"
        elif "offseason" in phase.get("phase", "") or "draft" in phase.get("phase", ""):
            line += f". No upcoming games (offseason). Story angle: {phase.get('editorial_direction', '')[:100]}"

        facts_summary.append(line)

    facts_block = "\n".join(facts_summary)

    prompt = f"""As of the morning of {today_str}, identify the TOP 3-4 most important sports stories across these four teams.

=== VERIFIED TEAM STATUS (from ESPN ‚Äî use these exact records and scores) ===
{facts_block}
=== END VERIFIED STATUS ===

CRITICAL ACCURACY AND RECENCY RULES:
- Today is {today_str}. Yesterday was {yesterday}.
- For IN-SEASON teams: stories MUST be from the last 24-48 hours. Last night's game results are top priority.
- For OFFSEASON teams: stories must be about CURRENT offseason activity (draft, trades, signings, coaching changes). Do NOT write headlines about old game results from weeks or months ago.
- RECORD ACCURACY: Headlines must use the EXACT records from VERIFIED TEAM STATUS above. If it says "7-9", the headline MUST say "7-9", not "6-9" or "8-8" or ANY other number.
- OPPONENT ACCURACY: If a team's next opponent is listed above, use THAT opponent name. Do NOT guess or use a different team name.
- NEVER use the phrase "Drop to" or "Fall to" with a record that doesn't match the VERIFIED TEAM STATUS.
- Every headline and dek must pass this test: "Would this make sense as a newspaper headline printed on {today_str}?"
- ZERO TOLERANCE: Any story with an incorrect record, wrong opponent, or outdated result WILL be rejected by the fact-checker. Get it right the first time.

For each story, provide in this EXACT JSON format (no markdown, just raw JSON):
{{
  "stories": [
    {{
      "team": "leafs|jays|raptors|commanders",
      "kicker": "Team Name &middot; Topic",
      "headline": "10-18 word punchy headline with — for drama",
      "dek": "2-3 sentences, 40-60 words with context and forward-looking detail",
      "source": "Source name like ESPN or NHL.com",
      "link": "Real, currently accessible URL to the best article covering this story"
    }}
  ]
}}

STORY SELECTION PRIORITY:
1. Playoff games played last night (highest priority)
2. Regular season games played last night with dramatic storylines
3. Major breaking news (trades, injuries, firings) from last 24 hours
4. Offseason team's most significant current storyline (draft, free agency, etc.)

Use HTML entities (— – &middot;) not unicode. All URLs must be real ‚Äî search for real articles published in the last 48 hours. Do NOT fabricate URLs. Do NOT include citation numbers like [1], [2] in any text fields."""

    result = perplexity_search(prompt)
    if not result:
        return None

    # Try to extract JSON from the response
    try:
        cleaned = re.sub(r'\[\d+\]', '', result)
        json_match = re.search(r'\{[\s\S]*"stories"[\s\S]*\}', cleaned)
        if json_match:
            data = json.loads(json_match.group())
            for story in data.get("stories", []):
                for field in ("headline", "dek", "kicker"):
                    if field in story:
                        story[field] = re.sub(r'  +', ' ', story[field]).strip()
            return data
    except json.JSONDecodeError:
        print("  WARNING: Could not parse stories JSON from Perplexity")
    return None


def find_highlight_url(team_key, opp_name):
    """Use Perplexity to find the YouTube highlight video URL."""
    cfg = TEAMS[team_key]

    prompt = f"""Find the official {cfg['league']} YouTube highlight video for the most recent {cfg['full_name']} game (vs {opp_name}).

Search YouTube for the official {cfg['league']} channel's highlight video. It should be on the official {cfg['youtube_channel']} YouTube channel.

Return ONLY the direct YouTube URL in this format: https://www.youtube.com/watch?v=VIDEOID
Nothing else ‚Äî just the URL."""

    result = perplexity_search(prompt)
    if result:
        # Extract YouTube URL
        url_match = re.search(r'https://www\.youtube\.com/watch\?v=[\w-]+', result)
        if url_match:
            return url_match.group()

    # Fallback: return a search URL
    # No fresh URL found — return None so the caller can gate on it
    return None


def find_news_articles(team_key, phase_info=None):
    """Use Perplexity to find the 3 most recent news articles.
    Includes URL validation ‚Äî drops any article with a broken link."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")
    yesterday = (NOW - timedelta(days=1)).strftime("%B %d, %Y")
    phase_label = phase_info["label"] if phase_info else "Unknown"
    recency_days = phase_info["recency_days"] if phase_info else 3
    editorial_dir = phase_info["editorial_direction"] if phase_info else ""

    # Build recency guidance based on phase
    if recency_days <= 3:
        recency_guidance = f"Articles must be from the last {recency_days} days ({yesterday} or {today_str}). Game recaps from last night take highest priority."
    elif recency_days <= 7:
        recency_guidance = f"Articles should be from the last {recency_days} days. Prefer the most recent available."
    else:
        recency_guidance = f"Articles can be up to {recency_days} days old, but prefer the most recent available. Feature-length and analysis pieces are acceptable."

    prompt = f"""Find the 3 most recent and important news articles about the {cfg['full_name']} as of the morning of {today_str}.

TEAM PHASE: {phase_label}
EDITORIAL CONTEXT: {editorial_dir}

RECENCY REQUIREMENT: {recency_guidance}

CRITICAL: Every article must be CURRENTLY RELEVANT. The question to ask for each article: "Would a reader on {today_str} find this article timely and useful?"
- For in-season teams: game recaps from last night, injury updates, roster moves
- For offseason teams: draft analysis, free agency news, coaching changes, roster outlook
- NEVER include articles about game results from weeks or months ago

Return in this EXACT JSON format (no markdown, just raw JSON):
{{
  "articles": [
    {{
      "source": "Source name (ESPN, NHL.com, TSN, Sportsnet, etc.)",
      "source_class": "espn|nba|tsn|web|nfl|hogs",
      "headline": "A compelling headline in 10-20 words",
      "dek": "1-2 sentence summary, 20-40 words",
      "date": "Month Day, Year format ‚Äî must be within last {recency_days} days",
      "link": "The real, currently accessible URL to the article"
    }}
  ]
}}

Prefer Tier 1 sources: league official sites, ESPN, TSN, Sportsnet, The Athletic, CBS Sports, NBC Sports.
Include a mix of content types: recaps, analysis, roster/injury news.
Use HTML entities (— –) not unicode.
Do NOT include citation numbers like [1], [2] in any text fields.
ALL URLs must be real and currently accessible ‚Äî do NOT guess or fabricate URLs."""

    result = perplexity_search(prompt)
    if not result:
        return None

    try:
        cleaned = re.sub(r'\[\d+\]', '', result)
        json_match = re.search(r'\{[\s\S]*"articles"[\s\S]*\}', cleaned)
        if json_match:
            data = json.loads(json_match.group())
            articles = data.get("articles", [])

            # Clean up text fields
            for article in articles:
                for field in ("headline", "dek"):
                    if field in article:
                        article[field] = re.sub(r'  +', ' ', article[field]).strip()

            # === URL VALIDATION ===
            validated_articles = []
            for article in articles:
                url = article.get("link", "")
                if validate_url(url):
                    validated_articles.append(article)
                    print(f"    URL OK: {url[:80]}")
                else:
                    print(f"    URL DEAD ‚Äî dropping: {url[:80]}")
                    # Try to salvage with a known-good fallback URL for the source
                    fallback = _get_fallback_url(team_key, article.get("source", ""))
                    if fallback:
                        article["link"] = fallback
                        article["headline"] = article.get("headline", cfg["full_name"] + " News")
                        validated_articles.append(article)
                        print(f"    Replaced with fallback: {fallback[:80]}")

            data["articles"] = validated_articles
            return data
    except json.JSONDecodeError:
        print(f"  WARNING: Could not parse articles JSON for {team_key}")
    return None


def _get_fallback_url(team_key, source_name=""):
    """Return a known-good URL for a team's news page as a fallback when Perplexity gives a bad link."""
    cfg = TEAMS[team_key]
    league = cfg["league"]
    abbr = cfg["espn_abbr"].lower()

    # League-specific fallbacks
    fallbacks = {
        "NHL": f"https://www.espn.com/nhl/team/_/name/{abbr}/toronto-maple-leafs",
        "MLB": f"https://www.espn.com/mlb/team/_/name/{abbr}/toronto-blue-jays",
        "NBA": f"https://www.espn.com/nba/team/_/name/{abbr}/toronto-raptors",
        "NFL": f"https://www.espn.com/nfl/team/_/name/{abbr}/washington-commanders",
    }

    return fallbacks.get(league, "")


def _resolve_highlights(fresh, existing_team, recent):
    """Decide which highlights object to use.
    - If we just found fresh highlights, use them.
    - If not, check the existing highlights ‚Äî only keep them if the game_date is
      within 14 days. Stale highlights from months ago (e.g., NFL offseason)
      should be hidden, not shown indefinitely.
    """
    if fresh.get("available"):
        return fresh

    existing_hl = existing_team.get("last_game_highlights", {"available": False})
    if not existing_hl.get("available"):
        return {"available": False}

    # Check if the existing highlight's game is still reasonably recent
    game_date_str = existing_hl.get("game_date", "")
    if game_date_str:
        try:
            gd = datetime.strptime(game_date_str, "%Y-%m-%d")
            days_old = (NOW.replace(tzinfo=None) - gd).days
            if days_old > 14:
                print(f"    Dropping stale highlights from {game_date_str} ({days_old}d old)")
                return {"available": False}
        except:
            pass

    # Also check against the most recent game in the schedule
    if recent:
        try:
            last_gd = datetime.strptime(recent[0].get("game_date", ""), "%Y-%m-%d")
            days_since_last = (NOW.replace(tzinfo=None) - last_gd).days
            if days_since_last > 14:
                return {"available": False}
        except:
            pass
    else:
        # No recent games at all ‚Äî team is deep in offseason, don't show stale highlights
        print(f"    No recent games ‚Äî hiding existing highlights")
        return {"available": False}

    return existing_hl


# === MAIN BUILD FUNCTION ===

def build_data():
    """Build the complete data.json structure."""
    print(f"Building data for {TODAY_DISPLAY}...")

    # Load existing data as fallback
    existing = {}
    try:
        with open(DATA_FILE, "r") as f:
            existing = json.load(f)
    except:
        pass

    db = {
        "meta": {
            "updated": NOW.isoformat(),
            "date_display": TODAY_DISPLAY,
        },
    }

    # === PHASE 1: FETCH ALL ESPN DATA (verified, factual) ===
    all_upcoming = []
    all_team_facts = {}  # Collected verified facts for each team

    for team_key, cfg in TEAMS.items():
        print(f"\n--- {cfg['full_name']} [ESPN Data] ---")

        # Get team info (record, standing summary) from ESPN
        print(f"  Fetching team info...")
        team_info = get_team_info(team_key)
        if team_info.get("record"):
            print(f"  Record: {team_info['record']}")
        if team_info.get("standing_summary"):
            print(f"  Standing: {team_info['standing_summary']}")

        # Get standings details from ESPN
        print(f"  Fetching standings...")
        standings = get_standings(team_key)
        if standings:
            print(f"  Standings data: {len(standings)} fields")

        # Get schedule/results from ESPN
        print(f"  Fetching schedule...")
        recent, upcoming = get_team_schedule(team_key)
        all_upcoming.extend(upcoming)
        if recent:
            print(f"  Recent: {len(recent)} games (last: {recent[0]['result']} {recent[0]['team_score']}-{recent[0]['opp_score']} vs {recent[0]['opp_name']})")
        if upcoming:
            print(f"  Upcoming: {len(upcoming)} games")

        # Detect season phase (pass standings for playoff seed detection)
        phase_info = detect_season_phase(team_key, recent, upcoming, standings)
        print(f"  Season phase: {phase_info['label']} (recency: {phase_info['recency_days']}d)")

        # Build verified facts string (now includes phase context)
        verified_facts = build_verified_facts(team_key, team_info, standings, recent, upcoming, phase_info)
        print(f"  Verified facts block: {len(verified_facts)} chars")

        # Store all facts for later use
        all_team_facts[team_key] = {
            "team_info": team_info,
            "standings": standings,
            "recent": recent,
            "upcoming": upcoming,
            "verified_facts": verified_facts,
            "phase_info": phase_info,
        }

    # === PHASE 2: DISCOVER ARTICLES (API-first, no AI needed) ===
    all_team_articles = {}  # Real articles with verified URLs per team
    for team_key, cfg in TEAMS.items():
        print(f"\n--- {cfg['full_name']} [Article Discovery] ---")
        facts = all_team_facts[team_key]
        recent = facts["recent"]
        phase_info = facts["phase_info"]
        articles = discover_articles_for_team(team_key, recent, phase_info)
        all_team_articles[team_key] = articles
        print(f"  Total verified articles: {len(articles)}")
        for a in articles[:4]:
            print(f"    [{a['source']}] {a['headline'][:60]}")

    # === PHASE 3: GENERATE AI CONTENT (Perplexity for editorial ONLY) ===
    teams_data = {}

    for team_key, cfg in TEAMS.items():
        print(f"\n--- {cfg['full_name']} [AI Content] ---")
        facts = all_team_facts[team_key]
        team_info = facts["team_info"]
        standings = facts["standings"]
        recent = facts["recent"]
        upcoming = facts["upcoming"]
        verified_facts = facts["verified_facts"]
        phase_info = facts["phase_info"]

        # Generate LOTL via Perplexity WITH verified facts AND phase context
        print(f"  Generating Lay of the Land (phase: {phase_info['label']})...")
        lotl_text = generate_lotl(team_key, verified_facts, phase_info, team_info, recent, upcoming)

        # === POST-GENERATION FACT-CHECK ===
        if lotl_text:
            lotl_text = fact_check_lotl(lotl_text, team_key, team_info, recent, upcoming, phase_info, standings)

        # Select articles for The Latest from discovered articles (no Perplexity needed)
        articles = select_the_latest(all_team_articles.get(team_key, []), count=4)
        print(f"  Selected {len(articles)} articles for The Latest")

        # Phase 2: regenerate editorial deks for The Latest via Perplexity.
        # Falls back to the discovered-article dek on any failure.
        for art in articles:
            new_dek = generate_editorial_dek(
                art.get("headline", ""), team_key, phase_info, team_info,
                existing_dek=art.get("dek", ""),
            )
            if new_dek:
                art["dek"] = new_dek

        # Find highlight video (only if team played within last 48 hours)
        highlights = {"available": False}
        if recent:
            last_game = recent[0]
            try:
                last_game_date = datetime.strptime(last_game.get("game_date", ""), "%Y-%m-%d")
                days_since_game = (NOW.replace(tzinfo=None) - last_game_date).days
            except:
                days_since_game = 999

            if days_since_game <= 2:
                print(f"  Finding highlight video for vs {last_game['opp_name']}...")
                hl_url = find_highlight_url(team_key, last_game["opp_name"])
                if hl_url:
                    result_badge = f"{'W' if last_game['result'] == 'W' else 'L'} {last_game['team_score']}-{last_game['opp_score']}"
                    highlights = {
                        "available": True,
                        "title": f"{last_game['opp_name']} vs. {cfg['full_name'].split()[-1]} — Full Highlights",
                        "subtitle": f"{cfg['league']} &middot; {last_game['date']}, {NOW.year}",
                        "result_badge": result_badge,
                        "result_class": "w" if last_game["result"] == "W" else "l",
                        "url": hl_url,
                        "game_date": last_game.get("game_date", ""),
                    }
            else:
                print(f"  Skipping highlights ‚Äî last game was {days_since_game} days ago")

        # Build key numbers from ESPN data
        key_numbers = build_key_numbers(team_key, team_info, standings, recent)

        # Use existing data as fallback ONLY for fields ESPN couldn't provide
        existing_team = existing.get("teams", {}).get(team_key, {})

        # Record comes from ESPN now, not fallback
        record = team_info.get("record", existing_team.get("record", ""))
        standing_summary = team_info.get("standing_summary", existing_team.get("detail", ""))

        team_entry = {
            "full_name": cfg["full_name"],
            "league": cfg["league"],
            "record": record,
            "detail": standing_summary,
            "live_strip": None,  # Will be set from ticker generation
            "lotl": {
                "label": f"What's Going On With the {cfg['full_name'].split()[-1]}",
                "body": lotl_text or existing_team.get("lotl", {}).get("body", ""),
                "updated": f"Updated {TODAY_DISPLAY}",
            },
            "key_numbers": key_numbers if key_numbers else existing_team.get("key_numbers", []),
            "recent_results": recent if (recent and is_recent_enough(recent)) else [],
            "last_game_highlights": _resolve_highlights(highlights, existing_team, recent),
            "the_latest": articles if articles else existing_team.get("the_latest", []),
            "standings": existing_team.get("standings", {}),
        }

        # Phase 2: draft board (Leafs + Commanders during offseason phases)
        draft_board = build_draft_board(team_key, phase_info, team_info)
        if draft_board:
            team_entry["draft_board"] = draft_board

        # Build full standings tables from ESPN API (replaces stale existing data)
        print(f"  Building standings tables...")
        fresh_standings = build_full_standings(team_key)
        if fresh_standings:
            team_entry["standings"] = fresh_standings
            print(f"    Built {len(fresh_standings.get('tabs', []))} tabs: {', '.join(fresh_standings.get('tabs', []))}")
        else:
            print(f"    WARNING: Could not build standings ‚Äî keeping existing")

        # Set section label based on phase ‚Äî eliminated/offseason get "Offseason Intel"
        phase_id_label = phase_info.get("phase", "")
        if phase_id_label in ("eliminated", "season_ended", "offseason", "deep_offseason",
                               "draft_free_agency", "pre_draft", "combine_free_agency"):
            team_entry["the_latest_label"] = "Offseason Intel"
        else:
            team_entry["the_latest_label"] = "The Latest"

        teams_data[team_key] = team_entry

    db["teams"] = teams_data

    # === PHASE 4: HOMEPAGE CONTENT (built from real discovered articles) ===
    print("\n--- Building homepage stories from discovered articles ---")
    stories = build_homepage_stories_from_articles(all_team_facts, all_team_articles)
    print(f"  Built {len(stories)} homepage stories from real articles")
    # Phase 2: regenerate editorial deks for homepage stories via Perplexity.
    for s in stories:
        t_key = s.get("team")
        if not t_key:
            continue
        s_facts = all_team_facts.get(t_key, {})
        new_dek = generate_editorial_dek(
            s.get("headline", ""), t_key,
            s_facts.get("phase_info"), s_facts.get("team_info"),
            existing_dek=s.get("dek", ""),
        )
        if new_dek:
            s["dek"] = new_dek

    if stories:
        for s in stories:
            print(f"    [{s['team']}] {s['headline'][:60]} -> {s['link'][:60]}")

        if len(stories) >= 1:
            db["featured"] = stories[0]
        if len(stories) >= 3:
            db["two_up"] = [stories[1], stories[2]]
        elif len(stories) >= 2:
            db["two_up"] = [stories[1]]
        if len(stories) >= 4:
            db["extra_story"] = stories[3]
    else:
        # No articles discovered at all ‚Äî keep existing
        print("  WARNING: No articles discovered ‚Äî keeping existing homepage stories")
        db["featured"] = existing.get("featured", {})
        db["two_up"] = existing.get("two_up", [])
        db["extra_story"] = existing.get("extra_story", {})

    # Ticker ‚Äî generated from REAL ESPN data now, not stale fallback
    print("\n--- Generating ticker from ESPN data ---")
    db["ticker"] = generate_ticker(all_team_facts, all_team_articles)
    print(f"  Generated {len(db['ticker'])} ticker items")

    # At a Glance ‚Äî build from REAL ESPN team data
    db["at_a_glance"] = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        facts = all_team_facts.get(team_key, {})
        ti = facts.get("team_info", {})
        recent = facts.get("recent", [])
        upcoming = facts.get("upcoming", [])
        standings_info = facts.get("standings", {})
        record = ti.get("record", "")
        standing = ti.get("standing_summary", "")

        # Determine if team is in-season using PHASE detection (fixes playoff gap issue)
        phase_info = facts.get("phase_info", {})
        phase_id = phase_info.get("phase", "")
        team_in_season = phase_id in ("regular_season", "regular_season_late", "playoffs",
                                       "preseason", "spring_training")
        if not team_in_season:
            team_in_season = bool(upcoming) or is_recent_enough(recent, max_days=30)

        # Build a smart status line
        status = standing or ""
        status_class = "muted"
        if team_in_season and recent:
            g = recent[0]
            if g["result"] == "W":
                status_class = "green"
            else:
                status_class = "red"
        elif not team_in_season:
            # Use phase label instead of generic "Offseason"
            status = standing or phase_info.get("label", "Offseason")

        # Build a key stat
        stat = ""
        streak = standings_info.get("streak", "")
        if streak and team_in_season:
            stat = f"Streak: {streak}"
        elif recent and team_in_season:
            # Calculate consecutive streak (not total)
            streak_type = recent[0]["result"]
            streak_count = 0
            for g in recent:
                if g["result"] == streak_type:
                    streak_count += 1
                else:
                    break
            stat = f"{'W' if streak_type == 'W' else 'L'}{streak_count}"

        db["at_a_glance"].append({
            "team": team_key,
            "name": TEAMS[team_key]["full_name"].split()[-1],
            "logo": TEAMS[team_key]["logo"],
            "record": record,
            "status": status,
            "status_class": status_class,
            "stat": stat,
        })

    # Today's Slate ‚Äî build from upcoming games
    db["today_slate"] = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        cfg = TEAMS[team_key]
        today_games = [g for g in all_upcoming if g["team"] == team_key and g.get("game_date") == TODAY]
        if today_games:
            g = today_games[0]
            db["today_slate"].append({
                "team": team_key,
                "logo": cfg["logo"],
                "matchup": g["opp"].replace("at ", f"{cfg['full_name'].split()[-1]} at ").replace("vs. ", f"{cfg['full_name'].split()[-1]} vs. "),
                "detail": f"{g['time']}",
                "channel": g.get("tv", ""),
                "off": False,
            })
        else:
            # Off day ‚Äî find next game
            team_upcoming = [g for g in all_upcoming if g["team"] == team_key and g.get("game_date", "") > TODAY]
            if team_upcoming:
                next_g = team_upcoming[0]
                db["today_slate"].append({
                    "team": team_key,
                    "logo": cfg["logo"],
                    "matchup": cfg["full_name"].split()[-1],
                    "detail": f"Next: {next_g['day']} {next_g['opp']} {next_g['time']}",
                    "channel": "",
                    "off": True,
                })
            else:
                # No upcoming games ‚Äî use phase-aware label (not always "Offseason")
                phase_info = all_team_facts.get(team_key, {}).get("phase_info", {})
                phase_id = phase_info.get("phase", "")
                phase_label = phase_info.get("label", "Offseason")
                # If team is in playoffs but between rounds/series, say so
                if "playoffs" in phase_id:
                    detail_text = "Playoffs — Schedule TBD"
                elif "draft" in phase_id or "pre_draft" in phase_id:
                    detail_text = phase_label
                else:
                    detail_text = phase_label
                db["today_slate"].append({
                    "team": team_key,
                    "logo": cfg["logo"],
                    "matchup": cfg["full_name"].split()[-1],
                    "detail": detail_text,
                    "channel": "",
                    "off": True,
                })

    # Week Ahead ‚Äî combine all upcoming games for next 7 days + playoff context
    print(f"\n--- Building Week Ahead ---")
    all_upcoming.sort(key=lambda x: x.get("game_date", ""))
    week_games = [
        {k: v for k, v in g.items() if k != "game_date"}
        for g in all_upcoming
    ]
    print(f"  Found {len(week_games)} scheduled games in next 7 days")

    # Add placeholder entries for playoff teams without specific game times
    teams_with_games = {g["team"] for g in all_upcoming}
    week_note_parts = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        cfg = TEAMS[team_key]
        phase_info = all_team_facts.get(team_key, {}).get("phase_info", {})
        phase_id = phase_info.get("phase", "")

        if team_key not in teams_with_games:
            if "playoffs" in phase_id:
                # Add a TBD playoff entry so the week ahead isn't empty
                week_games.append({
                    "day": "TBD",
                    "team": team_key,
                    "logo": cfg["logo"],
                    "name": cfg["full_name"].split()[-1],
                    "opp": "Playoffs",
                    "time": "TBD",
                    "tv": "",
                })
                short_name = cfg["full_name"].split()[-1]
                week_note_parts.append(f"{short_name} playoff schedule TBD")
                print(f"  Added playoff TBD for {short_name}")
            elif "draft" in phase_id or "pre_draft" in phase_id:
                week_note_parts.append(f"{cfg['full_name'].split()[-1]}: {phase_info.get('label', 'Offseason')}")
            elif "offseason" in phase_id or "ended" in phase_id:
                pass  # Don't clutter week ahead with offseason teams

    week_note = "; ".join(week_note_parts) if week_note_parts else ""

    db["week_ahead"] = {
        "games": week_games,
        "note": week_note,
    }
    print(f"  Week Ahead: {len(week_games)} entries" + (f" (note: {week_note})" if week_note else ""))

    return db


# === MAIN ===
def main():
    print(f"=== The Morning Skate Daily Update ===")
    print(f"Date: {TODAY_DISPLAY}")
    print(f"Time: {NOW.strftime('%I:%M %p %Z')}")
    print()

    db = build_data()

    # Sanitize all content for ASCII safety
    db = sanitize_entry(db)

    # Write data.json (UTF-8, ensure_ascii=False — Section 0.3 bans numeric
    # HTML entities like &#8212;, which ensure_ascii=True used to produce)
    print(f"\nWriting {DATA_FILE}...")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

    # Verify no banned chars / entities per Section 0.3
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    banned_markers = {
        "em-dash U+2014": "\u2014",
        "en-dash U+2013": "\u2013",
        "&mdash;": "&mdash;",
        "&ndash;": "&ndash;",
        "&#8212;": "&#8212;",
        "&#8211;": "&#8211;",
        "&middot; literal": "&middot;",
    }
    found = {name: text.count(s) for name, s in banned_markers.items() if text.count(s) > 0}
    if found:
        print(f"\n  FATAL: banned chars/entities in output: {found}")
        sys.exit(1)

    size_kb = len(text.encode("utf-8")) / 1024
    print(f"  Done. {size_kb:.1f} KB written, banned-char check passed.")
    print(f"\n=== Update complete ===")


if __name__ == "__main__":
    main()
