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
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

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


def build_verified_facts(team_key, team_info, standings, recent, upcoming):
    """Build a verified facts block from ESPN data to inject into AI prompts.
    This prevents Perplexity from hallucinating records, standings, or results."""
    cfg = TEAMS[team_key]
    facts = []

    facts.append(f"TEAM: {cfg['full_name']} ({cfg['league']})")

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

    # Recent results
    if recent:
        facts.append("RECENT RESULTS (most recent first):")
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
            facts.append(f"CURRENT RUN: {streak_count} game {'win' if streak_type == 'W' else 'loss'} streak (from recent results)")

    # Upcoming
    if upcoming:
        next_game = upcoming[0]
        facts.append(f"NEXT GAME: {next_game['day']} {next_game['opp']} at {next_game['time']}")

    # Season status indicators
    record_stats = team_info.get("record_stats", {})
    if record_stats:
        # Check for playoff elimination or clinch
        if record_stats.get("playoffSeed"):
            facts.append(f"PLAYOFF SEED: {record_stats['playoffSeed']}")

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
        record = team_info.get("record", "")

        # Determine if team is in-season: has a recent game within 14 days OR upcoming games
        is_in_season = bool(upcoming)
        if recent:
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
            # Offseason ‚Äî show a forward-looking note instead
            ticker_items.append({
                "badge": league,
                "badge_style": badge_style,
                "text": f"{team_name} &mdash; Offseason"
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


def generate_lotl(team_key, verified_facts=""):
    """Generate a Lay of the Land paragraph using Perplexity.
    verified_facts: pre-built string of ESPN-verified data that MUST be used for stats."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")

    system_prompt = """You are the lead sports columnist for "The Morning Skate," a daily briefing read by a 65-year-old father on his phone each morning. Write like a veteran newspaper columnist ‚Äî someone who's covered this beat for decades, knows the history, and isn't afraid of a sharp opinion.

YOUR VOICE:
- Write like a broadsheet sports columnist: authoritative, opinionated, and vivid. Think Cathal Kelly (Globe and Mail), Bob Ryan (Boston Globe), or the best of Sports Illustrated's long-form writers.
- Open with the narrative, not the team name. Lead with what matters ‚Äî a turning point, a collapse, a spark of hope.
- Use strong verbs and concrete images. "The bullpen imploded" not "the bullpen struggled." "Matthews has gone invisible" not "Matthews hasn't been producing."
- Don't hedge. If the season is over, say it plainly. If a player is carrying the team, give them their due.
- Use em dashes for dramatic pauses and parenthetical asides. Use short, punchy sentences between longer ones for rhythm.
- Include exactly one or two telling statistics, woven naturally into the prose ‚Äî never a stats dump.
- End with a forward look: what's next, what to watch for, why it matters.

CRITICAL ACCURACY RULES:
- You will be given VERIFIED FACTS below. These are pulled directly from ESPN and are CORRECT.
- You MUST use the exact record, scores, and standings from the VERIFIED FACTS. Do NOT guess or recall different numbers.
- If the verified facts say the record is "32-36-14", you MUST use "32-36-14" ‚Äî not a different number.
- If the verified facts show game results, use those exact scores.
- You may search for additional COLOR and NARRATIVE details (player performances, quotes, storylines), but ALL statistics (record, scores, standings, streaks) MUST come from the VERIFIED FACTS section.

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
{facts_block}
Search for additional {cfg['full_name']} narrative details ‚Äî key player performances, quotes, storylines, injuries, trades. But for ALL statistics (record, standings, scores, streaks), use ONLY the verified facts above. Do NOT invent or recall different numbers.

Your paragraph must include:
1. An opening that captures the team's current narrative arc ‚Äî not "The [Team] are..." but something with edge and voice
2. The single most important thing that happened in the last 1-3 days (bold with <strong> tags)
3. Context: record, streak, division/conference standing from the VERIFIED FACTS, woven naturally into the prose
4. A key player thread ‚Äî who's hot, who's hurt, who's the story
5. A forward-looking close: the next game, series, or milestone, bolded with <strong> tags

Write 150-200 words of polished sports column prose. Every sentence should earn its place."""

    result = perplexity_search(prompt, system_prompt)
    if result:
        # Clean up any markdown that Perplexity might add
        result = result.strip()
        result = re.sub(r'^\*\*.*?\*\*\s*\n*', '', result)  # Remove bold headers
        # Convert markdown bold **text** to <strong>text</strong>
        import itertools
        parts = result.split("**")
        if len(parts) > 1:
            rebuilt = parts[0]
            for i, part in enumerate(parts[1:], 1):
                tag = "<strong>" if i % 2 == 1 else "</strong>"
                rebuilt += tag + part
            result = rebuilt
        # Remove Perplexity citation markers like [1], [2], [1][2], [3][4][5], etc.
        result = re.sub(r'\[\d+\]', '', result)
        # Clean up any double spaces left behind after removing citations
        result = re.sub(r'  +', ' ', result)
        # Fix any unclosed strong tags
        open_count = result.count("<strong>")
        close_count = result.count("</strong>")
        if open_count > close_count:
            result += "</strong>" * (open_count - close_count)
    return result


def generate_featured_and_stories(all_team_facts):
    """Use Perplexity to identify the top 3-4 stories across all four teams.
    all_team_facts: dict of verified ESPN data per team to prevent hallucination."""
    today_str = NOW.strftime("%B %d, %Y")

    # Build a summary of verified facts for all teams
    facts_summary = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        fd = all_team_facts.get(team_key, {})
        cfg = TEAMS[team_key]
        ti = fd.get("team_info", {})
        recent = fd.get("recent", [])
        upcoming = fd.get("upcoming", [])

        line = f"- {cfg['full_name']} ({cfg['league']}): Record {ti.get('record', 'N/A')}, {ti.get('standing_summary', 'N/A')}"
        if recent:
            g = recent[0]
            line += f". Last game: {'W' if g['result'] == 'W' else 'L'} {g['team_score']}-{g['opp_score']} vs {g['opp_name']} ({g['date']})"
        if upcoming:
            ng = upcoming[0]
            line += f". Next: {ng['opp']} {ng['day']}"
        facts_summary.append(line)

    facts_block = "\n".join(facts_summary)

    prompt = f"""As of the morning of {today_str}, identify the TOP 3-4 most important sports stories across these four teams.

=== VERIFIED TEAM STATUS (from ESPN ‚Äî use these exact records and scores) ===
{facts_block}
=== END VERIFIED STATUS ===

Focus on what happened YESTERDAY and LAST NIGHT ‚Äî game results from last night take priority. Use the EXACT scores and records from the verified status above. Search for additional narrative details (player performances, context, storylines).

For each story, provide in this EXACT JSON format (no markdown, just raw JSON):
{{
  "stories": [
    {{
      "team": "leafs|jays|raptors|commanders",
      "kicker": "Team Name &middot; Topic",
      "headline": "10-18 word punchy headline with &mdash; for drama",
      "dek": "2-3 sentences, 40-60 words with context and forward-looking detail",
      "source": "Source name like ESPN or NHL.com",
      "link": "Real URL to the best article covering this story"
    }}
  ]
}}

The FIRST story should be the biggest ‚Äî playoff games, major trades, dramatic results. Use HTML entities (&mdash; &ndash; &middot;) not unicode. Include real, verified URLs. Do NOT include citation numbers like [1], [2] in any text fields."""

    result = perplexity_search(prompt)
    if not result:
        return None

    # Try to extract JSON from the response
    try:
        # Remove citation markers before parsing JSON
        cleaned = re.sub(r'\[\d+\]', '', result)
        json_match = re.search(r'\{[\s\S]*"stories"[\s\S]*\}', cleaned)
        if json_match:
            data = json.loads(json_match.group())
            # Clean up any double spaces in text fields
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


def find_news_articles(team_key):
    """Use Perplexity to find the 3 most recent news articles."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")

    prompt = f"""Find the 3 most recent and important news articles about the {cfg['full_name']} as of the morning of {today_str}. Prioritize articles from yesterday and last night ‚Äî especially game recaps from any games played last night.

Return in this EXACT JSON format (no markdown, just raw JSON):
{{
  "articles": [
    {{
      "source": "Source name (ESPN, NHL.com, TSN, Sportsnet, etc.)",
      "source_class": "espn|nba|tsn|web|nfl|hogs",
      "headline": "A compelling headline in 10-20 words",
      "dek": "1-2 sentence summary, 20-40 words",
      "date": "Month Day, Year format",
      "link": "The real, verified URL to the article"
    }}
  ]
}}

Prefer Tier 1 sources: league official sites, ESPN, TSN, Sportsnet, The Athletic.
Include a mix: game recaps, analysis, injury/roster news.
Use HTML entities (&mdash; &ndash;) not unicode characters.
Do NOT include citation numbers like [1], [2] in any text fields.
All URLs must be real and currently accessible."""

    result = perplexity_search(prompt)
    if not result:
        return None

    try:
        # Remove citation markers before parsing JSON
        cleaned = re.sub(r'\[\d+\]', '', result)
        json_match = re.search(r'\{[\s\S]*"articles"[\s\S]*\}', cleaned)
        if json_match:
            data = json.loads(json_match.group())
            # Clean up any double spaces in text fields
            for article in data.get("articles", []):
                for field in ("headline", "dek"):
                    if field in article:
                        article[field] = re.sub(r'  +', ' ', article[field]).strip()
            return data
    except json.JSONDecodeError:
        print(f"  WARNING: Could not parse articles JSON for {team_key}")
    return None


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

        # Build verified facts string
        verified_facts = build_verified_facts(team_key, team_info, standings, recent, upcoming)
        print(f"  Verified facts block: {len(verified_facts)} chars")

        # Store all facts for later use
        all_team_facts[team_key] = {
            "team_info": team_info,
            "standings": standings,
            "recent": recent,
            "upcoming": upcoming,
            "verified_facts": verified_facts,
        }

    # === PHASE 2: GENERATE AI CONTENT (with verified facts injected) ===
    teams_data = {}

    for team_key, cfg in TEAMS.items():
        print(f"\n--- {cfg['full_name']} [AI Content] ---")
        facts = all_team_facts[team_key]
        team_info = facts["team_info"]
        standings = facts["standings"]
        recent = facts["recent"]
        upcoming = facts["upcoming"]
        verified_facts = facts["verified_facts"]

        # Generate LOTL via Perplexity WITH verified facts
        print(f"  Generating Lay of the Land (with ESPN facts)...")
        lotl_text = generate_lotl(team_key, verified_facts)

        # Find news articles
        print(f"  Finding news articles...")
        articles_data = find_news_articles(team_key)
        articles = articles_data.get("articles", []) if articles_data else []

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
            "last_game_highlights": highlights if highlights.get("available") else existing_team.get("last_game_highlights", {"available": False}),
            "the_latest": articles if articles else existing_team.get("the_latest", []),
            "standings": existing_team.get("standings", {}),
        }

        # Preserve special fields
        if "the_latest_label" in existing_team:
            team_entry["the_latest_label"] = existing_team["the_latest_label"]

        teams_data[team_key] = team_entry

    db["teams"] = teams_data

    # === PHASE 3: HOMEPAGE CONTENT ===

    # Featured story + two-up stories (with verified facts)
    print("\n--- Generating homepage stories (with ESPN facts) ---")
    stories_data = generate_featured_and_stories(all_team_facts)
    if stories_data and stories_data.get("stories"):
        stories = stories_data["stories"]
        if len(stories) >= 1:
            s = stories[0]
            db["featured"] = {
                "team": s.get("team", ""),
                "kicker": s.get("kicker", ""),
                "headline": s.get("headline", ""),
                "dek": s.get("dek", ""),
                "link": s.get("link", "#"),
                "source": s.get("source", ""),
                "date": TODAY_DISPLAY,
            }
        if len(stories) >= 3:
            db["two_up"] = [
                {
                    "team": stories[1].get("team", ""),
                    "kicker": stories[1].get("kicker", ""),
                    "headline": stories[1].get("headline", ""),
                    "dek": stories[1].get("dek", ""),
                    "link": stories[1].get("link", "#"),
                    "source": stories[1].get("source", ""),
                    "date": TODAY_DISPLAY,
                },
                {
                    "team": stories[2].get("team", ""),
                    "kicker": stories[2].get("kicker", ""),
                    "headline": stories[2].get("headline", ""),
                    "dek": stories[2].get("dek", ""),
                    "link": stories[2].get("link", "#"),
                    "source": stories[2].get("source", ""),
                    "date": TODAY_DISPLAY,
                },
            ]
        if len(stories) >= 4:
            db["extra_story"] = {
                "team": stories[3].get("team", ""),
                "kicker": stories[3].get("kicker", ""),
                "headline": stories[3].get("headline", ""),
                "dek": stories[3].get("dek", ""),
                "link": stories[3].get("link", "#"),
                "source": stories[3].get("source", ""),
                "date": TODAY_DISPLAY,
            }
    else:
        # Fallback to existing
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

        # Determine if team is in-season (recent game within 30 days or upcoming games)
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
            status = standing or "Offseason"

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
                # Offseason ‚Äî show status
                db["today_slate"].append({
                    "team": team_key,
                    "logo": cfg["logo"],
                    "matchup": cfg["full_name"].split()[-1],
                    "detail": "Offseason",
                    "channel": "",
                    "off": True,
                })

    # Week Ahead ‚Äî combine all upcoming games for next 7 days
    all_upcoming.sort(key=lambda x: x.get("game_date", ""))
    db["week_ahead"] = {
        "games": [
            {k: v for k, v in g.items() if k != "game_date"}
            for g in all_upcoming
        ],
        "note": "",
    }

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
