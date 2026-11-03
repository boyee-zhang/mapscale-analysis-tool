"""
Period extraction from a publication's XML body.

`parse_body` is pure, so every case here is a string -- no network, no Postgres. That matters
more than usual: the consumer is a ground truth, and the cases that must not be guessed
(a missing end date, an impossible date, an event running to 23:59) are exactly the ones a
fixture-backed test would hide behind a happy path.

Formats are real, taken from fetched documents, not invented.
"""
from datetime import date, time

import pytest

from scripts.import_bekendmaking_body import parse_body

HEAD = "<officiele-publicatie><metadata><meta name='x' content='y'/></metadata>"


def doc(*als: str) -> str:
    return HEAD + "<gemeenteblad><tekst>" + "".join(
        f"<al>{a}</al>" for a in als) + "</tekst></gemeenteblad></officiele-publicatie>"


# ── the APV family: one Looptijd line carrying both ends ─────────────────────

def test_looptijd_range():
    # Space before the colon and none after it, as published.
    r = parse_body(doc("Stadsdeel: Oost", "Looptijd :22-05-2026 t/m 04-06-2026"))
    assert r["status"] == "ok"
    assert r["start_date"] == date(2026, 5, 22)
    assert r["end_date"] == date(2026, 6, 4)
    assert r["period_raw"] == "Looptijd :22-05-2026 t/m 04-06-2026"


def test_looptijd_single_day_is_a_range_of_one():
    r = parse_body(doc("Looptijd :06-03-2026 t/m 06-03-2026"))
    assert r["start_date"] == r["end_date"] == date(2026, 3, 6)


def test_looptijd_without_an_end_leaves_end_null():
    # The whole point of the table: nothing is inferred. An absent end is NULL, never the
    # start date, because a consumer cannot tell an inferred end from a published one.
    r = parse_body(doc("Looptijd :06-03-2026"))
    assert r["status"] == "ok"
    assert r["start_date"] == date(2026, 3, 6)
    assert r["end_date"] is None


# ── the evenement family: separate date and clock fields ─────────────────────

def test_evenement_colon_time():
    r = parse_body(doc("Datum van: 14-05-2026", "Datum t/m: 14-05-2026",
                       "Tijd van: 09:00", "Tijd tot: 19:00",
                       "Bezoekers drukste moment: 300"))
    assert (r["start_date"], r["end_date"]) == (date(2026, 5, 14), date(2026, 5, 14))
    assert (r["start_time"], r["end_time"]) == (time(9, 0), time(19, 0))
    assert r["visitors_peak"] == 300


def test_evenement_dot_time_is_the_same_clock():
    # 'Tijd van: 14.00' and 'Tijd van: 09:00' both occur in Amsterdam publications.
    r = parse_body(doc("Datum van: 21-10-2026", "Datum t/m: 25-10-2026",
                       "Tijd van: 14.00", "Tijd tot: 23.59",
                       "Bezoekers drukste moment: 1200"))
    assert (r["start_time"], r["end_time"]) == (time(14, 0), time(23, 59))
    assert r["visitors_peak"] == 1200


def test_time_past_midnight_is_stored_as_published():
    # 22:00 -> 02:00 on one date. Not normalised, not rolled to the next day: the fact that
    # it crosses midnight is end_time < start_time AND end_date = start_date, derivable
    # exactly, so storing a derived date would only be able to disagree with the source.
    r = parse_body(doc("Datum van: 01-06-2026", "Datum t/m: 01-06-2026",
                       "Tijd van: 22:00", "Tijd tot: 02:00"))
    assert (r["start_time"], r["end_time"]) == (time(22, 0), time(2, 0))
    assert r["start_date"] == r["end_date"] == date(2026, 6, 1)


def test_missing_end_time_leaves_end_time_null():
    r = parse_body(doc("Datum van: 14-05-2026", "Tijd van: 09:00"))
    assert r["start_time"] == time(9, 0)
    assert r["end_time"] is None
    assert r["status"] == "ok"


# ── values the source gets wrong ─────────────────────────────────────────────

def test_impossible_date_is_null_but_the_row_is_still_ok():
    # 31 April really occurs upstream. The status stays 'ok' because a period field WAS
    # present; demoting it to no_period would file a source defect as a legitimate absence,
    # and period_raw is what makes the defect findable afterwards.
    r = parse_body(doc("Looptijd :31-04-2026 t/m 02-05-2026"))
    assert r["status"] == "ok"
    assert r["start_date"] is None
    assert r["end_date"] == date(2026, 5, 2)
    assert "31-04-2026" in r["period_raw"]


def test_hour_24_is_null_not_clamped():
    r = parse_body(doc("Datum van: 01-06-2026", "Tijd van: 24:00"))
    assert r["start_time"] is None
    assert "24:00" in r["period_raw"]


# ── the states a ground truth must keep apart ────────────────────────────────

def test_body_with_text_but_no_period_is_not_a_failure():
    # The normal case for omgevingsvergunning. Distinct from empty_body: this document was
    # read successfully and says nothing about a period.
    r = parse_body(doc("Omschrijving: slopen van de bestaande uitbouw/balkon",
                       "Zaakadres: Geerdinkhof 318 1103RB Amsterdam"))
    assert r["status"] == "no_period"
    assert r["start_date"] is None
    assert "Omschrijving" in r["body_text"]


def test_body_present_but_textless():
    assert parse_body(HEAD + "<gemeenteblad><tekst><al /><al>  </al></tekst>"
                             "</gemeenteblad>")["status"] == "empty_body"


def test_no_body_element_at_all():
    assert parse_body(HEAD)["status"] == "no_body_element"
    assert parse_body("<officiele-publicatie/>")["status"] == "no_body_element"


# ── body_text ────────────────────────────────────────────────────────────────

def test_body_text_keeps_the_boilerplate():
    # An APV body is 91.3% byte-identical boilerplate, and stripping it is a sampled
    # heuristic. Raw in, derived out -- so the glossary paragraph is expected to survive.
    r = parse_body(doc("Looptijd :01-01-2026 t/m 02-01-2026",
                       "Nachtwerkontheffing: ... een zekere mate van lawaai ... 70Db."))
    assert "lawaai" in r["body_text"]


def test_markup_and_entities_inside_a_line_are_flattened():
    r = parse_body(doc("Stuur een <extref doc='mailto:a@b.c'>a@b.c</extref> &amp; wacht"))
    assert r["body_text"] == "Stuur een a@b.c & wacht"


@pytest.mark.parametrize("line,expected", [
    ("Looptijd :1-1-2026 t/m 2-1-2026", date(2026, 1, 1)),
    ("Looptijd: 01-01-2026 tot en met 02-01-2026", date(2026, 1, 1)),
    ("looptijd :01-01-2026 tot 02-01-2026", date(2026, 1, 1)),
])
def test_looptijd_spelling_variants(line, expected):
    assert parse_body(doc(line))["start_date"] == expected
