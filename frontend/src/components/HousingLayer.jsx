import { useEffect, useState } from 'react';
import { api } from '../api';

const SOURCE_ID = 'housing-choropleth';

// Fill color: gray for 0, blue gradient for 1–50+
const FILL_COLOR = [
  'case',
  ['==', ['get', 'listing_count'], 0],
  '#dde4ea',
  [
    'interpolate', ['linear'], ['get', 'listing_count'],
    1,  '#c6dff3',
    5,  '#7db8d8',
    15, '#2980b9',
    30, '#1a5276',
    50, '#0d2137',
  ],
];

const LAYER_FILL = {
  id: 'housing-fill',
  type: 'fill',
  source: SOURCE_ID,
  paint: {
    'fill-color': FILL_COLOR,
    'fill-opacity': 0.65,
  },
};

const LAYER_OUTLINE = {
  id: 'housing-outline',
  type: 'line',
  source: SOURCE_ID,
  paint: {
    'line-color': '#ffffff',
    'line-width': 1.5,
    'line-opacity': 0.9,
  },
};

const LAYER_LABELS = {
  id: 'housing-labels',
  type: 'symbol',
  source: SOURCE_ID,
  layout: {
    'text-field': [
      'format',
      ['get', 'city'], { 'font-scale': 0.75 },
      '\n', {},
      ['to-string', ['get', 'listing_count']], { 'font-scale': 1.1 },
      ' available apartment', { 'font-scale': 0.85 },
    ],
    'text-font':  ['Noto Sans Bold', 'Arial Unicode MS Bold'],
    'text-size':  12,
    'text-allow-overlap': false,
    'text-ignore-placement': false,
  },
  paint: {
    'text-color': [
      'case',
      ['==', ['get', 'listing_count'], 0], '#6b7f8e',
      '#ffffff',
    ],
    'text-halo-color': [
      'case',
      ['==', ['get', 'listing_count'], 0], 'rgba(255,255,255,0.6)',
      'rgba(0,40,80,0.4)',
    ],
    'text-halo-width': 1.5,
  },
};

const popupStyle = {
  position: 'absolute',
  background: 'rgba(255,255,255,0.95)',
  backdropFilter: 'blur(8px)',
  borderRadius: '10px',
  padding: '12px 16px',
  boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
  fontSize: '13px',
  lineHeight: '1.6',
  maxWidth: '200px',
  pointerEvents: 'none',
  zIndex: 10,
};

const countBarStyle = (count) => ({
  height: '6px',
  borderRadius: '3px',
  marginTop: '6px',
  background: count === 0
    ? '#dde4ea'
    : `linear-gradient(90deg, #c6dff3, #2980b9 ${Math.min(count / 50 * 100, 100)}%)`,
  width: '100%',
});

export default function HousingLayer({ map, isReady, provider = 'h2s', direction = 'future' }) {
  const months = direction === 'past' ? 1 : 3;
  const [popup, setPopup] = useState(null); // { x, y, city, count }
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isReady || !map) return;

    let mounted = true;

    async function load() {
      setLoading(true);
      try {
        const geojson = await api.fetchHousingChoropleth(provider, months, direction);
        if (!mounted) return;

        if (map.getSource(SOURCE_ID)) {
          map.getSource(SOURCE_ID).setData(geojson);
        } else {
          map.addSource(SOURCE_ID, { type: 'geojson', data: geojson });
          map.addLayer(LAYER_FILL);
          map.addLayer(LAYER_OUTLINE);
          map.addLayer(LAYER_LABELS);

          map.on('click', LAYER_FILL.id, (e) => {
            const props = e.features[0].properties;
            setPopup({ x: e.point.x, y: e.point.y, city: props.city, count: props.listing_count });
          });

          map.on('mouseenter', LAYER_FILL.id, () => {
            map.getCanvas().style.cursor = 'pointer';
          });
          map.on('mouseleave', LAYER_FILL.id, () => {
            map.getCanvas().style.cursor = '';
          });

          map.on('click', (e) => {
            if (!map.queryRenderedFeatures(e.point, { layers: [LAYER_FILL.id] }).length) {
              setPopup(null);
            }
          });
        }

        console.log('[HousingLayer] choropleth loaded', geojson.features.length, 'cities');
      } catch (err) {
        console.error('[HousingLayer] failed to load choropleth', err);
      } finally {
        if (mounted) setLoading(false);
      }
    }

    load();

    return () => {
      mounted = false;
      [LAYER_LABELS.id, LAYER_OUTLINE.id, LAYER_FILL.id].forEach(id => {
        if (map.getLayer(id)) map.removeLayer(id);
      });
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      setPopup(null);
    };
  }, [isReady, map, provider, months, direction]);

  return (
    <>
      {loading && (
        <div style={{
          position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)',
          borderRadius: '20px', padding: '8px 18px', fontSize: '12px',
          color: '#2980b9', fontWeight: 600, zIndex: 20,
          boxShadow: '0 2px 12px rgba(0,0,0,0.12)',
        }}>
          正在加载房源地图…
        </div>
      )}

      {popup && (
        <div style={{ ...popupStyle, left: popup.x + 12, top: popup.y - 72 }}>
          <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: 2 }}>{popup.city}</div>
          <div style={{ color: popup.count > 0 ? '#2980b9' : '#94a3b8', fontWeight: 600 }}>
            {popup.count} {direction === 'past' ? 'booked last month' : 'available next 3 months'}
          </div>
          <div style={countBarStyle(popup.count)} />
        </div>
      )}
    </>
  );
}
