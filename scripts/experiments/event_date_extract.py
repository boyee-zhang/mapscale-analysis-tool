"""Pull the EVENT date out of a municipal event publication, or admit there isn't one.

294 municipalities publish to no common template. A body carries 2.2 dates on average
(measured, 850 bodies / 195 municipalities) and most are administrative: when the application
arrived, when the decision went out, when the objection window shuts. Achtkarspelen opens
`Op 02-03-2026 is de volgende aanvraag binnengekomen` — first-date-wins returns the RECEIPT
date and looks entirely plausible. So a bare date is never taken.

THREE LAYERS, deliberately, instead of one regex accumulating exceptions:

    1. detect      every date-like token, with the text either side of it
    2. classify    what each one IS — event / admin / correction / unknown — with the cue
                   that decided it recorded on the candidate
    3. decide      which classified candidate to return, or NULL

The split is what makes this maintainable. A new municipal phrasing is a cue added to one
table in layer 2; it cannot silently change how candidates are found or how ties are broken.
It also makes the failure legible: `basis` names the cue, so a wrong date is traceable to one
string rather than to "the extractor".

Measurement history, each on municipalities the patterns had not been written against
(folds are cut by GEMEENTE — one template serves a whole municipality, so a document-level
split would measure memorisation):

    v1  fold A   precision 85.7%  recall 30.0%
    v2  fold A   precision 96.7%  recall 69.0%
    v2  fold B   precision 89.5%  recall 58.6%   <- the gap IS the fold-A overfitting
    v3  fold C   see docs/pg-notes/event-dates.md

Folds A and B are development data from v3 onward: both have been read. Fold C is drawn from
municipalities used in neither.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november "
    "december".split())}
_MON = "|".join(MONTHS)
_WEEKDAY = r"(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)"

# A full date, and the bare leading day of a range (`van 5 t/m 7 juni 2026`). Dutch writes the
# month once, so the first day of a range carries neither month nor year; a detector that
# demands a complete date misses every range AND silently reports the second day as the start.
_NUM = r"\d{1,2}[-/]\d{1,2}[-/]\d{4}"
_TXT = rf"\d{{1,2}}\s+(?:{_MON})\s+\d{{4}}"
FULL_DATE = re.compile(rf"\b({_NUM}|{_TXT})\b", re.I)
# A RUN of dates sharing one month/year tail. Dutch lists them with the month written once
# and only on the last: `14 mei, 11 juli en 12 september 2026`, `27, 29 en 30 april en 1 mei
# 2026`, `13 mei t/m 17 mei 2026`. Detecting only the complete trailing date makes the LAST
# day of the run the candidate — which is the end, never the start, and is wrong in exactly
# the cases a reader would notice. So the run is read backwards from the full date and the
# EARLIEST member becomes the candidate.
RUN_MEMBER = rf"\d{{1,2}}(?:\s+(?:{_MON}))?"
LEAD_RUN = re.compile(
    rf"((?:{RUN_MEMBER}\s*(?:,|en|t/m|tot en met|tot)\s*)+)$", re.I)

BEFORE, AFTER = 85, 45


@dataclass
class Cand:
    """One date found in the text, with everything layer 2 needs to judge it."""
    value: date
    start: int
    before: str
    after: str
    kind: str = "unknown"      # event_label | event_prose | correction | admin | unknown
    cue: str = ""
    lead_day: int | None = None   # `van 5 t/m 7 juni` -> 5, set by layer 1
    lead_month: int | None = None
    rank: int = field(default=99)


# ---------------------------------------------------------------------------------------
# Layer 2 vocabulary. Order within a table does not matter; the RANK does, and lives in
# `_RANK` so the precedence between classes is stated once instead of implied by code order.
# ---------------------------------------------------------------------------------------

# Administrative. A hit here is a veto and beats every event cue, because the cheapest way to
# be wrong is to find an event word somewhere in a long sentence whose date is a deadline.
# `verleend|toegekend|vergund|verlening` were added after fold B: `verleend op 4 maart 2026`
# was read as an event date, which is the error that actually costs a reader something.
ADMIN = (
    r"bekendgemaakt|bekendmaking|verzonden|verzenddatum|verzending|ontvangen|ontvangst"
    r"|binnengekomen|ingekomen|ingediend|besluit\s+is|datum\s+besluit|besloten|beslistermijn"
    r"|verleend|toegekend|vergund|datum\s+verlening|geweigerd|gepubliceerd|publicatie"
    r"|bezwaar|zienswijze|ter\s+inzage|uiterlijke\s+datum|termijn|weken?|dagen"
)
RE_ADMIN = re.compile(rf"({ADMIN})[^.;|]{{0,45}}$", re.I)
# Dutch puts the participle at the end of the clause, so the cue frequently comes AFTER the
# date it governs: `heeft op maandag 15 december 2025 een verzoek ONTVANGEN`. Looking only
# backwards reads that as an event date with a `heeft op` lead-in — measured on Rotterdam,
# where it returned the day the request came in, eight months before the event.
RE_ADMIN_AFTER = re.compile(rf"^[^.;|]{{0,40}}?\b({ADMIN})\b", re.I)

# A rectification states the WRONG date prominently in order to correct it:
#   `per abuis is ... de datum 24-05-2026 genoemd maar dit had 14-05-2026 moeten zijn`
# so the document's most salient date is the one that must not be used, and the real one is
# flagged by what FOLLOWS it. Handled as its own class rather than as a veto, because the
# corrected date outranks every other cue in the document.
RE_CORR_WRONG = re.compile(r"(per\s+abuis|rectificatie|onjuist|vervalt|komt\s+te\s+vervallen)"
                           r"[^.;|]{0,60}$", re.I)
RE_CORR_RIGHT = re.compile(r"^\s*(moet(?:en)?\s+zijn|moet\s+worden|gewijzigd\s+in|wordt)", re.I)

# Labelled event fields. The field name is the evidence, so these outrank prose.
EVENT_LABELS = [
    ("datum evenement", r"datum(?:\s*/\s*data)?\s+evenement\w*\s*:?\s*$"),
    ("datum activiteit", r"datum\s+activiteit(?:en)?\s*:?\s*$"),
    ("begindatum", r"(?:begin\s*datum|startdatum|start\s+datum|aanvangsdatum)\s*:?\s*$"),
    ("ingangsdatum", r"ingangsdatum(?:\s+van)?\s*:?\s*$"),
    ("datum van", r"datum\s+van\s*:?\s*$"),
    ("wanneer", r"wanneer\s*:?\s*(?:van\s+)?$"),
    ("datum en tijdstip", r"datum\s+en\s+tijdstip\s*:?\s*$"),
    ("vindt plaats op", r"(?:vind\w*\s+plaats|plaats\s+te\s+vinden)[^.;|]{0,30}?(?:op)?\s*:?\s*$"),
    # A bare `Datum:` is last and weakest — in an unseen template it is as likely to head an
    # administrative field as an event one, so it only survives when ADMIN did not fire.
    ("datum", r"\bdatum\s*:\s*$"),
]

EVENT_NOUN = (r"evenement\w*|festival|kermis|markt\w*|optocht|wedstrijd|toernooi|loterij"
              r"|braderie|feest\w*|concert|voorstelling|wandeling|tocht|rit|run|loop|cross"
              r"|dag|week|kamp|circus|show|beurs|fair|party|rave|vuur|kermismatinee"
              r"|organiseren|houden|gehouden|viering|herdenking|opening|jubileum")

# Prose. The connector between the event noun and the date is NOT just a preposition: these
# templates write `Kermis Den Burg, 8 t/m 12 juli 2026`, `"Zomermarkt, 27-06-2026"`,
# `Pinksterfietstocht (25 mei 2026)`. Requiring `op|van` cost 14 of the 19 fold-A misses and
# bought nothing, because what actually keeps this honest is the ADMIN veto in front of it,
# not the narrowness of the connector. So any ordinary separator is allowed and the clause
# boundary `[^.;|]` plus the 90-character reach do the containing.
_CONNECT = r"(?:\b(?:op|van|vanaf|t/m|tot en met|tot|en)\b|[,:(\u2013-])"
RE_EVENT_PROSE = re.compile(
    rf"(?:{EVENT_NOUN})[^.;|]{{0,90}}?{_CONNECT}\s*(?:{_WEEKDAY}\s+)?$", re.I)

_RANK = {"correction": 0, "event_label": 1, "event_prose": 2, "unknown": 8, "admin": 9}


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


def detect(text: str) -> list[Cand]:
    """Layer 1 — every date, with its surroundings. No judgement here at all."""
    out: list[Cand] = []
    for m in FULL_DATE.finditer(text):
        v = _parse(m.group(1))
        if not v:
            continue
        c = Cand(value=v, start=m.start(),
                 before=text[max(0, m.start() - BEFORE):m.start()],
                 after=text[m.end():m.end() + AFTER])
        lead = LEAD_RUN.search(c.before)
        if lead:
            # Each member is a day, optionally with its own month; the year always comes from
            # the full date that closes the run. The earliest resulting date is the start.
            best = v
            for dm in re.finditer(rf"(\d{{1,2}})(?:\s+({_MON}))?", lead.group(1), re.I):
                day = int(dm.group(1))
                mon = MONTHS[dm.group(2).lower()] if dm.group(2) else v.month
                try:
                    cand = date(v.year, mon, day)
                except ValueError:
                    continue
                if cand < best:
                    best = cand
            if best < v:
                c.lead_day = best.day
                c.lead_month = best.month
        out.append(c)
    return out


def classify(c: Cand) -> Cand:
    """Layer 2 — what IS this date? Records the cue that decided it."""
    m = RE_CORR_RIGHT.match(c.after)
    if m:
        c.kind, c.cue = "correction", f"after:{m.group(1).strip()}"
        c.rank = _RANK["correction"]
        return c
    m = RE_CORR_WRONG.search(c.before)
    if m:
        c.kind, c.cue = "admin", f"rectified-away:{m.group(1)}"
        c.rank = _RANK["admin"]
        return c
    m = RE_ADMIN.search(c.before)
    if m:
        c.kind, c.cue = "admin", f"admin:{m.group(1).strip()}"
        c.rank = _RANK["admin"]
        return c
    m = RE_ADMIN_AFTER.search(c.after)
    if m:
        c.kind, c.cue = "admin", f"admin-after:{m.group(1).strip()}"
        c.rank = _RANK["admin"]
        return c
    for name, pat in EVENT_LABELS:
        if re.search(pat, c.before, re.I):
            c.kind, c.cue = "event_label", f"label:{name}"
            c.rank = _RANK["event_label"]
            return c
    if RE_EVENT_PROSE.search(c.before):
        c.kind, c.cue = "event_prose", "prose:event-noun+prep"
        c.rank = _RANK["event_prose"]
        return c
    c.kind, c.cue, c.rank = "unknown", "", _RANK["unknown"]
    return c


def decide(cands: list[Cand]) -> dict:
    """Layer 3 — return a date only when something said it was an event date, else NULL.

    `unknown` is never returned. That is the whole NULL policy: a date nobody labelled is a
    date we cannot defend, and a wrong date is worse for a reader than a missing one. Ties
    break on position — the earliest, because templates lead with the subject and close with
    boilerplate.
    """
    usable = [c for c in cands if c.rank <= _RANK["event_prose"]]
    if not usable:
        return {"start": None, "end": None, "basis": None, "confidence": None}
    best = min(usable, key=lambda c: (c.rank, c.start))
    start = best.value
    if best.lead_day:
        try:
            start = best.value.replace(month=best.lead_month or best.value.month,
                                       day=best.lead_day)
        except ValueError:
            start = best.value
    # The end is the next usable date after the start that looks like a range partner.
    end = start
    for c in usable:
        if c.start > best.start and c.value >= start and (c.value - start).days <= 60:
            if re.search(r"(t/m|tot\s+en\s+met|tot|en)\s*$", c.before, re.I):
                end = c.value
            break
    return {"start": start, "end": max(end, start), "basis": best.cue,
            "confidence": {"correction": "high", "event_label": "high",
                           "event_prose": "medium"}[best.kind]}


def extract(title: str | None, body: str | None) -> dict:
    text = re.sub(r"\s+", " ", f"{title or ''} | {body or ''}")
    return decide([classify(c) for c in detect(text)])
