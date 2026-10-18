-- The category vocabulary and the OSM tag rules that feed it.
--
-- Kept in its own migration because this is reference data that will be amended by
-- later migrations as categories are added, while 0003 is structure that should not
-- need to change.
--
-- scripts/import_poi.py builds the `osmium tags-filter` expression by reading
-- poi_category_rule, so this table is the single source of truth for which objects
-- are imported at all. Adding a row here is the only step needed to widen the import.

INSERT INTO poi_category (category, label_en) VALUES
    ('supermarket',      'Supermarket'),
    ('convenience',      'Convenience store'),
    ('bakery',           'Bakery'),
    ('gym',              'Gym'),
    ('sports_centre',    'Sports centre'),
    ('swimming_pool',    'Swimming pool'),
    ('park',             'Park'),
    ('playground',       'Playground'),
    ('school',           'School'),
    ('kindergarten',     'Kindergarten / childcare'),
    ('university',       'University / college'),
    ('library',          'Library'),
    ('pharmacy',         'Pharmacy'),
    ('doctor',           'GP / doctor'),
    ('dentist',          'Dentist'),
    ('hospital',         'Hospital'),
    ('veterinary',       'Veterinary'),
    ('restaurant',       'Restaurant'),
    ('cafe',             'Cafe'),
    ('bar',              'Bar / pub'),
    ('fast_food',        'Fast food'),
    ('bank',             'Bank'),
    ('post_office',      'Post office'),
    ('hairdresser',      'Hairdresser'),
    ('clothes',          'Clothing shop'),
    ('hardware',         'Hardware / DIY'),
    ('fuel',             'Fuel station'),
    ('charging_station', 'EV charging station'),
    ('recycling',        'Recycling point'),
    ('bicycle_parking',  'Bicycle parking'),
    ('car_parking',      'Car parking'),
    ('bus_stop',         'Bus stop'),
    ('tram_stop',        'Tram stop'),
    ('train_station',    'Train station'),
    ('museum',           'Museum'),
    ('hotel',            'Hotel'),
    ('attraction',       'Tourist attraction');

-- priority: lower wins when one object carries several matching tags. Specific
-- retail/amenity tags are 10; generic or fallback tags are 50+.
INSERT INTO poi_category_rule (osm_key, osm_value, category, priority) VALUES
    -- retail
    ('shop',             'supermarket',      'supermarket',      10),
    ('shop',             'convenience',      'convenience',      10),
    ('shop',             'bakery',           'bakery',           10),
    ('shop',             'hairdresser',      'hairdresser',      10),
    ('shop',             'clothes',          'clothes',          10),
    ('shop',             'doityourself',     'hardware',         10),
    ('shop',             'hardware',         'hardware',         10),

    -- sport & recreation. A gym is `leisure=fitness_centre` on some objects and
    -- `amenity=gym` on others; this is exactly the split that was invisible while the
    -- mapping lived in an f-string.
    ('leisure',          'fitness_centre',   'gym',              10),
    ('amenity',          'gym',              'gym',              10),
    ('leisure',          'sports_centre',    'sports_centre',    20),
    ('leisure',          'swimming_pool',    'swimming_pool',    20),
    ('leisure',          'park',             'park',             20),
    ('leisure',          'playground',       'playground',       20),

    -- education
    ('amenity',          'school',           'school',           10),
    ('amenity',          'kindergarten',     'kindergarten',     10),
    ('amenity',          'childcare',        'kindergarten',     20),
    ('amenity',          'university',       'university',       10),
    ('amenity',          'college',          'university',       20),
    ('amenity',          'library',          'library',          10),

    -- health. amenity=* and healthcare=* overlap heavily on the same objects; both
    -- are listed so neither tagging style is missed, and they agree on the category.
    ('amenity',          'pharmacy',         'pharmacy',         10),
    ('healthcare',       'pharmacy',         'pharmacy',         50),
    ('amenity',          'doctors',          'doctor',           10),
    ('healthcare',       'doctor',           'doctor',           50),
    ('amenity',          'dentist',          'dentist',          10),
    ('healthcare',       'dentist',          'dentist',          50),
    ('amenity',          'hospital',         'hospital',         10),
    ('healthcare',       'hospital',         'hospital',         50),
    ('amenity',          'veterinary',       'veterinary',       10),

    -- food & drink
    ('amenity',          'restaurant',       'restaurant',       10),
    ('amenity',          'cafe',             'cafe',             10),
    ('amenity',          'bar',              'bar',              10),
    ('amenity',          'pub',              'bar',              20),
    ('amenity',          'fast_food',        'fast_food',        10),

    -- services & utilities
    ('amenity',          'bank',             'bank',             10),
    ('amenity',          'post_office',      'post_office',      10),
    ('amenity',          'fuel',             'fuel',             10),
    ('amenity',          'charging_station', 'charging_station', 10),
    ('amenity',          'recycling',        'recycling',        20),

    -- parking. Deliberately included despite being high-volume and low-interest:
    -- bicycle_parking in particular is enormous in the Netherlands, which is what
    -- makes the category distribution skewed enough for the index experiments in 1.6
    -- to have a real answer.
    ('amenity',          'bicycle_parking',  'bicycle_parking',  30),
    ('amenity',          'parking',          'car_parking',      30),

    -- public transport. Needed for the multi-predicate join that motivates the whole
    -- stage ("supermarkets within 300 m of a tram stop").
    ('highway',          'bus_stop',         'bus_stop',         20),
    ('railway',          'tram_stop',        'tram_stop',        20),
    ('railway',          'station',          'train_station',    20),
    ('railway',          'halt',             'train_station',    30),

    -- tourism
    ('tourism',          'museum',           'museum',           10),
    ('tourism',          'hotel',            'hotel',            10),
    ('tourism',          'attraction',       'attraction',       30);
