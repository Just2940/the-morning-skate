"""Offline regression tests for The Morning Skate pipeline.

Run:  python -m unittest discover -s tests -v
No network: ESPN is replaced by fixture data, the clock is pinned.

Each test exists because the bug it guards against actually shipped:
  - phases decided by the calendar ("Deep Offseason" two days before the
    Leafs' opener, Commanders "Offseason" in Week 3)
  - preseason games inflating streaks ("four straight" for an 0-2 team)
  - UTC dating that showed Friday's win as "last night" over Saturday's loss
  - fan blogs and syndicated fan content in the article pool
  - AI text with invented numbers, garbled headlines
"""
import importlib.util
import io
import contextlib
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


uc = _load("uc", "update-content.py")
vc = _load("vc", "validate_content.py")
from sources import reputable_publisher, reputable_provider, credits_fan_brand  # noqa: E402
from factcheck import ungrounded_numbers  # noqa: E402
from headlines import headline_problem, headline_case, clean_headline, fact_headline  # noqa: E402
from game_details import (stars_from_summary, preview_from_summary, goalies_from_dfo, last_name,  # noqa: E402
                          stars_text, preview_text)

FIXED_NOW = datetime(2026, 9, 27, 7, 0, tzinfo=uc.EST)


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


# --------------------------------------------------------------------- #
# Fixture builders                                                       #
# --------------------------------------------------------------------- #

def event(eid, date, season_type, state, us_score=None, them_score=None,
          home=True, opp="MTL", opp_name="Canadiens", completed=None, status_name=None):
    completed = (state == "post") if completed is None else completed
    name = status_name or {"post": "STATUS_FINAL", "pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS"}[state]
    us = {"id": "21", "homeAway": "home" if home else "away",
          "team": {"abbreviation": "TOR", "shortDisplayName": "Maple Leafs"}}
    them = {"id": "99", "homeAway": "away" if home else "home",
            "team": {"abbreviation": opp, "shortDisplayName": opp_name}}
    if us_score is not None:
        us["score"] = {"value": float(us_score), "displayValue": str(us_score)}
        them["score"] = {"value": float(them_score), "displayValue": str(them_score)}
    return {"id": eid, "date": date, "seasonType": {"type": season_type}, "timeValid": True,
            "competitions": [{"status": {"type": {"state": state, "completed": completed, "name": name}},
                              "competitors": [us, them], "broadcasts": []}]}


class _Patch:
    """Pin the clock and replace ESPN with fixture responses."""

    def __init__(self, responses):
        self.responses = responses

    def __enter__(self):
        self.saved = (uc.NOW, uc.espn_fetch, uc._nhl_canadian_tv)
        uc.NOW = FIXED_NOW
        uc.espn_fetch = lambda url: self.responses.get(url.split("?seasontype=")[-1] if "?seasontype=" in url else "base")
        uc._nhl_canadian_tv = lambda abbr: {}
        uc.SCHEDULE_CONTEXT.clear()
        return self

    def __exit__(self, *exc):
        uc.NOW, uc.espn_fetch, uc._nhl_canadian_tv = self.saved


# --------------------------------------------------------------------- #
# Schedule parser                                                        #
# --------------------------------------------------------------------- #

