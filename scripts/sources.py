"""Reputable-publisher allowlist for The Morning Skate.

Justin (2026-09-27): only articles from reputable news organizations -
never fan sites (Bluebird Banter, Raptors Republic, Hogs Haven, and the
networks behind them: SB Nation, FanSided, Nation Network, FanNation...).

An ALLOWLIST, not a blocklist: fan blogs come in whole networks and new
ones appear constantly, so anything not listed here is rejected.

Shared by update-content.py (filters every article before it can be used)
and validate_content.py (rule 0.30 fails the build if one slips through),
so the two can never disagree.
"""
from urllib.parse import urlparse

# host suffix -> display name. A host matches when it IS the domain or a
# subdomain of it, unless the domain is listed in EXACT_HOSTS below.
REPUTABLE_DOMAINS = {
    # Canadian news organizations
    "sportsnet.ca": "Sportsnet",
    "tsn.ca": "TSN",
    "cbc.ca": "CBC Sports",
    "thestar.com": "Toronto Star",
    "theglobeandmail.com": "The Globe and Mail",
    "torontosun.com": "Toronto Sun",
    "nationalpost.com": "National Post",
    "ctvnews.ca": "CTV News",
    "globalnews.ca": "Global News",
    "cp24.com": "CP24",
    "thecanadianpressnews.ca": "The Canadian Press",
    "thescore.com": "theScore",
    # US / international news organizations
    "espn.com": "ESPN",
    "espn.go.com": "ESPN",
    "espn.ca": "ESPN",
    "theathletic.com": "The Athletic",
    "nytimes.com": "The New York Times",   # /athletic/ paths -> The Athletic
    "washingtonpost.com": "Washington Post",
    "apnews.com": "Associated Press",
    "reuters.com": "Reuters",
    "cbssports.com": "CBS Sports",
    "nbcsports.com": "NBC Sports",
    "foxsports.com": "FOX Sports",
    "sports.yahoo.com": "Yahoo Sports",
    "usatoday.com": "USA Today",
    "sportingnews.com": "The Sporting News",
    "theringer.com": "The Ringer",
    "dailyfaceoff.com": "Daily Faceoff",
    # Official league / team sites (authoritative for moves and injuries)
    "nhl.com": "NHL.com",
    "mlb.com": "MLB.com",
    "nba.com": "NBA.com",
    "nfl.com": "NFL.com",
    "commanders.com": "Commanders.com",
}

# Domains whose SUBDOMAINS are not the news organization itself - e.g.
# commanderswire.usatoday.com is a team "Wire" site, not USA Today news.
EXACT_HOSTS = {"usatoday.com"}


def _host(url):
    try:
        host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def reputable_publisher(url):
    """Display name of the news organization behind `url`, or "" if the URL
    is not from an allowlisted publisher (fan sites, aggregators, unknowns)."""
    host = _host(url)
    if not host:
        return ""
    for domain, name in REPUTABLE_DOMAINS.items():
        if host == domain or (domain not in EXACT_HOSTS and host.endswith("." + domain)):
            if domain == "nytimes.com":
                path = (urlparse(url).path or "").lower()
                return "The Athletic" if path.startswith("/athletic") else name
            if domain == "nbcsports.com" and host.startswith("profootballtalk."):
                return "Pro Football Talk"
            return name
    return ""


def is_reputable(url):
    return bool(reputable_publisher(url))


# Syndication: aggregators re-host other outlets' stories on their own
# domain. Half of Yahoo's NFL feed (verified 2026-09-27) is SB Nation fan-
# blog content at sports.yahoo.com URLs, so when a feed item names its
# original publisher (<source> / dc:publisher), that publisher must pass too.
REPUTABLE_PROVIDERS = {
    "yahoo sports", "profootball talk on nbc sports", "pro football talk",
    "nbc sports", "usa today sports", "usa today", "the associated press",
    "associated press", "ap", "reuters", "cbs sports", "fox sports", "espn",
    "the athletic", "sportsnet", "tsn", "cbc sports", "the canadian press",
    "canadian press", "toronto star", "the globe and mail", "toronto sun",
    "postmedia news", "national post", "washington post", "the washington post",
    "mlb.com", "nhl.com", "nba.com", "nfl.com", "new york post", "ny post sports",
}


def reputable_provider(name):
    """True if a syndicated item's named publisher is a news organization.
    An empty provider (the feed's own reporting) passes."""
    n = " ".join((name or "").lower().split())
    return (not n) or n in REPUTABLE_PROVIDERS


# Defense in depth: fan-blog brands that surface inside otherwise-reputable
# feeds ("A Seahawks-Commanders preview with Hogs Haven"). Any headline or
# summary crediting one of these is rejected.
FAN_BRANDS = (
    "sb nation", "fansided", "fannation", "hogs haven", "riggo's rag",
    "bluebird banter", "jays journal", "blue jays nation", "raptors republic",
    "raptors hq", "leafs nation", "the leafs nation", "pension plan puppets",
    "maple leafs hot stove", "leafs hot stove", "field gulls", "blogging the boys",
    "bleeding green nation", "big blue view",
)


def credits_fan_brand(text):
    low = " ".join((text or "").lower().split())
    return any(b in low for b in FAN_BRANDS)
