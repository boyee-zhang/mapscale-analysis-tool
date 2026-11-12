-- The impact classes and the rules that assign them.
--
-- Reference data, so its own migration (0011 is structure). Amending a rule is an INSERT or
-- UPDATE here in a later migration, and the view picks it up with no reload.
--
-- Every pattern below was read off measured output, not guessed: the vocabulary counts in
-- docs/pg-notes/semsearch-ab.md (661 rows carry a direct noise signal, of which only 13 say
-- `geluidhinder`) and the family census in docs/pg-notes/noise-groundtruth.md.
--
-- `\y` is the word boundary in a Postgres regex. `\b` is a backspace and matches nothing --
-- an earlier probe returned 0 rows for a pattern that was visibly present because of it.

INSERT INTO noise_impact (impact, label_en, rank) VALUES
    ('explicit',       'Noise or working hours named in the document', 1),
    ('loud_activity',  'A loud activity named, noise implied',         2),
    ('event',          'Event, intensity depends on time and crowd',   3),
    ('undeterminable', 'Needs the period, which the index drops',      4),
    ('none',           'Administrative only, no works',                5);

INSERT INTO noise_impact_rule (match_on, pattern, impact, priority, note) VALUES
    -- 10 — the document itself names noise, or permission to work outside normal hours.
    -- `tijdstippen van uitvoering` is the exact subject and contains no word for noise, which
    -- is why a noise-vocabulary search misses it; `lozen van grondwater` shares the
    -- maatwerkvoorschrift doc_type and is not noise, which is why doc_type cannot carry this.
    ('text', 'nachtwerk',                   'explicit', 10, 'Nachtwerkontheffing: <=70dB permitted at night or weekend'),
    ('text', 'tijdstippen van uitvoering',  'explicit', 10, 'permission to deviate from the permitted hours of execution'),
    ('text', 'werktijden|arbeidstijd',      'explicit', 10, NULL),
    ('text', 'geluid|lawaai',               'explicit', 15, 'includes geluidsbepalingen, geluidhinder, bouwlawaai'),
    ('text', 'versterkte muziek',           'explicit', 15, NULL),

    -- 20 — a named activity that is loud by its nature. No noise word appears in these.
    ('text', '\ysloop|slopen|sloopmelding',             'loud_activity', 20, NULL),
    ('text', 'heien|heiwerk|damwand|trillen',           'loud_activity', 20, 'pile driving and sheet piling'),
    ('text', 'asfalt|graafwerk|straatwerk',             'loud_activity', 20, NULL),
    ('text', 'kraan|hijs',                              'loud_activity', 20, 'a crane closes a street and runs for days'),

    -- 30 — events. doc_type is load-bearing here: in a 1,117-row frame 24 evenement
    -- publications said nothing about an event in title or abstract and were classified only
    -- by doc_type. This is the row that fixes that gap.
    ('doc_type', 'evenement',               'event', 30, 'the gap the text-only rules missed'),
    ('text', 'evenement|festival|kermis',   'event', 30, NULL),

    -- 40 — administrative acts. A splitsingsvergunning is the legal act of dividing
    -- ownership; any construction it leads to needs its own omgevingsvergunning and is
    -- published separately, so the splitsing itself is silent.
    ('text', 'splits',                                  'none', 40, 'cadastral/ownership split, paper only'),
    ('text', 'ligplaats',                               'none', 40, NULL),
    ('text', 'horeca|drank|terras|exploitatie',         'none', 40, 'a licence, not works; noise would be a maatwerkvoorschrift'),
    ('doc_type', 'verkeersbesluit',                     'none', 45, 'changes a sign or a priority rule; the works, if any, get a TVM'),

    -- 90 — the honest default for the APV family and for bare building work: the index record
    -- cannot answer it. 51-54% of every geographic slice landed here, at every radius tried,
    -- before bekendmaking_body existed. Not 'none': a two-day skip container and 144 days of
    -- scaffolding with a crane are the same row until the period is fetched.
    ('text', 'objectvergunning|\yObject\y',                     'undeterminable', 90, 'scaffolding, container, skip, portable toilet'),
    ('text', 'tijdelijke verkeersmaatregel|\yTVM\y',            'undeterminable', 90, 'parking bays or a street closed for works'),
    ('text', 'rvv-verkeersontheffing',                          'undeterminable', 90, NULL),
    ('activiteit', 'bouwen',                                    'undeterminable', 95, 'no further detail in the index record'),
    ('activiteit', 'slopen',                                    'loud_activity',  20, NULL),
    ('activiteit', 'kappen',                                    'loud_activity',  25, 'chainsaws, a morning not a season');