class ScheduleParserTests(unittest.TestCase):
    def leafs_preseason_fixture(self):
        pre = [event("p1", "2026-09-19T23:00Z", 1, "post", 1, 4, opp_name="Canadiens"),
               event("p2", "2026-09-19T23:05Z", 1, "post", 3, 4, opp_name="Canadiens"),
               event("p3", "2026-09-23T23:00Z", 1, "post", 6, 1, opp_name="Senators")]
        reg = [event("r1", "2026-09-29T23:00Z", 2, "pre"),   # opener in 2 days
               event("r2", "2026-09-30T23:30Z", 2, "pre", opp_name="Islanders"),
               event("r3", "2027-04-15T23:00Z", 2, "pre", opp_name="Bruins")]
        return {"base": {"season": {"type": 1, "year": 2027}, "events": pre},
                "1": {"events": pre}, "2": {"events": reg}, "3": {"events": []}}

    def test_merges_season_types_and_sees_the_opener(self):
        with _Patch(self.leafs_preseason_fixture()):
            recent, upcoming = quiet(uc.get_team_schedule, "leafs")
            ctx = uc.SCHEDULE_CONTEXT["leafs"]
        self.assertEqual(ctx["next_type"], 2)
        self.assertEqual(ctx["days_to_next"], 2)
        self.assertEqual(ctx["played_regular"], 0)
        self.assertEqual(upcoming[0]["note"], "season opener")
        self.assertEqual(len(upcoming), 2, "only the next 7 days are returned")
        self.assertTrue(all("_comp" not in g for g in upcoming), "raw ESPN blobs must not leak")

    def test_split_squad_days_are_labeled(self):
        with _Patch(self.leafs_preseason_fixture()):
            recent, _ = quiet(uc.get_team_schedule, "leafs")
        dates = [g["date"] for g in recent]
        self.assertIn("Sep 19 (G1)", dates)
        self.assertIn("Sep 19 (G2)", dates)

    def test_preseason_leaves_recent_once_regular_season_starts(self):
        fx = {"base": {"season": {"type": 2, "year": 2026}, "events": [
                  event("r1", "2026-09-13T17:00Z", 2, "post", 22, 24, opp="PHI", opp_name="Eagles"),
                  event("r2", "2026-09-20T17:00Z", 2, "post", 20, 37, opp="DAL", opp_name="Cowboys"),
                  event("r3", "2026-09-27T17:00Z", 2, "pre", opp="SEA", opp_name="Seahawks")]},
              "1": {"events": [event("p1", "2026-08-22T23:00Z", 1, "post", 13, 17),
                               event("p2", "2026-08-28T23:00Z", 1, "post", 3, 41)]},
              "3": {"events": []}}
        with _Patch(fx):
            recent, _ = quiet(uc.get_team_schedule, "leafs")
        self.assertEqual([g["season_type"] for g in recent], [2, 2],
                         "exhibition losses must not stretch an 0-2 start into four straight")

    def test_late_games_are_dated_in_eastern_time(self):
        fx = {"base": {"season": {"type": 2, "year": 2026}, "events": [
                  # Friday 10:10 PM ET = Saturday 02:10 UTC; Saturday 4:10 PM ET matinee
                  event("f", "2026-09-26T02:10Z", 2, "post", 2, 0),
                  event("s", "2026-09-26T20:10Z", 2, "post", 0, 11)]},
              "1": {"events": []}, "3": {"events": []}}
        with _Patch(fx):
            recent, _ = quiet(uc.get_team_schedule, "leafs")
        self.assertEqual(recent[0]["game_date"], "2026-09-26")
        self.assertEqual((recent[0]["team_score"], recent[0]["opp_score"]), (0, 11),
                         "Saturday's loss is the latest game, not Friday's win")
        self.assertEqual(recent[1]["game_date"], "2026-09-25")

    def test_postponed_games_are_ignored(self):
        fx = {"base": {"season": {"type": 2, "year": 2026}, "events": [
                  event("x", "2026-09-28T23:00Z", 2, "post", completed=False, status_name="STATUS_POSTPONED")]},
              "1": {"events": []}, "3": {"events": []}}
        with _Patch(fx):
            recent, upcoming = quiet(uc.get_team_schedule, "leafs")
        self.assertEqual((recent, upcoming), ([], []))


# --------------------------------------------------------------------- #
# Phase engine                                                           #
# --------------------------------------------------------------------- #

