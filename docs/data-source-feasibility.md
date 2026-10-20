# Data source feasibility — raw probe results, 2026-10-17

Written after the H2S GraphQL endpoint started returning 403. The question was not
"how do we get H2S back" but "which Dutch housing data source can this project depend
on for years". Every number below was measured, not quoted.

## 1. Commercial rental platforms are uniformly unavailable

| Host | Protection | honest `curl/8.x` UA | spoofed Chrome 131 UA |
| --- | --- | --- | --- |
| `api.holland2stay.com/graphql/` | Cloudflare managed challenge | 403 | 403 |
| `www.huurwoningen.nl` listing page | Cloudflare managed challenge | 403 | 403 |
| `www.pararius.nl` listing page | Cloudflare managed challenge | 403 | 403 |
| `www.funda.nl/robots.txt` | own interstitial page | HTML block page | HTML block page |
| `www.woningnet.nl/` | nginx rule | **200** | **403** |

`cf-mitigated: challenge` + a `Just a moment...` body whose CSP allows
`script-src https://challenges.cloudflare.com` means Cloudflare answered before the
request reached the application. Passing the TLS/JA3 check is necessary but no longer
sufficient — all seven `curl_cffi` impersonation targets (chrome110/120/124/131/133a,
safari17_0, firefox133) return a byte-identical 403, so the fingerprint is no longer the
discriminator. A plain HTTP client has no JS engine and cannot produce the token.

Three findings worth keeping:

**`huurwoningen.nl` and `pararius.nl` are one codebase.** Both `robots.txt` files carry
the same `Disallow: /frontend/assets/*` + `Allow: /frontend/assets/Website/<Brand>/BrandIcons/*`
structure. They are Pararius Group properties sharing one Cloudflare configuration, so
they are **one** data source, not two, and they fail together.

**`robots.txt` contradicts the actual policy, twice.** H2S publishes `Allow: /` and a
`Sitemap:` line; that sitemap returns 403. `huurwoningen.nl` ships a 512-line `robots.txt`
that explicitly `Allow:`s ~30 city listing paths; `sitemap.xml` returns 403. In both cases
the stated crawl policy is more permissive than the enforced one — most likely Bot Fight
Mode switched on wholesale, overriding the hand-written rules.

**On `woningnet.nl`, honesty outperforms impersonation.** `curl/8.x` gets 200 and a real
`<title>Homepage - WoningNet</title>`; the same request with a Chrome User-Agent gets 403.
Their rule appears to be "claims to be a browser but lacks the rest of a browser's
signature → bot". The generic `Allow: /` applies to the honest client.

But `woningnet.nl/robots.txt` also contains:

```
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: anthropic-ai
...
Disallow: /          # Block AI bots from crawling any pages
Crawl-delay: 10
```

Our ETL is not any of those agents so the rule does not match literally, but the intent is
a clear "no AI-adjacent crawling". **Treating WoningNet as a provider is a policy decision,
not a technical one, and is left open deliberately.** If it is ever adopted, `Crawl-delay: 10`
is binding.

### Why the platforms block

Listing inventory *is* the product — Funda, Pararius and Huurwoningen do not own housing,
they own the aggregated list, and Funda is part-owned by the NVM broker association whose
members pay to be listed. They also license the same data commercially, so free scraping
competes with their own B2B line, which is why protection strength tracks commercial value
(CBS statistics are given away; listing detail is defended). Finally, in a market this
tight, polling bots find listings before humans do, which created a paid sniping-bot
industry; our daily snapshot is harmless but is indistinguishable at the protocol level
from a sniper. The blocking is rational, not arbitrary.

**Conclusion: no legally and technically reachable commercial listing source exists.**
Circumventing the challenge is out of scope — it needs a browser runtime the Vercel
deployment cannot host, `cf_clearance` is bound to IP + User-Agent and expires, and the
one-year-then-sudden-403 history of the `curl_cffi` approach is itself the proof that the
arms race is unwinnable. A collection pipeline with random multi-week gaps produces data
that cannot be analysed anyway, because an outage is indistinguishable from zero supply.

