"""Pull the EVENT date out of a municipal event publication, or admit there isn't one.

Why this is not "find a date in the text". A body carries 2.2 dates on average (measured over
850 national bodies, 195 municipalities) and most are administrative: when the application was
received, when the decision was sent, when the objection period ends. The Achtkarspelen
template opens `Op 02-03-2026 is de volgende aanvraag binnengekomen` — first-date-wins returns
the RECEIPT date and looks right. Two different things must not share a branch, so a bare date
is never taken: something nearby has to say what it is.

Three tiers, in order, and every hit records which rule fired in `basis` so a wrong date is
traceable to one pattern rather than to "the extractor":

  1. a labelled event field    `Datum evenement:`, `Begin datum:`, `Ingangsdatum van:`
  2. prose with an event cue   `het evenement "X" OP <date>`, `van <d> t/m <d>`
  3. nothing                   -> (None, None), a real answer and a common one

MEASURED, fold A (44 hand-labelled publications, 44 municipalities the patterns were not
written against). v1: precision 85.7%, recall 30.0%. See docs/pg-notes/event-dates.md.

Three things v1 got wrong, each fixed below and each worth keeping in mind:

* **The veto was checked at the wrong offset.** `_vetoed` looked at the run-up to the start of
  the whole match — the event noun — instead of the run-up to the DATE. So
  `evenementenvergunning ontvangen op 7 januari 2026` sailed past a veto list that contains
  `ontvangen`, because nothing administrative precedes `evenementenvergunning`. A guard that
  inspects the wrong span is worse than no guard: it reports a clean run.
* **Dutch writes a range with the month once.** `van 5 t/m 7 juni 2026`, `van 13 t/m 17
  februari 2026`, `op 26 en 27 april 2026` — the FIRST day carries no month or year, so a
  pattern requiring two complete dates misses every one of them and silently returns the
  second day as if it were the start. Partial leading days inherit from the full date.
* **A weekday sits between the preposition and the date.** `op zaterdag 28 februari 2026`.

A rectification deserves its own note. `per abuis is ... de datum 24-05-2026 genoemd maar dit
had 14-05-2026 moeten zijn` states a WRONG date explicitly in order to correct it, so the
document's most prominent date is the one that must not be used. Handled by veto, not by
cleverness: a date introduced by `per abuis`/`genoemd maar` is refused, and the corrected one
is picked up by `moeten zijn`.

Written against the dev half (municipalities whose name hashes even) plus fold A after fold A
was measured once. Fold B — the ~50 test municipalities not in fold A — has not been looked
at, and is what the final number is measured on.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november "
    "december".split())}
_MON = "|".join(MONTHS)
_NUM_D = r"\d{1,2}[-/]\d{1,2}[-/]\d{4}"
_TXT_D = rf"\d{{1,2}}\s+(?:{_MON})\s+\d{{4}}"
DATE = rf"(?:{_NUM_D}|{_TXT_D})"
# A weekday may sit between the preposition and the date, and is not part of either.
_DAY = r"(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\s+"
_OP = rf"\bop\s+(?:{_DAY})?"

# What marks a date as ADMINISTRATIVE. Checked against the run-up to the DATE — not to the
# start of the surrounding match, which is the bug that made v1's veto inert.
VETO = re.compile(
    r"(?:bekendgemaakt|verzonden|verzenddatum|ontvangen|ontvangst|binnengekomen|ingediend"
    r"|besluit\s+is|datum\s+besluit|beslistermijn|bezwaar|bezwaartermijn|zienswijze"
    r"|gepubliceerd|publicatie|publicatiedatum|ter\s+inzage|uiterlijke\s+datum|weken?"
    r"|per\s+abuis|genoemd\s+maar|vervalt|ingetrokken)"
    r"[^.;|]{0,45}$", re.I)

LABELLED_START = [
    ("datum_evenement", rf"datum\s+evenement\s*:?\s*({DATE})"),
    ("datum_activiteit", rf"datum\s+activiteit\s*:?\s*({DATE})"),
    ("begindatum", rf"(?:begin\s*datum|startdatum|start\s+datum|aanvangsdatum)\s*:?\s*({DATE})"),
    ("ingangsdatum", rf"ingangsdatum(?:\s+van)?\s*:?\s*({DATE})"),
    ("datum_van", rf"datum\s+van\s*:?\s*({DATE})"),
    ("moeten_zijn", rf"({DATE})\s+moeten\s+zijn"),
    # A bare `Datum:` is last and still vetoed — it is the field name most likely to mean
    # something administrative in a template this code has never seen.
    ("datum_bare", rf"\bdatum\s*:\s*(?:{_DAY})?({DATE})"),
]
LABELLED_END = [
    ("einddatum", rf"(?:eind\s*datum|einddatum)(?:\s+tot\s+en\s+met)?\s*:?\s*({DATE})"),
    ("datum_tm", rf"datum\s*(?:t/m|tot\s+en\s+met)\s*:?\s*({DATE})"),
]

EVENT_NOUN = (r"(?:evenement|evenementen|festival|kermis|markt|optocht|wedstrijd|toernooi"
              r"|loterij|braderie|feest|concert|voorstelling|activiteit|organiseren|houden"
              r"|viering|herdenking|plaatsvind\w*|gehouden)")
# `van 5 t/m 7 juni 2026` — the leading day is bare and inherits month+year from the full one.
PARTIAL_RANGE = re.compile(
    rf"(?:van\s+)?(\d{{1,2}})\s*(?:t/m|tot\s+en\s+met|tot|en|,)\s*({DATE})", re.I)
PROSE_RANGE = re.compile(
    rf"{EVENT_NOUN}[^.;|]{{0,90}}?(?:in\s+de\s+periode\s+)?van\s+({DATE})\s*"
    rf"(?:t/m|tot\s+en\s+met|tot)\s+({DATE})", re.I)
PROSE_SINGLE = re.compile(rf"{EVENT_NOUN}[^.;|]{{0,90}}?{_OP}({DATE})", re.I)
PROSE_PLAATS = re.compile(
    rf"(?:vind\w*\s+plaats|plaats\s+te\s+vinden)[^.;|]{{0,40}}?{_OP}({DATE})", re.I)


def _parse(s: str) -> date | None:
    s = s.strip().lower()
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(rf"(\d{{1,2}})\s+({_MON})\s+(\d{{4}})", s)
        if not m:
            return None
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _back(text: str, at: int) -> bool:
    """True if the 70 characters leading up to `at` mark this date as administrative."""
    return bool(VETO.search(text[max(0, at - 70):at]))


def _lead_day(text: str, end_date: date, at: int) -> date:
    """`van 5 t/m 7 juni 2026` -> 5 June. Returns `end_date` when there is no leading day."""
    win = text[max(0, at - 24):at]
    m = re.search(r"(?:van|op)\s+(?:" + _DAY + r")?(\d{1,2})\s*(?:t/m|tot en met|tot|en|,)\s*$",
                  win, re.I)
    if m:
        try:
            return end_date.replace(day=int(m.group(1)))
        except ValueError:
            return end_date
    return end_date


def extract(title: str | None, body: str | None) -> dict:
    """-> {start, end, basis}. `basis` is None exactly when no date was taken."""
    text = re.sub(r"\s+", " ", f"{title or ''} | {body or ''}")

    for name, pat in LABELLED_START:
        for m in re.finditer(pat, text, re.I):
            if _back(text, m.start(1)):
                continue
            start = _parse(m.group(1))
            if not start:
                continue
            start = _lead_day(text, start, m.start(1))
            end = None
            for _, epat in LABELLED_END:
                for em in re.finditer(epat, text, re.I):
                    if not _back(text, em.start(1)):
                        end = _parse(em.group(1))
                        break
                if end:
                    break
            return {"start": start, "end": end if end and end >= start else start,
                    "basis": f"label:{name}"}

    for m in PROSE_RANGE.finditer(text):
        if _back(text, m.start(1)):
            continue
        a, b = _parse(m.group(1)), _parse(m.group(2))
        if a and b and b >= a:
            return {"start": a, "end": b, "basis": "prose:range"}

    for pat, name in ((PROSE_PLAATS, "prose:plaats"), (PROSE_SINGLE, "prose:op")):
        for m in pat.finditer(text):
            if _back(text, m.start(1)):
                continue
            d = _parse(m.group(1))
            if d:
                s = _lead_day(text, d, m.start(1))
                return {"start": s, "end": d, "basis": name}

    # Last: a bare partial range anywhere, still requiring an event cue in front of it.
    for m in PARTIAL_RANGE.finditer(text):
        if _back(text, m.start(2)):
            continue
        if not re.search(EVENT_NOUN + r"[^.;|]{0,90}$", text[:m.start()], re.I):
            continue
        b = _parse(m.group(2))
        if b:
            try:
                return {"start": b.replace(day=int(m.group(1))), "end": b,
                        "basis": "partial:range"}
            except ValueError:
                return {"start": b, "end": b, "basis": "partial:range"}

    return {"start": None, "end": None, "basis": None}