class PhaseEngineTests(unittest.TestCase):
    def phase(self, team, ctx, standings=None, recent=None, upcoming=None):
        saved_now = uc.NOW
        uc.NOW = FIXED_NOW
        uc.SCHEDULE_CONTEXT.clear()
        uc.SCHEDULE_CONTEXT[team] = dict(ok=True, **ctx)
        try:
            return quiet(uc.detect_season_phase, team, recent or [], upcoming or [], standings or {})["phase"]
        finally:
            uc.NOW = saved_now

    def test_preseason_two_days_before_opener(self):
        self.assertEqual(self.phase("leafs", dict(next_type=2, days_to_next=2, played_regular=0,
                                                  remaining_regular=84, last_type=1, last_days_ago=4)), "preseason")

    def test_training_camp_before_first_exhibition(self):
        self.assertEqual(self.phase("raptors", dict(next_type=1, days_to_next=6, played_regular=0,
                                                    remaining_regular=82, last_type=None, last_days_ago=None)),
                         "training_camp")

    def test_regular_season(self):
        self.assertEqual(self.phase("commanders", dict(next_type=2, days_to_next=0, played_regular=2,
                                                       remaining_regular=15, last_type=2, last_days_ago=7)),
                         "regular_season")

    def test_eliminated_team_playing_out_the_schedule(self):
        self.assertEqual(self.phase("jays", dict(next_type=2, days_to_next=0, played_regular=161,
                                                 remaining_regular=1, last_type=2, last_days_ago=1),
                                    standings={"clincher": "e"}), "playing_out")

    def test_season_just_ended(self):
        self.assertEqual(self.phase("jays", dict(next_type=None, days_to_next=None, played_regular=162,
                                                 remaining_regular=0, last_type=2, last_days_ago=2)),
                         "season_ended")

    def test_clinched_team_waiting_for_the_bracket_is_in_the_playoffs(self):
        self.assertEqual(self.phase("jays", dict(next_type=None, days_to_next=None, played_regular=162,
                                                 remaining_regular=0, last_type=2, last_days_ago=2),
                                    standings={"clincher": "x"}), "playoffs")

    def test_postseason_game_scheduled(self):
        self.assertEqual(self.phase("leafs", dict(next_type=3, days_to_next=2, played_regular=84,
                                                  remaining_regular=0, last_type=2, last_days_ago=3)), "playoffs")


# --------------------------------------------------------------------- #
# Sources                                                                #
# --------------------------------------------------------------------- #

class SourceTests(unittest.TestCase):
    def test_fan_sites_rejected(self):
        for url in ("https://www.bluebirdbanter.com/news/1", "https://raptorsrepublic.com/2026/09/26/x/",
                    "https://www.hogshaven.com/x", "https://riggosrag.com/x", "https://jaysjournal.com/x",
                    "https://theleafsnation.com/x", "https://www.raptorshq.com/x",
                    "https://commanderswire.usatoday.com/x", "https://www.si.com/nfl/commanders/x",
                    "https://evilespn.com/x"):
            self.assertEqual(reputable_publisher(url), "", url)

    def test_news_organizations_accepted(self):
        self.assertEqual(reputable_publisher("https://www.sportsnet.ca/nhl/article/x"), "Sportsnet")
        self.assertEqual(reputable_publisher("https://www.nytimes.com/athletic/1/2026/09/26/x/"), "The Athletic")
        self.assertEqual(reputable_publisher("https://profootballtalk.nbcsports.com/x"), "Pro Football Talk")

    def test_syndicated_fan_content_rejected(self):
        self.assertFalse(reputable_provider("SB Nation"))
        self.assertTrue(reputable_provider("Yahoo Sports"))
        self.assertTrue(credits_fan_brand("5 questions, 5 answers: A Seahawks-Commanders preview with Hogs Haven"))
        self.assertFalse(credits_fan_brand("The reason since Tuesday is clear"))

    def test_feed_links_are_cleaned(self):
        self.assertEqual(uc._clean_feed_link("\n  https://www.sportingnews.com/us/nfl/news/x/210aed>\n "),
                         "https://www.sportingnews.com/us/nfl/news/x/210aed")


# --------------------------------------------------------------------- #
# AI text guards                                                         #
# --------------------------------------------------------------------- #

FACTS = """CURRENT RECORD: 0-2
STANDING: 4th in NFC East
GAMES BEHIND: 2
  Sep 20: L 20-37 vs Cowboys
CURRENT RUN: 2 game loss streak
Headline: Jays Edge Reds 6-5 (MLB.com)"""


