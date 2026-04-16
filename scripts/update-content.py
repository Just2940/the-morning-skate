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
from urllib.parse import quote_plus

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
UNICODE_TO_ENTITY = {
    "\u2014": "&mdash;", "\u2013": "&ndash;", "\u00d7": "&times;",
    "\u2264": "&le;", "\u2265": "&ge;", "\u2192": "&rarr;", "\u2190": "&larr;",
    "\u00b7": "&middot;", "\u2022": "&#8226;", "\u2026": "&hellip;",
    "\u2018": "&lsquo;", "\u2019": "&rsquo;", "\u201c": "&ldquo;", "\u201d": "&rdquo;",
    "\u2122": "&trade;", "\u00a9": "&copy;", "\u00ae": "&reg;",
    "\u00e9": "&eacute;", "\u00e8": "&egrave;", "\u00e0": "&agrave;",
    "\u00e7": "&ccedil;", "\u00f1": "&ntilde;", "\u00fc": "&uuml;",
}

def sanitize_ascii(text):
    if not isinstance(text, str):
        return text
    for char, entity in UNICODE_TO_ENTITY.items():
        text = text.replace(char, entity)
    return text.encode("ascii", "xmlcharrefreplace").decode("ascii")

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
    if standings:
        seed = standings.get("playoffSeed", "")
        clincher = standings.get("clincher", "")
        if seed and str(seed) not in ("", "0"):
            has_playoff_seed = True
        if clincher:
            has_clinched = True

    if league == "NHL":
        if has_upcoming and has_recent_game:
            # Mid-April onward with games = playoffs or late regular season
            if month >= 4 and month <= 6:
                return _phase("playoffs", league, cfg)
            return _phase("regular_season", league, cfg)
        elif month >= 4 and month <= 6:
            # April-June: NHL playoffs. Schedule feed may have gaps between rounds.
            if has_playoff_seed or has_clinched:
                return _phase("playoffs", league, cfg)
            elif has_upcoming or has_recent_game or last_game_days_ago <= 14:
                return _phase("playoffs", league, cfg)
            else:
                return _phase("season_ended", league, cfg)
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
        # NBA playoff detection: use standings data (playoffSeed, clincher) as
        # the authoritative signal. The schedule feed may be empty between
        # play-in and Round 1, but the team is still in the playoffs.
        if has_upcoming and has_recent_game:
            if month >= 4 and month <= 6:
                return _phase("playoffs", league, cfg)
            return _phase("regular_season", league, cfg)
        elif month >= 4 and month <= 6:
            # April-June: check if team is in playoffs via ANY signal
            # 1. Standings data says they have a playoff seed or clinched
            # 2. Last game was within 14 days (between rounds / play-in gap)
            # 3. Team has upcoming games scheduled
            # The regular season schedule feed goes empty after the play-in,
            # so we can't rely on has_recent_game alone.
            if has_playoff_seed or has_clinched:
                return _phase("playoffs", league, cfg)
            elif has_upcoming:
                return _phase("playoffs", league, cfg)
            elif has_recent_game or last_game_days_ago <= 14:
                return _phase("playoffs", league, cfg)
            else:
                return _phase("season_ended", league, cfg)
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
        parts.append(f"<strong>The {short} {result_word} the {g['opp_name']} {g['team_score']}&ndash;{g['opp_score']}</strong>, moving to {record} on the season.")
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
            parts.append(f"That&rsquo;s {streak_count} straight {word}.")

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
    url = f"https://site.api.espn.com/apis/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/standings"
    data = espn_fetch(url)
    if not data:
        return {}

    team_id = cfg["espn_team_id"]
    team_abbr = cfg["espn_abbr"]

    # Navigate the standings structure: children > standings > entries
    for group in data.get("children", []):
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
            "headline": f"{cfg['full_name'].split()[-1]} {result_word} {opp} {ts}&ndash;{os_score}",
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
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            source_el = item.find("source")
            source_name = source_el.text if source_el is not None else ""

            if not title_raw or not link:
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