## 2. BAG — `verblijfsobject` via PDOK WFS

`https://service.pdok.nl/lv/bag/wfs/v2_0` — no protection, no key, no rate limit observed.

Feature types: `bag:pand`, `bag:ligplaats`, `bag:standplaats`, `bag:verblijfsobject`,
`bag:woonplaats`. `verblijfsobject` is the addressable unit (dwelling), which is the one
that matters.

`DescribeFeatureType` → every field, verbatim:

| field | type | note |
| --- | --- | --- |
| `geom` | `gml:GeometryPropertyType` | Point |
| `identificatie` | string | stable national BAG id — the natural key |
| `rdf_seealso` | string | permalink |
| `oppervlakte` | long | usable floor area, m2 |
| `status` | string | e.g. `Verblijfsobject in gebruik` |
| `gebruiksdoel` | string | e.g. `woonfunctie` |
| `openbare_ruimte` | string | street |
| `huisnummer` | long | |
| `huisletter` | string | |
| `toevoeging` | string | |
| `postcode` | string | `9901KB` form, no internal space |
| `woonplaats` | string | |
| `bouwjaar` | long | construction year |
| `pandidentificatie` | string | FK to `bag:pand` |
| `pandstatus` | string | |

A real feature, `outputFormat=application/json&srsName=EPSG:4326`:

```json
{
  "identificatie": "0003010000125985",
  "oppervlakte": 69,
  "status": "Verblijfsobject in gebruik",
  "gebruiksdoel": "woonfunctie",
  "openbare_ruimte": "Molenstraat",
  "huisnummer": 16,
  "postcode": "9901KB",
  "woonplaats": "Appingedam",
  "bouwjaar": 2002,
  "pandidentificatie": "0003100000122770",
  "geometry": { "type": "Point", "coordinates": [6.854402481878347, 53.32119143104485] }
}
```

### Volume

`resultType=hits` on the unfiltered type: **`numberMatched="9994899"`** — 9.99M dwellings
nationally. That lands exactly in the 10-20M band stage 3 was going to reach with synthetic
rows, except these are real.

### Paging has a hard 1000-row cap

Requesting `count=2000&startIndex=5000` returns HTTP 200 with **1000** features and no
warning. `ImplementsResultPaging` and `CountDefault` are both advertised in
`GetCapabilities` and `startIndex` works, so full extraction is possible but costs
~10,000 requests. Measured 694 KB per 1000 features as GeoJSON, i.e. ~7 GB over the wire
for the whole country.

### Filtering: `FILTER` works, `CQL_FILTER` is silently ignored

```
bbox=51.40,5.40,51.48,5.54,urn:ogc:def:crs:EPSG::4326   -> numberMatched="148134"
FILTER=<fes:PropertyIsEqualTo>woonplaats = Eindhoven     -> numberMatched="149577"
CQL_FILTER=woonplaats='Eindhoven'                        -> numberMatched="9994899"
```

**The `CQL_FILTER` result is the trap.** It returns HTTP 200 and a plausible-looking
response while quietly ignoring the predicate, so a bug here reads as "Eindhoven has 10M
dwellings" rather than as an error. Use the OGC XML `FILTER` form. The `woonplaats` filter
returns slightly more than the bbox (149,577 vs 148,134) because the municipality is not
rectangular.

Per-city extraction is therefore ~150 requests; the 26 cities currently in `CITY_CENTRES`
come to ~3,900 requests. That is the incremental path.

### Bulk download is the right path for a full load

Atom feed `https://service.pdok.nl/lv/bag/atom/bag.xml` (redirects to `/kadaster/bag/atom/`):

| file | size | format |
| --- | --- | --- |
| `bag-light.gpkg` | 7,818,600,448 B (7.82 GB) | GeoPackage, **EPSG:28992** |
| `lvbag-extract-nl.zip` | 3,644,747,052 B (3.64 GB) | raw LVBAG XML |

`Last-Modified: Wed, 30 Sep 2026 23:39:11 GMT`. GeoPackage is SQLite, so `ogr2ogr` loads it
straight into PostGIS — far better than 10,000 WFS calls. Note the CRS is RD New (28992),
not 4326, so it needs `-t_srs EPSG:4326` to match this project's geometry convention.

