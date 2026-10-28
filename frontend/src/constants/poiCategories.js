// POI category catalogue, grouped for colouring.
//
// The 37 keys here mirror the `poi_category` table — `/pois` accepts them as repeated
// `categories` params and returns `category` on every element. Kept as a frontend constant
// by decision (2026-10-28): the alternative was two columns on `poi_category` plus an
// endpoint, which is more correct but more moving parts than this needs.
//
// The cost of that decision: a category added to the table will NOT appear here, so it
// cannot be selected and its markers fall back to the neutral colour. If you widen the
// import (an INSERT into `poi_category_rule`), add the key below too.
//
// Colours are one per GROUP, not per category — 37 distinguishable colours do not exist.
// Within a group you tell markers apart from the popup, not the dot.
// #AF52DE is deliberately absent: the isochrone area already uses it.

export const POI_GROUPS = [
  {
    key: 'shopping',
    label: 'Shopping',
    color: '#FF9500',
    categories: [
      ['supermarket', 'Supermarket'],
      ['convenience', 'Convenience store'],
      ['bakery', 'Bakery'],
      ['clothes', 'Clothing shop'],
      ['hardware', 'Hardware / DIY'],
    ],
  },
  {
    key: 'food',
    label: 'Food & drink',
    color: '#FF3B30',
    categories: [
      ['restaurant', 'Restaurant'],
      ['fast_food', 'Fast food'],
      ['cafe', 'Cafe'],
      ['bar', 'Bar / pub'],
    ],
  },
  {
    key: 'health',
    label: 'Health',
    color: '#34C759',
    categories: [
      ['pharmacy', 'Pharmacy'],
      ['doctor', 'GP / doctor'],
      ['dentist', 'Dentist'],
      ['hospital', 'Hospital'],
      ['veterinary', 'Veterinary'],
    ],
  },
  {
    key: 'education',
    label: 'Education',
    color: '#007AFF',
    categories: [
      ['school', 'School'],
      ['kindergarten', 'Kindergarten / childcare'],
      ['university', 'University / college'],
      ['library', 'Library'],
    ],
  },
  {
    key: 'leisure',
    label: 'Sport & leisure',
    color: '#FFCC00',
    categories: [
      ['gym', 'Gym'],
      ['sports_centre', 'Sports centre'],
      ['swimming_pool', 'Swimming pool'],
      ['playground', 'Playground'],
      ['park', 'Park'],
      ['attraction', 'Tourist attraction'],
      ['museum', 'Museum'],
    ],
  },
  {
    key: 'transport',
    label: 'Transport',
    color: '#5AC8FA',
    categories: [
      ['bus_stop', 'Bus stop'],
      ['tram_stop', 'Tram stop'],
      ['train_station', 'Train station'],
      ['car_parking', 'Car parking'],
      ['bicycle_parking', 'Bicycle parking'],
      ['fuel', 'Fuel station'],
      ['charging_station', 'EV charging station'],
    ],
  },
  {
    key: 'services',
    label: 'Services',
    color: '#8E8E93',
    categories: [
      ['bank', 'Bank'],
      ['post_office', 'Post office'],
      ['hairdresser', 'Hairdresser'],
      ['recycling', 'Recycling point'],
      ['hotel', 'Hotel'],
    ],
  },
];

// Shown when a marker's category is missing or unknown — the Overpass fallback path
// (no DATABASE_URL, i.e. production) returns no `category` at all, so every marker
// lands here rather than rendering invisibly.
export const UNKNOWN_COLOR = '#FF4500';

/** category key → group colour */
export const CATEGORY_COLOR = Object.fromEntries(
  POI_GROUPS.flatMap(g => g.categories.map(([key]) => [key, g.color]))
);

/** category key → human label */
export const CATEGORY_LABEL = Object.fromEntries(
  POI_GROUPS.flatMap(g => g.categories.map(([key, label]) => [key, label]))
);

/** Every category key, in group order. */
export const ALL_CATEGORIES = POI_GROUPS.flatMap(g => g.categories.map(([key]) => key));

/**
 * What the map starts with. The backend's own default when `categories` is omitted is the
 * legacy supermarket/convenience/gym set; this mirrors it so the first render matches what
 * the old build showed, and so the initial request stays small.
 */
export const DEFAULT_CATEGORIES = ['supermarket', 'convenience', 'gym'];

export const colorFor = (category) => CATEGORY_COLOR[category] || UNKNOWN_COLOR;
