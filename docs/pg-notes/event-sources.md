# Is there a source for Dutch events? — probed 2026-11-04

Asked before building semantic search: phase 1 wants Netherlands-wide event documents (markets,
concerts, theatre), phase 2 construction, phase 3 urban activity generally.

**Answer: yes, and it is the source already in use. No new API is needed for public-space
events, and no source exists — in open data — for venue programming.**

## 1. The headline, and a correction to a documented project constraint

`CLAUDE.md` and the memory index both say `etl/ingest/bekendmaking.py` is scoped per municipality
"because those are the only two ways KOOP's SRU service narrows by place — there is no national
mode". The second half is wrong, and it was load-bearing: it made national coverage look like
342 separate imports.

The constraint is real only for narrowing **by place**. Narrowing by **type and date** needs no
place at all, and SRU answers it for the whole country in one query. MEASURED against
`https://repository.overheid.nl/sru`, all 2026-01-01 onward:

| `dt.type` | national count |
|---|---|
| **evenementenvergunning** | **34,481** |
| omgevingsvergunning | 303,186 |
| omgevingsmelding | 23,036 |
| verkeersbesluit of -mededeling | 21,011 |
| exploitatievergunning | 6,527 |

Per-municipality spot checks confirm it is not an artefact of one city: Rotterdam 547, Den Haag
463, Eindhoven 312, Groningen 321, Maastricht 124 event permits. A national page of 12 records
returned Het Hogeland, Alkmaar, Leudal, Maastricht, Alphen-Chaam, Echt-Susteren, Krimpenerwaard,
Westerkwartier, Son en Breugel, Raalte, Sittard-Geleen and Leeuwarden — small municipalities, not
a big-city bias — with real event names (`Opening Vastelaovend`, `Spokentocht Nuis-Niebert`,
`Midwintercrossloop`, `Ilotopie Aqua Forte Parade`).

So widening from the current 3 municipalities (61,853 rows) to the country is a change to the
importer's scope argument, not 342 invocations and not a new data source.

## 2. What we already hold, which is more than it looked

Of the 61,853 imported rows, 2,153 are event publications, and they are not bare bureaucracy:

| field | coverage |
|---|---|
| `start_date` | 1,587 (74%) |
| `end_date` | 1,586 (74%) |
| `visitors_peak` | 1,443 (67%) |

with real titles and forward-looking dates — `Gigantische Vlooienmarkt` (2027-07-10, 2,000),
`Jumping Amsterdam` (2027-01-28, 7,500), `Electric Fireworks 2026` (2026-12-31, **60,000**),
`Thuishaven Circus 2027`. Name, date, expected crowd and geometry is most of an event record.

## 3. The gap, stated precisely

**Public-space events are covered; venue programming is not, and never will be by this source.**
An event in the street, a park or a square needs a municipal permit and is therefore published.
A concert inside Paradiso, a play at a Stadsschouwburg or a film screening needs no such permit,
so no publication exists. Probed: `Paradiso` returns **1** publication for all of 2026 and
`Stadsschouwburg` **5** — i.e. absent. (`Koninklijk Theater Carré` returns 429, but SRU's `any`
operator splits the phrase and matches the word *theater*, so that number is not evidence of
anything.)

Mapped to the question as asked: **markets yes** (a market on a square is permitted and
published), **concerts and theatre no** unless they are open-air.

## 4. The alternatives, and why they do not change the answer

| source | status |
|---|---|
| `data.overheid.nl` CKAN, `q=evenementen` | 45 datasets, **all single-municipality**; `title:evenement` is 12 datasets across 9 separate gemeenten. No national set. |
| Amsterdam "Festivals" (CC-BY 4.0, "daily refreshed") | the CKAN record points at `open.data.amsterdam.nl`, which **does not resolve**. Dead host behind a live index entry. |
| `api.data.amsterdam.nl/v1/evenementen` | **live API, dead data.** HTTP 200, clean HAL+JSON, `titel`/`omschrijving`/`startdatum`/geometry — and **66 records total, newest 2022-12-24**. A reminder that 200 plus a well-formed schema says nothing about whether anyone still fills it. |
| NDTRC (national tourism/culture DB) | not centrally operated since 2021; was never open data. |
| Ticketmaster Discovery API | reachable, `countryCode=NL`, **401 without a key** (free key available). The only probed route to venue programming. Commercial terms unexamined. |