`https://api.pdok.nl/lv/bag/ogc/v1/collections` returns 404 — there is no OGC API Features
endpoint for BAG, only WFS 2.0.

## 3. CBS OData — housing tables

`https://opendata.cbs.nl/ODataApi/odata/<id>` — no protection. Catalogue query:

```
/ODataCatalog/Tables?$filter=substringof('woning',tolower(Title))&$select=Identifier,Title,Frequency,Period,Modified&$format=json
```

40+ housing tables. Note the matcher is a naive substring: `substringof('uur',Title)`
matches `arbeidsduur` (working hours), not `huur` (rent).

Rental-relevant ones:

| id | frequency | period | title |
| --- | --- | --- | --- |
| `86098NED` | **monthly** | 2015-01 .. 2026-08 | Levensloop van woningen; gebruiksfunctie, regio |
| `85773NED` | monthly | 1995-01 .. 2026-08 | Bestaande koopwoningen; prijsindex 2020=100 |
| `86287NED` | yearly | 2021-2026 | Voorraad huurwoningen; type eigenaar, portefeuille verhuurder, regio |
| `86288NED` | yearly | 2021-2026 | Eigenaren huurwoningen; type eigenaar, portefeuille |
| `83162NED` | yearly | 2015-2026 | Huurverhoging woningen; regio |
| `84823NED` | yearly | 2015-2026 | Huurontwikkeling woningen; soort verhuurder |
| `85036NED` | yearly | 2019-2026 | Gemiddelde WOZ-waarde van woningen; eigendom, regio |
| `82211NED` | quarterly | 2015 Q1 .. 2026 Q1 | Woningen in de pijplijn; gebruiksfunctie, regio |

Every table exposes the same sub-resources: `TableInfos`, `TypedDataSet`,
`UntypedDataSet`, `DataProperties`, `CategoryGroups`, plus one per dimension.

### `86098NED` shape — a textbook star schema

`DataProperties` gives 3 dimensions and 22 topics:

- `RegioS` — `GeoDimension`, **480** entries (`NL01`=Nederland, `LD01`=Noord-Nederland,
  down to individual municipalities), i.e. mixed aggregation levels in one column
- `Gebruiksfunctie` — `Dimension`
- `Perioden` — `TimeDimension`, **197** entries, `2015MM01` .. `2026MM08`
- 22 `Topic` measures: `BeginstandVoorraad_1`, `VergundeNieuwbouw_2`, `BouwGestart_3`,
  ... `Sloop_15`, `Onttrekkingen_16`, ... `SaldoVoorraad_21`, `EindstandVoorraad_22`

`TypedDataSet/$count` = **1,323,840** rows.

### The important part: CBS periods are revised

`Perioden` carries a `Status` field:

```json
{"Key": "2015MM01", "Title": "2015 januari", "Status": "Definitief"}
{"Key": "2026MM08", "Title": "2026 augustus", "Description": "Voorlopige cijfers", "Status": "Voorlopig"}
```

The newest months are `Voorlopig` (provisional) and get restated to `Definitief` later.
This is a genuine, externally-imposed reason for period-scoped idempotent upsert: the same
`(region, function, period)` key legitimately changes value on a later run, and the ETL has
to converge rather than accumulate duplicates. It is a better motivation for the stage 1
machinery than scraping ever was, because the revision is real rather than hypothetical.

## 4. EP-Online — energy labels (NOT probed by hand; corrected after review)

An earlier draft of this document and the recommendation built on it asserted that energy
labels were unavailable. **That was wrong, and it was the load-bearing error.** Recorded here
so the mistake is not repeated.

RVO's EP-Online is the national energy-label register and publishes **open bulk files**:
monthly full exports plus **daily mutation files**, in XML / CSV / XLSX, around 227-360 MB for
the October 2026 full file depending on format (`https://www.ep-online.nl/PublicData`). A free
key from `apikey.ep-online.nl` is the only gate. There is also a per-address REST endpoint
`https://public.ep-online.nl/api/v5/PandEnergielabel/Adres`.

