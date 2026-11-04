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
  noisy:    { label: 'Likely noisy', color: '#FF3B30', hint: 'Model predicts works or an event a neighbour would hear' },
  quiet:    { label: 'Likely quiet', color: '#8E8E93', hint: 'Model predicts no audible works' },
  unscored: { label: 'Not scored',   color: '#C7C7CC', hint: 'No prediction yet — not a verdict of quiet' },
};

export const NOISE_ORDER = ['noisy', 'quiet', 'unscored'];

/**
 * Score → colour. A SEQUENTIAL encoding: one hue, light → dark, because the thing being
 * shown is a magnitude ("how likely is this to be loud"), not an identity. Never a rainbow,
 * and never a hue change partway up — the step from pale to deep is the whole signal.
 *
 * THE STOPS ARE MEASURED QUANTILES, NOT ROUND NUMBERS, and that is the point. The corpus
 * score distribution is strongly bimodal: 64% of publications sit below 0.4 and 29% above
 * 0.8, with only 7% in the 0.4–0.8 valley between them. A ramp spread evenly over 0–1
 * therefore spends most of its colour on a range almost nothing occupies, and squashes the
 * two places the data actually lives into a single shade each. Anchoring on p10/p25/p50/
 * p75/p90/max instead makes colour track RANK, so equal colour steps mean equal numbers of
 * publications — the choropleth rule, for the same reason.
 *
 * The visible symptom of getting this wrong, which is how it was found: the first version
 * ramped linearly and hit full red at 0.85, while building permits score 0.856–0.929
 * (p5–p95). Every dot in the default view was the identical red, so a gradient was being
 * drawn and no gradient could be seen.
 *
 * Hexes validated against the pale map basemap (#f2f1ee): monotone lightness, every
 * adjacent gap ΔL ≥ 0.06, light end 2.13:1 — above the 2:1 floor, so the quietest dot is
 * still a dot and not a smudge of the basemap. Hue spread 6°, i.e. genuinely one hue.
 */
export const NOISE_RAMP = [
  [0.09, '#e4918f'],  // p10
  [0.21, '#d66663'],  // p25
  [0.37, '#c53f3b'],  // p50
  [0.87, '#a22e29'],  // p75
  [0.93, '#7a211d'],  // p90
  [0.99, '#511210'],  // max
];

/** Unscored is NOT on the ramp — see `noiseColor`. */
export const NOISE_UNSCORED_COLOR = '#b8b8b2';

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
  if (score >= 0.93) return 'Among the noisiest';
  if (score >= 0.87) return 'Noisier than most';
  if (score >= 0.37) return 'Middling';
  if (score >= 0.21) return 'Quieter than most';
  return 'Among the quietest';
};

/** The panel's filter. `null` sends no param, so the backend returns every bucket. */
export const NOISE_FILTERS = [
  { value: null, label: 'All' },
  { value: ['noisy'], label: 'Noisy' },
  { value: ['quiet'], label: 'Quiet' },
];