class AiTextGuardTests(unittest.TestCase):
    def test_invented_streak_is_caught(self):
        self.assertEqual(ungrounded_numbers("stretched the skid to four straight", FACTS), ["streak of 4"])

    def test_grounded_text_passes(self):
        self.assertEqual(ungrounded_numbers(
            "The 37-20 loss left them 0-2, fourth in the NFC East, two games back after two straight losses.", FACTS), [])

    def test_no_false_alarms_on_common_numbers(self):
        self.assertEqual(ungrounded_numbers("the 2026-27 season, a 2-year deal, a 2-for-4 night, 7:00 PM", FACTS), [])

    def test_invented_score_and_standing_caught(self):
        problems = ungrounded_numbers("a 2-1 series lead, third in the NFC East", FACTS)
        self.assertIn("score/record 2-1", problems)
        self.assertIn("3rd in nfc", problems)

    def test_headline_gate(self):
        self.assertIn("unrecognized word", headline_problem("McKenna Choves Number 92", "mckenna"))
        self.assertEqual(headline_problem("Leafs Open With Montreal on Tuesday", ""), "")


# --------------------------------------------------------------------- #
# Morning Brief headlines                                                #
# --------------------------------------------------------------------- #

class HeadlineTests(unittest.TestCase):
    FACTS = ("[Toronto Blue Jays - MLB - Regular Season]\n- Last night: lost to the Reds 5-1\n"
             "- Today: Jays vs. Reds, 3:07 PM ET - Season finale on Sportsnet\n"
             "- Game note: Probable starters: Scherzer (3-9, 6.14 ERA) vs. Williamson (5-4, 4.89 ERA)\n"
             "[Washington Commanders - NFL - Regular Season]\n- Today: Commanders vs. Seahawks, 1:00 PM ET on FOX\n"
             "- Game note: Out: Cosmi (concussion), Daniels (elbow)")
    NICK = {"leafs": "Leafs", "jays": "Jays", "raptors": "Raptors", "commanders": "Commanders"}

    def test_labels_are_not_headlines(self):
        for t in ("Sunday's Skate", "The Morning Skate Brief", "Toronto Sports Update",
                  "Leafs, Jays, Raptors and Commanders", "Can the Jays Finish Strong?", "A Big Day for Everyone"):
            self.assertTrue(headline_problem(t, self.FACTS), t)

    def test_real_headlines_pass(self):
        for t in ("Scherzer Starts Jays' Season Finale", "Commanders Face Seahawks Without Daniels",
                  "Jays Fall to Reds 5-1", "Leafs Open Season Tuesday Against Canadiens"):
            self.assertEqual(headline_problem(t, self.FACTS), "", t)

    def test_invented_numbers_and_words_rejected(self):
        self.assertIn("unverified number", headline_problem("Jays Fall to Reds 7-2", self.FACTS))
        self.assertIn("unrecognized word", headline_problem("Scherzer Blorps Jays Finale", self.FACTS))

    def test_player_named_in_the_article_is_accepted(self):
        body = "Addison Barger homered twice as the Jays won."
        self.assertEqual(headline_problem("Barger Homers Twice in Jays Win", self.FACTS, body), "")

    def test_cleanup_and_case(self):
        self.assertEqual(clean_headline('1. "Jays fall to Reds 5-1."'), "Jays fall to Reds 5-1")
        self.assertEqual(headline_case("scherzer starts jays' season finale"), "Scherzer Starts Jays' Season Finale")
        self.assertEqual(headline_case("McLaurin out as Commanders host Seahawks"),
                         "McLaurin Out as Commanders Host Seahawks")

    def test_fact_headline_leads_with_the_biggest_story(self):
        slate = [{"team": "leafs", "off": True, "matchup": "Leafs"},
                 {"team": "jays", "off": False, "matchup": "Jays vs. Reds", "detail": "3:07 PM ET - Season finale",
                  "preview": {"duel": {"label": "Probable starters", "ours": {"name": "Scherzer"},
                                       "theirs": {"name": "Williamson"}}}},
                 {"team": "commanders", "off": False, "matchup": "Commanders vs. Seahawks", "detail": "1:00 PM ET"}]
        sb = [{"team": "jays", "result": "L", "team_score": 1, "opp_score": 5, "opp_name": "Reds",
               "game_date": "2026-09-26"}]
        days = {"today": "2026-09-27", "yest": "2026-09-26"}
        h = fact_headline(sb, slate, self.NICK, **days)
        self.assertEqual(h, "Scherzer Starts Jays' Season Finale")
        self.assertEqual(headline_problem(h, self.FACTS), "")
        slate[1]["detail"] = "3:07 PM ET"  # an ordinary game: last night's result leads
        self.assertEqual(fact_headline(sb, slate, self.NICK, **days), "Jays Fall to Reds 5-1")

    def test_fact_headline_on_quiet_days(self):
        opener = {"leafs": {"day": "Tue 9/29", "opp": "vs. Canadiens"}}
        self.assertEqual(fact_headline([], [], self.NICK, openers=opener),
                         "Leafs Open Season Tuesday Against Canadiens")
        self.assertEqual(fact_headline([], [], self.NICK, news={"raptors": "Raptors sign veteran guard"}),
                         "Raptors sign veteran guard")
        self.assertEqual(fact_headline([], [], self.NICK), "")