## 5. What this implies for the three phases

- **Phase 1 (events):** no new source. Add a national/type scope to `etl/ingest/bekendmaking.py`.
  34,481 event publications a year, CC0, with dates and crowd sizes.
- **Phase 2 (construction semantic search):** 303,186 omgevingsvergunning + 23,036
  omgevingsmelding nationally. `bekendmaking_activity.activity_text` is already the field the
  embedding work used, so the retrieval side is built.
- **Phase 3 (urban activity):** the same corpus carries traffic orders (21,011) and tree felling
  (`activiteit = 'kappen'`), so "what is changing around my neighbourhood" is one corpus and one
  index, not a federation.

The one thing none of the phases gets from here is indoor cultural programming. If that is
wanted, it is a separate source with separate terms, and it should be decided as such rather
than discovered as a hole later.

## 6. Paging and import cost — measured 2026-11-04

### A national query is not paged to completion, and does not need to be

The wall is on `startRecord`, and it is exact: **`startRecord >= 10000` returns HTTP 504**,
while 9999 and below are served normally. It is a limit on the *offset asked for*, not on the
record reached — `startRecord=9990&maximumRecords=50` answers 200 and hands back records 9990
to 10039, past 10,000.

That does not block a national import, because `_plan()` already cuts a range into windows that
each stay under the ceiling, and the national windows fit:

| scope | per month | verdict |
|---|---|---|
| evenementenvergunning, national | 1,830 / 3,901 / 3,527 / 3,031 / 2,206 (Jan/Mar/May/Jul/Sep) | **month windows are enough** |
| omgevingsvergunning, national | 30,319 (Mar), 25,349 (Sep) | over the ceiling → day windows |
| omgevingsvergunning, one day | 1,207 (2026-03-10) | comfortable |

So phase 1 needs no new mechanism at all, and phase 2 uses the day cascade that already exists.

### Two comments in `etl/ingest/bekendmaking.py` are now wrong

**`PAGE = 100  # the service's own maximum per request` — it is not.** `maximumRecords=1000` is
honoured: the response grows from 455 KB to 4.7 MB, i.e. ten times the payload, so this is not a
silent truncation. 1000 is the real cap, and **asking for more fails in a shape worth knowing**:
2000, 5000 and 10000 all return HTTP **200** with a 971-byte body that echoes
`<maximumRecords>10</maximumRecords>` and contains neither `numberOfRecords` nor a single
record. A client counting records would read that as "no results". Ours survives only by
accident of a guard written for a different case — `_page()` raises when `numberOfRecords` is
missing — which is luck, not design.

**The 9900 ceiling comment describes behaviour that has changed.** It records that
`startRecord 9901` answers 200 with an *empty* page. It no longer does: 9901 returns a full
page of 100. Only the 504 half still holds. `PAGE_CEILING = 9900` stays correct as a
conservative value; its justification needs rewriting.

### Cost of going national, measured

Three sequential requests each, same query window:

| `maximumRecords` | per page | payload | per record |
|---|---|---|---|
| 100 | 0.51 s | 455 KB | 5.1 ms |
| 1000 | 3.09 s | 4.7 MB | 3.1 ms |

Server time scales with records, not with requests, so 1000 buys about 1.6x per record rather
than 10x — but it cuts request *count* by ten, which is what politeness is counted in.

| phase | records | requests @1000 | wall time | transfer |
|---|---|---|---|---|
| 1 — events | 34,481 | ~35 + 12 probes | ~2 min | ~160 MB |
| 2 — construction | 303,186 | ~304 + 365 probes | ~16 min | ~1.4 GB |

(The record-per-page counts in that run were miscounted by a `grep -c`, which counts matching
*lines* and the XML is one line. Payload size is the evidence that 1000 was served, and it is
unambiguous.)

### Rate limits: none published, and one policy question that is not mine to settle

No `ratelimit-*`, `retry-after` or equivalent header is returned. The endpoint sets
`access-control-allow-origin: *` and allows GET/POST/OPTIONS/HEAD, i.e. cross-origin browser
calls were deliberately enabled — this is meant to be called by programs.