def discover_articles_for_team(team_key, recent, phase_info):
    """Master article discovery: ESPN API + game recaps + league APIs + Google News RSS.
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

    # Layer 4: Google News RSS (multi-source: TSN, Sportsnet, The Athletic,
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
    STRONGLY prioritizes source diversity ‚Äî the whole point of the app
    is that your dad gets articles from TSN, Sportsnet, The Athletic,
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
    used_sources = set()
    used_urls = set()

    # Separate into ESPN and non-ESPN buckets
    espn_articles = [a for a in all_articles if a.get("source") == "ESPN"]
    non_espn_articles = [a for a in all_articles if a.get("source") != "ESPN"]

    # Pass 1: Pick the BEST game recap (if any) ‚Äî ESPN recaps are great for this
    for article in all_articles:
        if len(selected) >= 1:
            break
        if article.get("type") == "recap":
            selected.append(make_entry(article))
            used_sources.add(article.get("source"))
            used_urls.add(article.get("link"))

    # Pass 2: Fill with NON-ESPN articles (diverse sources)
    for article in non_espn_articles:
        if len(selected) >= count:
            break
        source = article.get("source", "")
        url = article.get("link", "")
        if url in used_urls:
            continue
        if source in used_sources and len(non_espn_articles) > count:
            continue  # Skip duplicate sources if we have enough variety
        selected.append(make_entry(article))
        used_sources.add(source)
        used_urls.add(url)

    # Pass 3: Fill remaining slots with ESPN articles
    for article in espn_articles:
        if len(selected) >= count:
            break
        url = article.get("link", "")
        if url in used_urls:
            continue
        selected.append(make_entry(article))
        used_sources.add(article.get("source"))
        used_urls.add(url)

    # Pass 4: If STILL not enough (unlikely), take anything
    for article in all_articles:
        if len(selected) >= count:
            break
        url = article.get("link", "")
        if url not in used_urls:
            selected.append(make_entry(article))
            used_urls.add(url)

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
        # but fall back to ESPN (which has guaranteed-working URLs)
        best_article = articles[0]
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
            dek = dek[:247] + "..."

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

    # Explicit playoff opponent from upcoming schedule (most reliable source)
    if phase_info and "playoffs" in phase_info.get("phase", ""):
        if upcoming:
            opp = upcoming[0].get("opp", "").replace("vs. ", "").replace("at ", "").strip()
            if opp:
                facts.append(f"PLAYOFF OPPONENT: {opp} (from ESPN schedule ‚Äî use THIS name, not any other)")
        facts.append(f"NOTE: This team is in the PLAYOFFS. All content must reflect playoff urgency.")

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


def generate_ticker(all_team_facts):
    """Generate ticker items from verified ESPN data ‚Äî no AI needed.
    Each item must have: badge, badge_style, text (matching index.html renderTicker)."""
    ticker_items = []

    # Badge styles per team (CSS variable colors)
    BADGE_STYLES = {
        "leafs": "background:var(--leafs);color:#fff",
        "jays": "background:var(--jays);color:#fff",
        "raptors": "background:var(--raptors);color:#fff",
        "commanders": "background:var(--commanders);color:#fff",
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
        # Phase detection already considers standings, schedule, and calendar
        phase_id = phase_info.get("phase", "")
        is_in_season = phase_id in ("regular_season", "regular_season_late", "playoffs",
                                     "preseason", "spring_training")
        # Also check schedule data as backup
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
                    "text": f"{team_name} {result_word} {g['opp_name']} {g['team_score']}&ndash;{g['opp_score']}"
                })

        # Record + standing
        standing = team_info.get("standing_summary", "")
        if record and standing:
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} ({record}) &mdash; {standing}"
            })
        elif record:
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} record: {record}"
            })
        elif not is_in_season:
            # Offseason ‚Äî show phase-appropriate label (not always "Offseason")
            phase_label = phase_info.get("label", "Offseason")
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} &mdash; {phase_label}"
            })

        # Next game (only if in-season / has upcoming)
        if upcoming:
            ng = upcoming[0]
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"Next: {team_name} {ng['opp']} &mdash; {ng['day']} {ng['time']}"
            })

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

CRITICAL CONTENT RULES:
- You MUST produce a finished column paragraph. Do NOT explain what you would need to write the column.
- Do NOT say "I don't have sufficient search results" or anything similar. Write the column with the facts you have.
- If your web search returns limited results, write the column using the VERIFIED FACTS provided ‚Äî they are more than enough.
- A column based on verified facts alone is infinitely better than no column at all.

FORMATTING:
- ONE paragraph, 150-200 words. Dense, polished prose ‚Äî no filler.
- Bold the single most important recent event with <strong> tags.
- Bold the forward-looking detail at the end with <strong> tags.
- Use HTML entities: &mdash; for em dashes, &ndash; for en dashes, &rsquo; for apostrophes.
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

Your paragraph must include:
1. An opening that captures the team's CURRENT narrative arc ‚Äî not "The [Team] are..." but something with edge and voice
2. The most important RECENT development (bold with <strong> tags) ‚Äî this must be something from the last 1-3 days, NOT old news
3. Context: record, standings from the VERIFIED FACTS, woven naturally into the prose
4. A key player thread ‚Äî who's hot, who's hurt, who's the story RIGHT NOW
5. A forward-looking close bolded with <strong> tags ‚Äî next game, next milestone, or what to watch for

IMPORTANT: Every fact you cite must be CURRENT as of {today_str}. Do not reference game results from weeks or months ago as if they just happened.

Write 150-200 words of polished sports column prose. Every sentence should earn its place."""

    result = perplexity_search(prompt, system_prompt)
    result = _clean_perplexity_prose(result)

    # === GARBAGE FILTER ===
    if is_perplexity_failure(result):
        print(f"  WARNING: Perplexity returned garbage for {team_key} LOTL ‚Äî using ESPN fallback")
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
                # Find all record-like patterns: "6-9", "6\u20139", "6\u20149", "6&ndash;9", "6&mdash;9"
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
                                text = text[:m.start()] + correct_record.replace("-", "&ndash;") + text[m.end():]
            except (ValueError, IndexError):
                pass

    # 2. Check for offseason content being written as if team is playing
    if "offseason" in phase_id or "ended" in phase_id or "draft" in phase_id:
        game_phrases = ["last night", "tonight's game", "yesterday's game", "beat the", "fell to the", "lost to the"]
        text_lower = text.lower()
        for phrase in game_phrases:
            if phrase in text_lower:
                # Only flag if there are no recent games within 14 days
                if not recent or not is_recent_enough(recent, max_days=14):
                    problems.append(f"Offseason team has active game language: '{phrase}'")

    # 3. Check for playoff opponent correctness (if in playoffs with upcoming games)
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
            headline = f"{cfg['full_name'].split()[-1]} {result_word} {g['opp_name']} {g['team_score']}&ndash;{g['opp_score']} &mdash; Record Moves to {record}"
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
            headline = f"{cfg['full_name'].split()[-1]} in the Playoffs &mdash; {record} Heading Into the Postseason"
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
            headline = f"{cfg['full_name'].split()[-1]} Update &mdash; {record}, {standing or topic}"
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
      "headline": "10-18 word punchy headline with &mdash; for drama",
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