# --------------------------------------------------------------------- #
# Game details                                                           #
# --------------------------------------------------------------------- #

class SanitizeTests(unittest.TestCase):
    def test_decimals_keep_their_space(self):
        self.assertEqual(uc.sanitize_ascii("26 saves, .963"), "26 saves, .963")
        self.assertEqual(uc.sanitize_ascii("a .500 team"), "a .500 team")

    def test_citation_leftovers_still_tidied(self):
        self.assertEqual(uc.sanitize_ascii("into the playoffs .[1]"), "into the playoffs.")
        self.assertEqual(uc.sanitize_ascii("Leafs , Jays"), "Leafs, Jays")


class ScoreboardWindowTests(unittest.TestCase):
    """The homepage scoreboard shows last night's finals and nothing older
    (Justin, 2026-10-01: Sept 27 finals were still up on Sept 30)."""

    def test_morning_edition(self):
        now = datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc)  # 4:30 AM EDT Oct 1
        self.assertTrue(uc._is_last_night("2026-09-30", now))
        self.assertFalse(uc._is_last_night("2026-09-29", now))
        self.assertFalse(uc._is_last_night("2026-09-27", now))

    def test_late_evening_run_uses_eastern_dates(self):
        now = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)  # 11 PM EDT Oct 1
        self.assertTrue(uc._is_last_night("2026-10-01", now))
        self.assertTrue(uc._is_last_night("2026-09-30", now))
        self.assertFalse(uc._is_last_night("2026-10-02", now))


class GameDetailTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(last_name("V. Guerrero Jr."), "Guerrero Jr.")
        self.assertEqual(last_name("T. Blueger"), "Blueger")

    def test_mlb_stars(self):
        s = {"header": {"competitions": [{"status": {"featuredAthletes": [
                {"name": "winningPitcher", "athlete": {"shortName": "R. Lowder"}},
                {"name": "losingPitcher", "athlete": {"shortName": "T. Yesavage"}}]}}]},
             "boxscore": {"players": [{"team": {"abbreviation": "TOR"}, "statistics": [
                 {"labels": ["H-AB", "AB", "R", "H", "RBI", "HR"], "athletes": [
                     {"athlete": {"shortName": "A. Gimenez"}, "stats": ["1-3", "3", "0", "1", "1", "0"]},
                     {"athlete": {"shortName": "G. Springer"}, "stats": ["2-4", "4", "1", "2", "3", "1"]}]}]}]}}
        stars = stars_from_summary(s, "MLB", "TOR")
        self.assertEqual([(x["tag"], x["tone"], x["name"]) for x in stars],
                         [("W", "win", "Lowder"), ("L", "loss", "Yesavage"), ("BAT", "", "Springer")])
        self.assertEqual(stars[2]["stat"], "2-for-4, HR, 3 RBI")
        self.assertEqual(stars_text(stars), ["W: Lowder", "L: Yesavage", "BAT: Springer (2-for-4, HR, 3 RBI)"])

    def test_nfl_stars(self):
        s = {"leaders": [{"team": {"abbreviation": "WSH"}, "leaders": [
            {"name": "passingYards", "leaders": [{"athlete": {"shortName": "M. Mariota"}, "displayValue": "11/16, 111 YDS, 1 TD"}]},
            {"name": "rushingYards", "leaders": [{"athlete": {"shortName": "J. Daniels"}, "displayValue": "7 CAR, 69 YDS"}]}]}]}
        self.assertEqual(stars_text(stars_from_summary(s, "NFL", "WSH")),
                         ["PASS: Mariota (111 yds, 1 TD)", "RUSH: Daniels (69 yds)"])

    def test_nhl_stars(self):
        s = {"boxscore": {"players": [{"team": {"abbreviation": "TOR"}, "statistics": [
            {"labels": ["G", "A"], "athletes": [{"athlete": {"shortName": "A. Matthews"}, "stats": ["2", "0"]},
                                                {"athlete": {"shortName": "J. Tavares"}, "stats": ["1", "1"]}]},
            {"labels": ["GA", "SA", "SV"], "athletes": [{"athlete": {"shortName": "S. Bobrovsky"}, "stats": ["1", "30", "29"]}]}]}]}}
        self.assertEqual(stars_text(stars_from_summary(s, "NHL", "TOR")),
                         ["GOAL: Matthews (2G)", "GOAL: Tavares (1G, 1A)", "SV: Bobrovsky (29 saves, .967)"])

    def test_older_editions_still_render(self):
        """Editions saved before the redesign stored plain strings."""
        self.assertEqual(stars_text(["W: Lowder"]), ["W: Lowder"])
        self.assertEqual(preview_text(["Out: Daniels (elbow)"]), ["Out: Daniels (elbow)"])
        self.assertEqual(stars_text(None), [])
        self.assertEqual(preview_text(None), [])

    def test_email_renders_structured_details(self):
        send_brief = _load("send_brief", "send_brief.py")
        data = {"meta": {"date_display": "Sunday, September 27, 2026"},
                "scoreboard": [{"team": "jays", "name": "Jays", "league": "MLB", "opp_name": "Reds", "result": "L",
                                "team_score": 1, "opp_score": 5, "date": "Sep 26",
                                "stars": [{"tag": "W", "tone": "win", "name": "Lowder", "stat": "6.0 IP, 0 ER, 2 K"}]}],
                "today_slate": [{"team": "jays", "matchup": "Jays vs. Reds", "detail": "3:07 PM ET",
                                 "preview": {"duel": {"label": "Probable starters", "status": None,
                                                      "ours": {"name": "Scherzer", "sub": "3-9, 6.14 ERA"},
                                                      "theirs": {"name": "Williamson", "sub": ""}}}},
                                {"team": "commanders", "matchup": "Commanders vs. Seahawks", "detail": "1:00 PM ET",
                                 "preview": ["Out: Daniels (elbow)"]}]}
        _, body, text = send_brief.build(data)
        self.assertIn("Lowder", body)
        self.assertIn("Scherzer", body)
        self.assertIn("W: Lowder (6.0 IP, 0 ER, 2 K)", text)
        self.assertIn("Probable starters: Scherzer (3-9, 6.14 ERA) vs. Williamson", text)
        self.assertIn("Out: Daniels (elbow)", text)

    def test_previews(self):
        mlb = {"header": {"competitions": [{"competitors": [
            {"team": {"abbreviation": "TOR"}, "probables": [{"athlete": {"shortName": "M. Scherzer"},
             "statistics": {"splits": {"categories": [{"abbreviation": "W", "displayValue": "3"},
                                                      {"abbreviation": "L", "displayValue": "9"},
                                                      {"abbreviation": "ERA", "displayValue": "6.14"}]}}}]},
            {"team": {"abbreviation": "CIN"}, "probables": [{"athlete": {"shortName": "B. Williamson"}}]}]}]}}
        duel = preview_from_summary(mlb, "MLB", "TOR")["duel"]
        self.assertEqual((duel["ours"]["name"], duel["ours"]["sub"], duel["theirs"]["name"]),
                         ("Scherzer", "3-9, 6.14 ERA", "Williamson"))
        self.assertEqual(preview_text({"duel": duel}), ["Probable starters: Scherzer (3-9, 6.14 ERA) vs. Williamson"])
        nfl = {"header": {"competitions": [{"competitors": [{"team": {"abbreviation": "WSH"}}]}]},
               "injuries": [{"team": {"abbreviation": "WSH"}, "injuries": [
                   {"status": "Out", "athlete": {"shortName": "J. Daniels"}, "details": {"type": "Elbow"}},
                   {"status": "Questionable", "athlete": {"shortName": "T. McLaurin"}, "details": {"type": "Ankle"}}]}]}
        inj = preview_from_summary(nfl, "NFL", "WSH")
        self.assertEqual([(g["status"], g["tone"]) for g in inj["injuries"]],
                         [("Out", "out"), ("Questionable", "questionable")])
        self.assertEqual(preview_text(inj), ["Out: Daniels (elbow)", "Questionable: McLaurin (ankle)"])
        dfo = [{"homeTeamName": "Toronto Maple Leafs", "homeGoalieName": "Sergei Bobrovsky",
                "awayTeamName": "Montreal Canadiens", "awayGoalieName": "Jakub Dobes", "homeNewsStrengthName": "Confirmed"}]
        goalies = goalies_from_dfo(dfo, "Toronto Maple Leafs")
        self.assertEqual(goalies["status"], "confirmed")
        self.assertEqual(preview_text({"duel": goalies}), ["In goal: Bobrovsky vs. Dobes (confirmed)"])


