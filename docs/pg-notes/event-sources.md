# Is there a source for Dutch events? — probed 2026-11-04

Asked before building semantic search: phase 1 wants Netherlands-wide event documents (markets,
concerts, theatre), phase 2 construction, phase 3 urban activity generally.

**Answer: yes, and it is the source already in use. No new API is needed for public-space
events, and no source exists — in open data — for venue programming.**

## 1. The headline, and a correction to a documented project constraint

`CLAUDE.md` and the memory index both say `import_bekendmaking.py` is scoped per municipality
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

- **Phase 1 (events):** no new source. Add a national/type scope to `import_bekendmaking.py`.
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

## 6. Not yet checked

- Whether SRU paginates a 34,481-row type-scoped query to completion, or caps out. Only a
  12-record page and `maximumRecords=0` counts were pulled.
- Rate limits and etiquette for a national import. The current importer's politeness settings
  were sized for one municipality at a time.
- Whether `evenementenvergunning` publication *predates* the event reliably enough to be useful
  as a forward calendar everywhere, or only in the municipalities already imported.
- Ticketmaster's licence terms for storing and re-serving event data.
