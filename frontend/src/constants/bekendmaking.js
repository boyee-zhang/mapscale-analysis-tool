// Official announcements (`/api/main/bekendmakingen`), as the map presents them.
//
// Kept as a frontend constant for the same reason as poiCategories.js: the alternative is
// a lookup endpoint over `bekendmaking.doc_type`, which is more correct and more moving
// parts than this needs. The cost is the same too — a doc_type the importer starts seeing
// will render with the fallback colour and its raw Dutch name until it is added here.

/**
 * `stage` is DERIVED BY THE BACKEND FROM THE TITLE, not published as a field. 99.8% of
 * building permits classify (measured over 10,342); `overig` is genuine ambiguity, such as
 * a title that is only the word "Omgevingsvergunning". Labels say "(from title)" so the
 * UI never presents it as source data.
 */
export const STAGE = {
  aanvraag: { label: 'Applied for', color: '#FF9500', hint: 'Application filed — a plan, not yet permitted' },
  ontwerp:  { label: 'Draft decision', color: '#5AC8FA', hint: 'Draft decision open to objection' },
  besluit:  { label: 'Decided', color: '#34C759', hint: 'Decision published — granted, refused or withdrawn' },
  overig:   { label: 'Unclassified', color: '#8E8E93', hint: 'Title did not say which stage' },
};

export const STAGE_ORDER = ['aanvraag', 'ontwerp', 'besluit', 'overig'];

export const stageColor = (stage) => (STAGE[stage] || STAGE.overig).color;
export const stageLabel = (stage) => (STAGE[stage] || STAGE.overig).label;

/**
 * `doc_type` → an English label. The full published vocabulary is 15 values; these are the
 * ones that occur often enough to name, and anything else falls through to its own Dutch
 * name, which is the documented cost of keeping this map in the frontend (see the note at
 * the top of this file) rather than behind a lookup endpoint.
 *
 * This became the panel's primary breakdown when the hard-coded `building_permits=true`
 * filter was lifted. Before that the panel only ever saw one doc_type, so breaking down BY
 * doc_type would have drawn a single bar; `activiteit` was the only axis with any variation
 * in view. Now that every type is in scope it is the other way round — `activiteit` is
 * populated for omgevingsvergunning alone, i.e. 29% of the corpus.
 */
export const DOC_TYPE_LABEL = {
  'andere vergunning': 'Other permit',
  'omgevingsvergunning': 'Building / environment',
  'verkeersbesluit of -mededeling': 'Traffic order',
  'exploitatievergunning': 'Business licence',
  'evenementenvergunning': 'Event',
  'omgevingsmelding': 'Works notification',
  'andere beschikking': 'Other decision',
  'ander besluit van algemene strekking': 'General order',
  'andere voorlichtingsinformatie': 'Public information',
  'overige overheidsinformatie': 'Other official info',
  'andere melding': 'Other notification',
  'participatie': 'Participation',
  'vergunning voor activiteiten op of in oppervlaktewater': 'Surface water',
  'ruimtelijk plan of omgevingsdocument': 'Zoning document',
  'evenementmelding': 'Event notification',
};

/** The activities an omgevingsvergunning can carry. Only that doc_type has any. */
export const ACTIVITEIT_LABEL = {
  bouwen: 'Building',
  'ruimtelijke ordening': 'Zoning',
  kappen: 'Tree felling',
  reclame: 'Signage',
  milieu: 'Environment',
  natuur: 'Nature',
  slopen: 'Demolition',
  'uitweg en inrit': 'Driveway',
  'brandveilig gebruik': 'Fire safety',
};

/**
 * How far back the panel looks. The backend caps the window at 730 days, so 24 months is
 * the widest option rather than an arbitrary stop.
 */
export const WINDOWS = [
  { months: 3, label: '3 m' },
  { months: 6, label: '6 m' },
  { months: 12, label: '12 m' },
  { months: 24, label: '24 m' },
];

export const DEFAULT_WINDOW_MONTHS = 12;

/** `YYYY-MM` → `Mar '26`, for histogram ticks that have to fit in a panel. */
export const monthTick = (ym) => {
  const [y, m] = ym.split('-');
  const name = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m) - 1];
  return `${name} '${y.slice(2)}`;
};