Use HTML entities (&mdash; &ndash; &middot;) not unicode. All URLs must be real ‚Äî search for real articles published in the last 48 hours. Do NOT fabricate URLs. Do NOT include citation numbers like [1], [2] in any text fields."""

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
    search_name = cfg["youtube_search_name"]
    return f"https://www.youtube.com/{cfg['youtube_channel']}/search?query={search_name}+{opp_name.replace(' ', '+')}+highlights"


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
Use HTML entities (&mdash; &ndash;) not unicode.
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
                result_badge = f"{'W' if last_game['result'] == 'W' else 'L'} {last_game['team_score']}-{last_game['opp_score']}"
                highlights = {
                    "available": True,
                    "title": f"{last_game['opp_name']} vs. {cfg['full_name'].split()[-1]} &mdash; Full Highlights",
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

        # Preserve special fields
        if "the_latest_label" in existing_team:
            team_entry["the_latest_label"] = existing_team["the_latest_label"]

        teams_data[team_key] = team_entry

    db["teams"] = teams_data

    # === PHASE 4: HOMEPAGE CONTENT (built from real discovered articles) ===
    print("\n--- Building homepage stories from discovered articles ---")
    stories = build_homepage_stories_from_articles(all_team_facts, all_team_articles)
    print(f"  Built {len(stories)} homepage stories from real articles")

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
    db["ticker"] = generate_ticker(all_team_facts)
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
                    detail_text = "Playoffs &mdash; Schedule TBD"
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

    # Write data.json
    print(f"\nWriting {DATA_FILE}...")
    with open(DATA_FILE, "w", encoding="ascii") as f:
        json.dump(db, f, indent=2, ensure_ascii=True)

    # Verify pure ASCII
    with open(DATA_FILE, "rb") as f:
        raw = f.read()
        non_ascii = [i for i, b in enumerate(raw) if b > 127]
        if non_ascii:
            print(f"\n  FATAL: {len(non_ascii)} non-ASCII bytes in output!")
            sys.exit(1)

    size_kb = len(raw) / 1024
    print(f"  Done. {size_kb:.1f} KB written, pure ASCII verified.")
    print(f"\n=== Update complete ===")


if __name__ == "__main__":
    main()
