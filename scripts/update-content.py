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
        "espn_team_id": "17",  # Toronto Maple Leafs
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
                    from datetime import datetime as dt
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
    """Get standings from ESPN."""
    cfg = TEAMS[team_key]
    url = f"https://site.api.espn.com/apis/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/standings"
    data = espn_fetch(url)
    if not data:
        return None
    return data  # Return raw ‚Äî we'll process per-team in the main function


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


def generate_lotl(team_key):
    """Generate a Lay of the Land paragraph using Perplexity."""
    cfg = TEAMS[team_key]
    today_str = NOW.strftime("%B %d, %Y")

    system_prompt = """You are a knowledgeable, conversational sports writer for "The Morning Skate," a personal sports briefing app. Your audience is a 65-year-old father who reads this on his phone every morning.

Write ONE paragraph, 120-200 words. Be conversational but informed ‚Äî like a smart sports-radio host talking to a friend. Use contractions and dashes for rhythm. Don't hedge ‚Äî if the team is bad, say so. If there's hope, be specific about why.

FORMATTING RULES:
- Use <strong> tags for the key recent event and for what's next (forward-looking)
- Use HTML entities: &mdash; for em dashes, &ndash; for en dashes
- Do NOT open with "The [Team] are..." ‚Äî vary the opening
- Don't list stats without context
- End with a forward-looking note (next game, upcoming event)
- Do NOT exceed 200 words"""

    prompt = f"""Write today's "Lay of the Land" paragraph for the {cfg['full_name']} as of {today_str}.

Search for the most recent {cfg['full_name']} news, game results, standings, injuries, and upcoming schedule. Include:
1. The headline narrative ‚Äî where does this team stand right now?
2. The most important thing that happened in the last 1-3 days (bold with <strong> tags)
3. Current record, streak, standings position
4. Key player thread ‚Äî who's playing well, who's injured
5. What's next ‚Äî the next game or upcoming event (bold with <strong> tags)

Write in the voice described in the system prompt. One paragraph, 120-200 words."""

    result = perplexity_search(prompt, system_prompt)
    if result:
        # Clean up any markdown that Perplexity might add
        result = result.strip()
        result = re.sub(r'^\*\*.*?\*\*\s*\n*', '', result)  # Remove bold headers
        result = result.replace("**", "<strong>").replace("**", "</strong>")  # Convert markdown bold
        # Fix any unclosed strong tags
        open_count = result.count("<strong>")
        close_count = result.count("</strong>")
        if open_count > close_count:
            result += "</strong>" * (open_count - close_count)
    return result


def generate_featured_and_stories():
    """Use Perplexity to identify the top 3-4 stories across all four teams."""
    today_str = NOW.strftime("%B %d, %Y")

    prompt = f"""As of {today_str}, identify the TOP 3-4 most important sports stories across these four teams:
1. Toronto Maple Leafs (NHL)
2. Toronto Blue Jays (MLB)
3. Toronto Raptors (NBA)
4. Washington Commanders (NFL)

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

The FIRST story should be the biggest ‚Äî playoff games, major trades, dramatic results. Use HTML entities (&mdash; &ndash; &middot;) not unicode. Include real, verified URLs."""

    result = perplexity_search(prompt)
    if not result:
        return None

    # Try to extract JSON from the response
    try:
        # Find JSON in the response
        json_match = re.search(r'\{[\s\S]*"stories"[\s\S]*\}', result)
        if json_match:
            return json.loads(json_match.group())
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

    prompt = f"""Find the 3 most recent and important news articles about the {cfg['full_name']} as of {today_str}.

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
All URLs must be real and currently accessible."""

    result = perplexity_search(prompt)
    if not result:
        return None

    try:
        json_match = re.search(r'\{[\s\S]*"articles"[\s\S]*\}', result)
        if json_match:
            return json.loads(json_match.group())
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

    # === TEAM DATA ===
    all_upcoming = []
    teams_data = {}

    for team_key, cfg in TEAMS.items():
        print(f"\n--- {cfg['full_name']} ---")

        # Get schedule/results from ESPN
        print(f"  Fetching schedule...")
        recent, upcoming = get_team_schedule(team_key)
        all_upcoming.extend(upcoming)

        # Get record from most recent data
        # (We'll derive this from ESPN standings or schedule data)

        # Generate LOTL via Perplexity
        print(f"  Generating Lay of the Land...")
        lotl_text = generate_lotl(team_key)

        # Find news articles
        print(f"  Finding news articles...")
        articles_data = find_news_articles(team_key)
        articles = articles_data.get("articles", []) if articles_data else []

        # Find highlight video (if team played recently)
        highlights = {"available": False}
        if recent:
            last_game = recent[0]
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

        # Use existing data as fallback for fields we couldn't fetch
        existing_team = existing.get("teams", {}).get(team_key, {})

        team_entry = {
            "full_name": cfg["full_name"],
            "league": cfg["league"],
            "record": existing_team.get("record", ""),
            "detail": existing_team.get("detail", ""),
            "live_strip": existing_team.get("live_strip"),
            "lotl": {
                "label": f"What's Going On With the {cfg['full_name'].split()[-1]}",
                "body": lotl_text or existing_team.get("lotl", {}).get("body", ""),
                "updated": f"Updated {TODAY_DISPLAY}",
            },
            "key_numbers": existing_team.get("key_numbers", []),
            "recent_results": recent if recent else existing_team.get("recent_results", []),
            "last_game_highlights": highlights if highlights.get("available") else existing_team.get("last_game_highlights", {"available": False}),
            "the_latest": articles if articles else existing_team.get("the_latest", []),
            "standings": existing_team.get("standings", {}),
        }

        # Preserve special fields
        if "the_latest_label" in existing_team:
            team_entry["the_latest_label"] = existing_team["the_latest_label"]

        teams_data[team_key] = team_entry

    db["teams"] = teams_data

    # === HOMEPAGE CONTENT ===

    # Featured story + two-up stories
    print("\n--- Generating homepage stories ---")
    stories_data = generate_featured_and_stories()
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

    # Ticker (generated from today's key stories)
    db["ticker"] = existing.get("ticker", [])  # Keep existing ticker as baseline

    # At a Glance ‚Äî build from team data
    db["at_a_glance"] = []
    for team_key in ["leafs", "jays", "raptors", "commanders"]:
        existing_glance = {}
        for eg in existing.get("at_a_glance", []):
            if eg.get("team") == team_key:
                existing_glance = eg
                break
        td = teams_data.get(team_key, {})
        db["at_a_glance"].append({
            "team": team_key,
            "name": TEAMS[team_key]["full_name"].split()[-1],
            "logo": TEAMS[team_key]["logo"],
            "record": td.get("record", existing_glance.get("record", "")),
            "status": existing_glance.get("status", ""),
            "status_class": existing_glance.get("status_class", "muted"),
            "stat": existing_glance.get("stat", ""),
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
                # Use existing
                for es in existing.get("today_slate", []):
                    if es.get("team") == team_key:
                        db["today_slate"].append(es)
                        break

    # Week Ahead ‚Äî combine all upcoming games for next 7 days
    all_upcoming.sort(key=lambda x: x.get("game_date", ""))
    db["week_ahead"] = {
        "games": [
            {k: v for k, v in g.items() if k != "game_date"}
            for g in all_upcoming
        ],
        "note": existing.get("week_ahead", {}).get("note", ""),
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