# --------------------------------------------------------------------- #
# Validator rules                                                        #
# --------------------------------------------------------------------- #

class ValidatorTests(unittest.TestCase):
    def run_rule(self, fn, data):
        r = vc.Reporter()
        fn(data, r)
        return r

    def test_data_health_fails_when_espn_is_down(self):
        bad = {"meta": {"data_health": {"espn_ok": 2, "espn_failed": 30, "espn_via_mirror": 0,
                                        "schedule_ok": {"leafs": False, "jays": True}}}}
        self.assertTrue(self.run_rule(vc.check_data_health, bad).errors)
        good = {"meta": {"data_health": {"espn_ok": 37, "espn_failed": 0, "espn_via_mirror": 0,
                                         "schedule_ok": {"leafs": True, "jays": True}}}}
        self.assertFalse(self.run_rule(vc.check_data_health, good).errors)

    def test_reputable_rule_catches_fan_sites(self):
        data = {"featured": {"link": "https://www.hogshaven.com/x", "headline": "x"},
                "teams": {"jays": {"the_latest": [{"link": "https://www.sportsnet.ca/x", "headline": "y"}]}}}
        r = self.run_rule(vc.check_reputable_sources, data)
        self.assertEqual(len(r.errors), 1)

    def test_brief_headline_rule(self):
        def brief(title):
            return {"morning_brief": {"article": {"title": title, "paragraphs": ["x"]}}}
        self.assertTrue(self.run_rule(vc.check_brief_headline, brief("Sunday's Skate")).errors)
        self.assertTrue(self.run_rule(vc.check_brief_headline, brief("")).errors)
        self.assertFalse(self.run_rule(vc.check_brief_headline,
                                       brief("Scherzer Starts Jays' Season Finale")).errors)
        fallback = {"morning_brief": {"title": "Jays Fall to Reds 5-1", "teams": [{"lines": [{"text": "x"}]}]}}
        self.assertFalse(self.run_rule(vc.check_brief_headline, fallback).errors)


if __name__ == "__main__":
    unittest.main()