/**
 * The noise classifier's verdict, as the map presents it.
 *
 * `noise` is a MODEL OUTPUT, not a published field, and is labelled as such everywhere it
 * surfaces — the same discipline `stage` already gets for being derived from the title. The
 * model is logistic regression over 18 metadata features plus a 768-d multilingual-e5-base
 * embedding of the publication's activity description, measured at precision 0.919 / recall
 * 0.866 (F1 0.891) against 695 hand-labelled publications in three Amsterdam/Utrecht blocks.
 * The response's `noise_models[0].metrics` carries the live figures; these labels carry the
 * caveat so a red dot never reads as a fact.
 *
 * `unscored` is its OWN bucket and must stay that way. It means no score exists — a
 * publication imported after the last scoring run — and rendering it as `quiet` would turn
 * "we have not judged this" into "this is fine", which is the one error a resident would
 * actually act on.
 */
export const NOISE = {
  // The two buckets are the two POLES of the diverging scale, so the filter pills read as
  // the same encoding as the dots. Grey is reserved for the midpoint and must not be spent
  // on `quiet`, which is a side, not a middle.
  noisy:    { label: 'Likely noisy', color: '#e34948', hint: 'Model predicts works or an event a neighbour would hear' },
  quiet:    { label: 'Likely quiet', color: '#2a78d6', hint: 'Model predicts no audible works' },
  unscored: { label: 'Not scored',   color: '#6e6d68', hint: 'No prediction yet — not a verdict of quiet' },
};

export const NOISE_ORDER = ['noisy', 'quiet', 'unscored'];

/**
 * Score → colour. A DIVERGING encoding: blue on one side, red on the other, a neutral grey
 * between them. It replaced a single-hue red ramp, and the change is not cosmetic.
 *
 * WHY DIVERGING IS THE RIGHT FORM HERE, now that it has been looked at properly. The score has
 * a real midpoint — `noise_model.threshold`, MEASURED at 0.3826 — and that midpoint is where the
 * model flips its own `is_noisy` verdict. A sequential ramp says "more of the same thing" all
 * the way across and so cannot show a boundary; a diverging ramp puts the hue flip exactly on
 * it, so the colour and the verdict agree by construction. The distribution backs this up: the
 * median is 0.373, within 0.01 of the threshold, and the data is strongly bimodal — 64% below
 * 0.4, 29% above 0.8, only 7.5% in the 0.4–0.8 valley. Two masses either side of a meaningful
 * line is the textbook diverging case, and reading it as one magnitude was the earlier mistake.
 *
 * Blue for quiet, red for noisy, which is also the CVD-safe pair: red/green would collapse for
 * the commonest colour-vision deficiencies, red/blue does not.
 *
 * THE STOPS ARE STILL MEASURED QUANTILES, per arm, so equal colour steps mean equal numbers of
 * publications: p10/p25 on the blue arm, p75/p95/max on the red. The red arm deliberately spends
 * three of its four steps above 0.8, because that is where 29% of the corpus sits while the
 * 0.4–0.8 valley holds almost nothing.
 *
 * VALIDATED, not eyeballed, with the dataviz validator against the pale basemap (#f2f1ee) — and
 * for a diverging ramp the gate is lightness monotonicity PER ARM, not adjacent-pair CVD, which
 * the categorical validator would fail by design. Both arms pass all four ordinal checks:
 * monotone L, every adjacent ΔL ≥ 0.06, single hue (3° spread each), and the step nearest the
 * midpoint clears the 2:1 surface floor (#6da7ec 2.22:1, #e08a86 2.28:1).
 *
 * The 2:1 floor is what fixes the shape: it caps lightness at about L 0.73, so the NEUTRAL has
 * to be the lightest point in the whole ramp (#a9a7a0, L 0.728, 2.13:1) and each arm descends
 * from it to L ≈ 0.39 at its extreme. The two arms are near-mirrored in lightness
 * (0.385/0.480/0.575/0.717 against 0.389/0.506/0.623/0.722), which is the "equal steps per arm"
 * rule holding.
 */
export const NOISE_THRESHOLD = 0.3826;

export const NOISE_RAMP = [
  [0.013, '#104281'],   // min — the quietest publications in the corpus
  [0.091, '#1c5cab'],   // p10
  [0.211, '#2a78d6'],   // p25
  [0.330, '#6da7ec'],
  [NOISE_THRESHOLD, '#a9a7a0'],  // the model's own noisy/quiet cut, and the lightest step
  [0.500, '#e08a86'],
  [0.870, '#e34948'],   // p75
  [0.923, '#b02f2f'],   // p95
  [0.994, '#7a1f1f'],   // max
];

