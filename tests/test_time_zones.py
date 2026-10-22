"""A time as a clock shows it, made into one moment - across daylight saving.

scheduler/core/zones.py and the repeats that keep a clock
(scheduler/core/recurrence.py), and the notification step reading the
times the model gives (heartbeat/core/heartbeat.py, _when_said). Places
and dates only: no person, nothing of anyone's.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from personalized_agent.heartbeat.core.heartbeat import _when_said
from personalized_agent.scheduler.core import zones
from personalized_agent.scheduler.core.recurrence import (
    next_occurrence, occurrences, parse_recurrence, step)

PARIS, NEW_YORK = "Europe/Paris", "America/New_York"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def wall(text: str) -> datetime:
    return datetime.fromisoformat(text)


class AWallTimeIsOneMomentTests(unittest.TestCase):

    def test_the_offset_is_the_one_of_that_date_not_of_today(self) -> None:
        # Paris is UTC+2 until 25 October 2026 and UTC+1 after.
        self.assertEqual(zones.moment_of(wall("2026-10-22 15:00"), PARIS),
                         (utc("2026-10-22 13:00"), zones.EXACT))
        self.assertEqual(zones.moment_of(wall("2026-10-29 15:00"), PARIS),
                         (utc("2026-10-29 14:00"), zones.EXACT))

    def test_a_time_the_clocks_skip_is_taken_after_the_jump(self) -> None:
        # 29 March 2026: 02:00 becomes 03:00 in Paris; 02:30 is 03:30.
        moment, how = zones.moment_of(wall("2026-03-29 02:30"), PARIS)
        self.assertEqual(how, zones.SKIPPED)
        self.assertEqual(zones.wall_of(moment, PARIS), wall("2026-03-29 03:30"))

    def test_a_time_that_comes_twice_is_taken_the_first_time(self) -> None:
        # 25 October 2026: 03:00 goes back to 02:00; 02:30 happens twice.
        moment, how = zones.moment_of(wall("2026-10-25 02:30"), PARIS)
        self.assertEqual(how, zones.TWICE)
        self.assertEqual(moment, utc("2026-10-25 00:30"))     # still summer time

    def test_a_name_that_is_not_a_zone_is_refused_not_guessed(self) -> None:
        with self.assertRaises(ValueError):
            zones.zone_of("Paris-ish")
        self.assertIsNone(zones.zone_of(zones.LOCAL))         # this computer's, as it is


class TheUsersOwnTimeTests(unittest.TestCase):
    """The user's own time is the zone the computer is in when a time is
    made, kept by name: it does not follow them when they travel - the
    calendar, always in the zone they are in now, shows it converted."""

    def tearDown(self) -> None:
        zones._seen.clear()

    def test_it_is_kept_by_the_name_of_this_computers_zone(self) -> None:
        name = zones.zone_name("user")
        self.assertNotEqual(name, "user")
        # A zone with this computer's own offsets, whatever it is called.
        self.assertEqual(zones._marks_of(name) if name != zones.LOCAL
                         else zones._local_marks(), zones._local_marks())

    def test_the_pages_name_is_believed_only_when_its_clock_is_this_ones(self) -> None:
        here = zones.here()
        other = "Asia/Kathmandu"           # UTC+05:45 all year: no machine's by chance
        if zones._marks_of(other) == zones._local_marks():   # pragma: no cover
            self.skipTest("this computer is in Kathmandu")
        zones.seen(other)
        self.assertEqual(zones.here(), here)

    def test_a_reminder_in_their_own_time_stays_where_it_was_made(self) -> None:
        zones._seen["found_for"] = zones._local_marks()
        zones._seen["found"] = "Europe/Lisbon"     # made while in Lisbon
        when, said, _ = _when_said({"at_local": "2026-11-02 09:00", "zone": "user"})
        self.assertEqual(said["zone"], "Europe/Lisbon")
        self.assertTrue(said["own"])
        self.assertEqual(when, utc("2026-11-02 09:00"))        # 09:00 Lisbon, winter
        # Seen from New York it is 04:00 - the calendar converts; it is not
        # 09:00 New York time.
        self.assertEqual(zones.wall_of(when, NEW_YORK), wall("2026-11-02 04:00"))


class ARepeatKeepsItsClockTests(unittest.TestCase):

    def test_a_weekly_meeting_stays_at_ten_on_its_own_clock(self) -> None:
        rule = parse_recurrence({"every": 1, "unit": "weeks"}, zone=PARIS)
        self.assertEqual(rule["zone"], PARIS)
        first = zones.moment_of(wall("2026-10-19 10:00"), PARIS)[0]
        times = [moment for _, moment in occurrences(rule, first, limit=3)]
        self.assertEqual([zones.wall_of(one, PARIS).hour for one in times], [10, 10, 10])
        # Seen from New York, the week between the two changes is an hour
        # later: Europe changed on the 25th, the United States on 1 November.
        self.assertEqual([zones.wall_of(one, NEW_YORK).hour for one in times], [4, 5, 4])

    def test_the_next_run_after_a_change_is_on_the_clock_too(self) -> None:
        rule = parse_recurrence({"every": 1, "unit": "weeks"}, zone=PARIS)
        first = zones.moment_of(wall("2026-10-19 10:00"), PARIS)[0]
        upcoming, why = next_occurrence(rule, first, 1, now=utc("2026-10-20 00:00"))
        self.assertEqual(why, "scheduled")
        self.assertEqual(upcoming, utc("2026-10-26 09:00"))   # 10:00 Paris, winter

    def test_a_repeat_of_hours_is_elapsed_time(self) -> None:
        # A dose every eight hours is due eight hours later on the night the
        # clocks change too - and keeps no clock.
        rule = parse_recurrence({"every": 8, "unit": "hours"}, zone=PARIS)
        self.assertNotIn("zone", rule)
        self.assertEqual(step(rule, utc("2026-10-24 22:00"), 1), utc("2026-10-25 06:00"))

    def test_an_end_with_no_offset_is_on_the_repeats_clock(self) -> None:
        rule = parse_recurrence({"every": 1, "unit": "days", "until": "2026-12-01 10:00"},
                                zone=PARIS)
        self.assertEqual(rule["until"], utc("2026-12-01 09:00"))

    def test_stepping_back_comes_to_the_same_clock_time(self) -> None:
        rule = parse_recurrence({"every": 1, "unit": "weeks"}, zone=PARIS)
        later = zones.moment_of(wall("2026-11-02 10:00"), PARIS)[0]
        self.assertEqual(zones.wall_of(step(rule, later, -2), PARIS), wall("2026-10-19 10:00"))


class TheNotificationStepsTimesTests(unittest.TestCase):

    def test_a_time_on_another_places_clock_is_kept_with_what_was_written(self) -> None:
        when, said, refused = _when_said({"at_local": "2026-10-29 14:50",
                                          "zone": "Europe/Berlin",
                                          "as_written": "Thursday at 3pm"})
        self.assertEqual(refused, "")
        self.assertEqual(when, utc("2026-10-29 13:50"))
        self.assertEqual(said["zone"], "Europe/Berlin")
        self.assertEqual(said["wall"], "2026-10-29 14:50")
        self.assertEqual(said["as_written"], "Thursday at 3pm")

    def test_a_refused_zone_says_why_and_what_is_accepted(self) -> None:
        when, _, refused = _when_said({"at_local": "2026-10-29 14:50", "zone": "CET-ish"})
        self.assertIsNone(when)
        self.assertTrue(refused.startswith("the zone was refused: "), refused)
        self.assertIn(zones.PLACE_RULE, refused)

    def test_an_offset_given_is_still_read_as_it_was(self) -> None:
        when, said, _ = _when_said({"at": "2026-10-29T14:50:00+01:00"})
        self.assertEqual(when, utc("2026-10-29 13:50"))
        self.assertEqual(said["zone"], zones.here())          # the user's own

    def test_no_time_is_refused(self) -> None:
        self.assertEqual(_when_said({"title": "x"})[2],
                         "a time is needed: at_local, YYYY-MM-DD HH:MM")


class AZoneTheModelWritesIsAPlaceTests(unittest.TestCase):
    """zones.place_zone: Region/City, "UTC" or "user" - nothing else."""

    def test_a_place_utc_and_the_users_own_are_accepted(self) -> None:
        self.assertEqual(zones.place_zone("Asia/Shanghai"), "Asia/Shanghai")
        self.assertEqual(zones.place_zone("America/New_York"), "America/New_York")
        self.assertEqual(zones.place_zone("utc"), "UTC")
        self.assertEqual(zones.place_zone("user"), zones.here())

    def test_old_short_names_are_refused_though_the_database_knows_them(self) -> None:
        # EST is UTC-05:00 all year: "3pm EST" in July would be an hour off.
        self.assertEqual(zones.zone_name("EST"), "EST")
        for name in ("EST", "CET", "PRC"):
            with self.assertRaisesRegex(ValueError, "old short name"):
                zones.place_zone(name)

    def test_etc_names_abbreviations_offsets_and_cities_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "reads backwards"):
            zones.place_zone("Etc/GMT-8")
        for name in ("PST", "CST", "UTC+8", "Beijing", "local", "Asia/Beijing"):
            with self.assertRaises(ValueError, msg=name):
                zones.place_zone(name)


class _Answers:
    """A model that answers the zone asked again from a list, and keeps
    what it was asked."""

    def __init__(self, *texts: object) -> None:
        self.texts, self.asked = list(texts), []

    def invoke(self, request, role=None):
        self.asked.append(request)
        text = self.texts.pop(0)
        if isinstance(text, Exception):
            raise text
        return type("Response", (), {"text": text})()


class AZoneRefusedIsAskedOnceMoreTests(unittest.TestCase):
    """AgentHeartbeat._zone_read: sent back once, told why and what is
    accepted; still refused, the user's own time - never dropped."""

    ENTRY = {"title": "Call with the Denver office", "message": "Call at 3pm",
             "at_local": "2026-10-29 15:00", "as_written": "Thu 3pm EST"}

    def _read(self, zone, *answers):
        """The moment and note, what the model was asked, the step's row
        and the report."""
        from personalized_agent.heartbeat.core.heartbeat import AgentHeartbeat

        heart = type("Heart", (), {"models": _Answers(*answers),
                                   "ZONE_CONTEXT": AgentHeartbeat.ZONE_CONTEXT,
                                   "_charge": staticmethod(lambda row, response: None)})()
        step_row = {"id": "step_notify", "context": "From: a colleague in New York"}
        report: dict = {}
        entry = {**self.ENTRY, "zone": zone}
        when, said, refused = _when_said(
            entry, AgentHeartbeat._zone_again(heart, entry, step_row, report))
        self.assertEqual(refused, "")
        self.assertIsNotNone(when)                            # never dropped
        return (when, said), heart.models.asked, step_row, report

    def test_a_place_is_not_asked_again(self) -> None:
        (_, said), asked, _, report = self._read("America/New_York")
        self.assertEqual(said["zone"], "America/New_York")
        self.assertEqual(asked, [])
        self.assertNotIn("zone_checks", report)

    def test_a_refused_zone_is_named_again_told_why_and_what_is_accepted(self) -> None:
        (when, said), asked, step_row, report = self._read(
            "EST", '{"zone": "America/New_York", "why": "the sender is in New York"}')
        prompt = asked[0].messages[0]["content"]
        self.assertIn('THE ZONE YOU GAVE: "EST"', prompt)
        self.assertIn('WHY IT WAS REFUSED: the zone was refused: "EST" is an old '
                      "short name", prompt)
        self.assertIn("What is accepted: " + zones.PLACE_RULE, prompt)
        self.assertIn("From: a colleague in New York", prompt)    # what it came from
        self.assertIn(zones.PLACE_RULE, asked[0].system)
        self.assertEqual(report["zone_checks"][0]["taken"], "America/New_York")
        self.assertEqual(step_row["zone_checks"], report["zone_checks"])
        # And the moment is New York's, daylight saving and all (EDT then).
        self.assertEqual(said["zone"], "America/New_York")
        self.assertEqual(when, utc("2026-10-29 19:00"))
        self.assertEqual(said["zone_written"], "EST")
        self.assertNotIn("zone_unread", said)

    def test_a_second_answer_held_to_the_same_rule_is_the_users_own(self) -> None:
        (_, said), asked, _, report = self._read("PST", '{"zone": "PST8PDT", "why": "x"}')
        self.assertEqual(len(asked), 1)                       # once, not again
        self.assertIn("old short name", report["zone_checks"][0]["error"])
        self.assertEqual(report["zone_checks"][0]["taken"], "user")
        self.assertEqual(said["zone_unread"], "PST")
        self.assertTrue(said["own"])
        self.assertEqual(said["zone"], zones.here())

    def test_no_place_named_or_no_answer_is_the_users_own(self) -> None:
        for answer in ('{"zone": "", "why": "nothing says"}', '{"zone": "user"}',
                       "not JSON", RuntimeError("the model is away")):
            (_, said), _, _, _ = self._read("Paris time", answer)
            self.assertEqual(said["zone"], zones.here(), answer)
            self.assertEqual(said["zone_unread"], "Paris time", answer)


if __name__ == "__main__":
    unittest.main()