**But `https://repository.overheid.nl/robots.txt` is `User-agent: *` / `Disallow: /`** — a
blanket disallow on the host serving the SRU API, and we have already imported 61,853 records
from it. The context cuts both ways and the user should decide, not me:

- against: the literal rule covers the whole host, with no API carve-out.
- for: the same body's human-facing mirror `zoek.officielebekendmakingen.nl` disallows only
  search-result pages and leaves the content open, which reads as "do not crawl search
  results" rather than "no machine access"; `koopoverheid.nl` disallows nothing; and the
  wide-open CORS headers on the SRU endpoint are hard to read as anything but an invitation to
  call it from code.

This is the same shape as the `woningnet.nl` finding in `data-sources.md`, and it gets the same
treatment: **recorded, flagged, and left to the user.** Do not scale up the import until it is
answered.

## 7. The national import, run 2026-11-04 — and the limit it exposed

`etl.ingest.bekendmaking --doc-type evenementenvergunning --from 2026-01-01` imported
**34,481 of 34,481 publications, 0 unparseable**, in 11 monthly windows. `bekendmaking` went
61,853 -> **94,183** rows, `bekendmaking_locatie` -> 160,477, and coverage went from **9
municipalities to 294**. Event publications: 2,153 -> 34,481.

The degradation path held up under its first real test. All 32,330 new rows arrive with no
score, and they surface as `is_noisy = NULL` -> `unscored`, never as `quiet`. Checked end to
end at Rotterdam, which had no data at all before: 17 event permits in a 10-minute shape,
every one of them `noise: "unscored"`, `noise_score: null` — `Koningsdag Stadhuisplein`,
`Koningsdag Terras DJ`, `CECE Kingsday Borrel`.

### The periods do not come, and that is a template problem

Body fetching is fast and works nationally: 50 documents in **1.38 s** at concurrency 4, so
all 32,330 would take roughly 15 minutes. But of those 50 — a genuine national spread across
Achtkarspelen, Alphen aan den Rijn, Barendrecht, Echt-Susteren, Katwijk, Helmond, Purmerend,
Dijk en Waard, Ede and Twenterand — **50 of 50 came back `no_period`.**

The cause is visible in the text. Amsterdam publishes a labelled APV template, which is where
every one of the 1,587 start dates and 1,443 crowd figures in this repo came from:

    Datum van: 05-05-2026
    Datum t/m: 05-05-2026
    Tijd van: 12:00
    Bezoekers drukste moment: 30000

Everywhere else it is prose, and the date is inside a sentence:

    Op 02-03-2026 is de volgende aanvraag voor een vergunning/ontheffing binnengekomen:
    Singelland VO Surhuisterveen schooltriatlon

`etl/ingest/bekendmaking_body.py`'s extractors look for `Looptijd:`, `Datum van:`, `Tijd van:` —
Amsterdam's labels. They are not wrong; they are **local**, and nobody had reason to know that
until there was a second template to compare against.

**So the honest statement of what the national corpus supports today:** event name, location
and publication date everywhere; event *date* and crowd size **in Amsterdam only**. An event
map outside Amsterdam is a map of permit addresses, not a calendar. Publication date is not a
substitute — a permit is published weeks to months before the event.

Three ways out, none of them started:
- extend the extractor per municipality template. Bounded but open-ended; there are 294.
- extract dates from prose with the model we already run offline. Generalises by construction,
  and is unverified — it would need its own labelled sample before any date is shown to a user.
- show no date outside Amsterdam and say so. Correct today, and the weakest product.

Fetching the bodies is still worth doing regardless of dates: it replaces a title-only
`activity_text` (all 32,330 new rows fell back to the title, mean 104 chars) with real prose,
which is the input phase 2's semantic search actually reads.

## 8. Still not checked

- Whether `evenementenvergunning` publication reliably *precedes* the event outside the three
  municipalities already imported — i.e. whether it works as a forward calendar nationally.
- Ticketmaster's licence terms for storing and re-serving event data.
- Whether a 1.4 GB phase-2 transfer should be rate-limited on our side regardless of the
  absence of a published limit. On the evidence above, it should.
- Whether `evenementenvergunning` publication *predates* the event reliably enough to be useful
  as a forward calendar everywhere, or only in the municipalities already imported.
- Ticketmaster's licence terms for storing and re-serving event data.
