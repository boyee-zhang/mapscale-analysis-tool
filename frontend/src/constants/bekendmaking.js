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

export const noiseColor = (noise) => (NOISE[noise] || NOISE.unscored).color;
export const noiseLabel = (noise) => (NOISE[noise] || NOISE.unscored).label;

/** The panel's filter. `null` sends no param, so the backend returns every bucket. */
export const NOISE_FILTERS = [
  { value: null, label: 'All' },
  { value: ['noisy'], label: 'Noisy' },
  { value: ['quiet'], label: 'Quiet' },
];