The decisive field is **`Pand_bagverblijfsobjectid`** — a direct foreign key to BAG
`verblijfsobject.identificatie`. Labels therefore join to BAG without any address matching.

Coverage: ~5.93M of ~8.3M dwellings registered (about 71%) as of 2026-08-01, and rental stock
is labelled at a materially higher rate than owner-occupied.

**Unresolved, and it gates the product shape:** data.overheid.nl lists the EP-Online webservice
as *"geen open licentie"*. Obtainable is not the same as redistributable. Confirm the licence
before designing anything that republishes label data rather than consulting it per query.

The daily mutation stream is also the only record of *when* a label changed — the monthly full
files are snapshots. That makes label history genuinely non-backfillable from today onward.

## 5. WOZ per dwelling — a permanent ceiling, not a gap

WOZ values are *openbaar* but **not open data**. There is no national bulk dataset; the
WOZ-waardeloket is an individual-consultation facility and mass retrieval is not permitted.
The official Kadaster *WOZ API Bevragen* requires a Dutch government OIN plus a PKIoverheid
certificate. Compliant bulk access therefore does not exist for this project at any effort
level; the only routes are paid commercial resellers.

Treat this as a structural ceiling. Do not add "probe WOZ bulk access" to any plan.

Available substitutes, in increasing coarseness:
- **COROP-region average WOZ per m2** — published by the Huurcommissie as bijlage 1 of the
  WWSO beleidsboek (2026 range roughly EUR 2,015 Delfzijl to EUR 6,378 Groot-Amsterdam). For
  *rooms* this is not an approximation, it is the legally prescribed input (see below).
- **Neighbourhood (buurt) average** — CBS Kerncijfers wijken en buurten, CC-BY 4.0, far finer
  than the municipality-level `85036NED` cited in section 3.

## 6. Huurcommissie public rulings register — unprobed, CC0, and the best remaining asset

`https://data.overheid.nl/dataset/openbaar-register-uitspraken` — the Huurcommissie's own
register of rent-tribunal decisions, roughly 12,685 records, searchable by subject, **postcode**
and case number, published under **Creative Commons Public Domain Mark 1.0**, the most
permissive licence available. Standing bulk-data requests already exist on data.overheid.nl, so
asking for a bulk export is the normal route rather than an imposition.

This is the only legitimate source of real rent-dispute *outcomes* in the Netherlands. It is
append-only, growing, joinable to BAG by postcode, and nobody has made it queryable. It is the
closest surviving analogue to the proprietary transactional asset that the listing scrape was
supposed to produce.

Note: the register was taken offline in May 2024 after a data breach and only restored from
September 2025. Handle conservatively.

**Not yet probed.** This is the highest-value outstanding probe in this document.

## 7. WWS reference values (verified 2026) — and where our data actually fits

The woningwaarderingsstelsel is the statutory points system that caps Dutch rent. Since
**2025-01-01 a landlord must supply a point count with every new tenancy**, so a tenant already
holds an itemised breakdown; and the Huurcommissie publishes its own free Huurprijscheck. Any
design premised on *computing* points for the tenant is therefore competing with the regulator
while holding worse inputs.

| element | 2026 rule |
| --- | --- |
| `vertrekken` floor area | 1 point / m2 |
| `overige ruimten` | 0.75 point / m2 |
| WOZ | `WOZ / EUR 16,954` **plus** `(WOZ per m2) / EUR 268` — **two components** |
| WOZ cap | applies only to dwellings reaching 187+ points uncapped; capping returns them to 186 |
| energy label | +62 (A++++) down to -15 (G) |
| no private outdoor space | -5 points |
| new mid-rent builds | +10% on the maximum rent for 20 years after first occupancy |

| points | segment | 2026 maximum rent |
| --- | --- | --- |
| <= 143 | social | EUR 932.93 / month |
| 144-186 | mid-market | EUR 1,228.07 / month |
| >= 187 | free sector | no cap |

Mapping our measured data onto this, corrected:

- **BAG `oppervlakte` is NOT the WWS area.** It is the lumped NEN 2580 total
  (`gebruiksoppervlakte wonen` + `overige inpandige ruimte`). WWS needs `vertrekken` and
  `overige ruimten` separated at different point rates, and the two measurement bases treat
  load-bearing internal walls differently. Using it as "1 point per m2" systematically
  **overestimates**. Its only sound use is as a **one-sided upper bound**: claimed WWS area can
  never exceed BAG total internal area.
- **WOZ per dwelling: unavailable** (section 5). With a realistic within-neighbourhood spread,
  the WOZ term alone swings roughly 45 points — and the entire mid-market band is only 43 points
  wide. An area+regional-WOZ estimate cannot discriminate between segments.
- **Facilities** (kitchen 0 to ~26, sanitary 0 to ~32, heating, outdoor space) require physical
  inspection. Altum AI's commercial WWS API explicitly does not auto-fill these either.

**The one case where regional averages are legally correct: rooms.** For *onzelfstandige*
woonruimte (WWSO) the WOZ rubric is not a division at all — it compares the dwelling's WOZ/m2
against the published COROP average and awards 10 / 12 / 14 points. WOZ uncertainty is bounded
at 4 points rather than 45. This is also the most exploited segment (students, labour migrants,
expats) and no existing free tool pairs it with registry prefill.

**Legal constraints that must be in any design, independent of data quality:**
- The `Wet betaalbare huur` binds on the contract's **conclusion date**. Contracts concluded
  before 2024-07-01 are largely outside the new rules; low-segment pre-July-2024 contracts only
  came under them from 2025-07-01, and the excessive-increase ban from 2026-01-01. A tool taking
  only address + rent will confidently tell long-standing tenants their legal rent is illegal.
  **Contract date is a required input, not optional.**
- Challenging an initial rent is a **fatale termijn**: 6 months from contract start, not
  extendable, EUR 25 filing fee.
- Publishing a per-address "probably illegal" layer over 10M dwellings is a GDPR problem. Keep
  verdicts query-time and ephemeral; aggregate before publishing.

## 8. Assessment against stage 1

Stage 1's learning content is idempotent upsert, the `etl_run` state machine, the
`(provider_id) WHERE status='running'` partial unique index, write skew, and the >50%
sentinel check.

CBS `86098NED` supplies real restatement semantics (`Voorlopig -> Definitief`) and an explicit
period key, which the idempotency key wants. But on its own it **weakens** most of the rest:
monthly cadence makes duplicate delivery rare, CBS rows never disappear so the sentinel defends
against nothing, and the whole series ships historically so nothing is urgent to capture.

**EP-Online's daily mutation files restore what CBS alone removes:** daily cadence,
re-deliverable files, missed days requiring reconciliation against the monthly full export,
file-level idempotency keyed on `(source, file_date)`, and a genuine sentinel — a mutation file
that would retire an implausible share of labels must fail the run rather than be applied. Label
history is also not backfillable, which restores the original "lose a day, lose it forever"
forcing function.

Honest accounting of what the listing scrape took with it: time-to-rent, price-cut behaviour,
weekly new-supply flow, disappeared-vs-booked. BAG, CBS and EP-Online describe **stock**, not
transactions. The Huurcommissie register (section 6) is the only transactional substitute, and
it covers disputes rather than lettings.

Open items, ranked:
1. **Probe the Huurcommissie CC0 rulings register** (section 6) — highest value, unprobed.
2. **Confirm the EP-Online licence** (section 4) — gates whether labels can be republished or
   only consulted, which determines the product shape.
3. WoningNet: **decline on both grounds.** Beyond the AI-bot `Disallow:`, it is a
   waiting-time allocation platform, not a priced market, so it would not restore any of the
   four lost metrics. Previously logged as "unresolved"; now resolved as no.
4. Ask H2S for documented API access. Zero cost, but expect no — they are a landlord whose
   interest is inbound leads, not data distribution. Do not plan around it.
5. `providers/h2s.py` stays as the first implementation of the provider ABC and as evidence of
   the Cloudflare escalation; mark it degraded rather than delete it.