/**
 * Unscored is NOT on the ramp — see `noiseColor`. It is now a DARK outline rather than a pale
 * grey, and it had to change: the midpoint of a diverging ramp is itself neutral grey, so the
 * old `#b8b8b2` would have read as "sitting exactly on the threshold" — the one value it must
 * not be confused with. Measured, that old value was also below the 2:1 surface floor at
 * 1.76:1, so it was already too faint to be a dot; this one is 4.59:1 and sits at L 0.534
 * against the midpoint's 0.728, which no interpolation of the ramp reaches at that lightness.
 */
export const NOISE_UNSCORED_COLOR = '#6e6d68';

/**
 * Colour for one item. Takes the score, not the bucket.
 *
 * `null` (no score) returns a neutral that is deliberately OFF the ramp rather than its
 * palest step. "We have not judged this" is not "we judged this and it is quiet", and a
 * ramp position would state the second. The map backs that up with a second channel —
 * unscored dots are drawn hollow — so the distinction does not rest on colour alone.
 */
export const noiseColor = (score) => {
  if (score == null) return NOISE_UNSCORED_COLOR;
  const stops = NOISE_RAMP;
  if (score <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i += 1) {
    const [x0, c0] = stops[i - 1];
    const [x1, c1] = stops[i];
    if (score <= x1) return mixHex(c0, c1, (score - x0) / (x1 - x0));
  }
  return stops[stops.length - 1][1];
};

/** Linear sRGB-space blend. Good enough between adjacent steps of one validated hue. */
const mixHex = (a, b, t) => {
  const n = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [ar, ag, ab] = n(a);
  const [br, bg, bb] = n(b);
  const h2 = (v) => Math.round(v).toString(16).padStart(2, '0');
  return `#${h2(ar + (br - ar) * t)}${h2(ag + (bg - ag) * t)}${h2(ab + (bb - ab) * t)}`;
};

export const noiseLabel = (noise) => (NOISE[noise] || NOISE.unscored).label;

/**
 * Plain words for a score, so the colour is never the only carrier. Bands are the same
 * measured quantiles the ramp uses: "most" = above p75, i.e. this publication is in the
 * noisiest quarter of everything we have scored. Phrased as a comparison on purpose —
 * the score is a logistic output on 695 training rows, not a calibrated probability, so
 * "in the top quarter" is supportable where "92% chance of noise" is not.
 */
export const noiseBand = (score) => {
  if (score == null) return 'Not scored';
  if (score >= 0.923) return 'Among the noisiest';
  if (score >= 0.870) return 'Noisier than most';
  // The middle boundary is the model's threshold, not a round number near it. The words and the
  // hue now change at the same place the `is_noisy` flag does, so a dot that reads blue can
  // never be labelled noisy.
  if (score >= NOISE_THRESHOLD) return 'Just over the line';
  if (score >= 0.211) return 'Quieter than most';
  return 'Among the quietest';
};

/** The panel's filter. `null` sends no param, so the backend returns every bucket. */
export const NOISE_FILTERS = [
  { value: null, label: 'All' },
  { value: ['noisy'], label: 'Noisy' },
  { value: ['quiet'], label: 'Quiet' },
];

/**
 * The event's own date, which is NOT `published_at`.
 *
 * A permit is published weeks to months before the thing happens — `Electric Fireworks 2026`
 * was published in February for 31 December — so showing the publication date where a reader
 * expects the event date is not a cosmetic error, it is a wrong answer to the question they
 * asked. The two are rendered differently and labelled.
 *
 * `derived` says HOW the date was obtained, and the distinction is kept all the way to the
 * reader. `stated` means the publisher wrote it into a labelled field. `derived` means it was
 * read out of a sentence by scripts/event_dates.py — measured at precision 100.0%, recall
 * 65.5%, zero hallucinations on 30 municipalities it was not built against, which is good and
 * is still a different kind of fact from a value someone typed into a form.
 *
 * 65.5% recall is the reason `by_event_date` exists in the response and is shown: a calendar
 * that silently drops a third of its entries looks complete and is not.
 */
export const fmtEventDate = (start, end) => {
  if (!start) return null;
  const d = (x) => new Date(x + 'T00:00:00').toLocaleDateString('en-GB',
    { day: 'numeric', month: 'short', year: 'numeric' });
  return !end || end === start ? d(start) : `${d(start)} – ${d(end)}`;
};
