import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

const SOURCE_ID    = 'housing-choropleth';
const LISTINGS_SRC = 'housing-listings';
const STATIONS_SRC = 'housing-stations';

// ── Icon factories ────────────────────────────────────────────────────────────

function makeHouseIcon(color, size = 28) {
  const s = size * 2; // 2× for crispness
  const c = document.createElement('canvas');
  c.width = s; c.height = s;
  const ctx = c.getContext('2d');
  const w = s, h = s;

  // Drop shadow
  ctx.shadowColor = 'rgba(0,0,0,0.28)';
  ctx.shadowBlur = s * 0.10;
  ctx.shadowOffsetY = s * 0.04;

  // Roof
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(w * 0.50, h * 0.04);
  ctx.lineTo(w * 0.93, h * 0.44);
  ctx.lineTo(w * 0.07, h * 0.44);
  ctx.closePath();
  ctx.fill();

  // Body
  ctx.fillRect(w * 0.11, h * 0.42, w * 0.78, h * 0.52);

  ctx.shadowColor = 'transparent';

  // Left window
  ctx.fillStyle = 'rgba(255,255,255,0.75)';
  ctx.fillRect(w * 0.14, h * 0.50, w * 0.20, h * 0.16);

  // Right window
  ctx.fillRect(w * 0.66, h * 0.50, w * 0.20, h * 0.16);

  // Arched door
  ctx.fillStyle = 'rgba(255,255,255,0.90)';
  const dx = w * 0.37, dy = h * 0.62, dw = w * 0.26, dh = h * 0.32;
  ctx.beginPath();
  ctx.arc(dx + dw / 2, dy + dw / 2, dw / 2, Math.PI, 0);
  ctx.lineTo(dx + dw, dy + dh);
  ctx.lineTo(dx, dy + dh);
  ctx.closePath();
  ctx.fill();

  return ctx.getImageData(0, 0, s, s);
}

// ── Static data ───────────────────────────────────────────────────────────────

const STATIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    ['Amersfoort', 52.1528, 5.3742], ['Amsterdam', 52.3791, 4.9003],
    ['Arnhem', 51.9247, 5.9027],     ['Capelle aan den IJssel', 51.9209, 4.5683],
    ['Delft', 52.0062, 4.3560],      ['Den Bosch', 51.6906, 5.2930],
    ['Diemen', 52.3467, 4.9406],     ['Dordrecht', 51.8106, 4.6744],
    ['Eindhoven', 51.4432, 5.4796],  ['Groningen', 53.2106, 6.5636],
    ['Haarlem', 52.3877, 4.6371],    ['Helmond', 51.4833, 5.6556],
    ['Leiden', 52.1659, 4.4800],     ['Maarssen', 52.1389, 5.0389],
    ['Maastricht', 50.8511, 5.7075], ['Nieuwegein', 52.0317, 5.0872],
    ['Nijmegen', 51.8425, 5.8523],   ['Rijswijk', 52.0483, 4.3236],
    ['Rotterdam', 51.9249, 4.4690],  ['Sittard', 51.0017, 5.8681],
    ['The Hague', 52.0806, 4.3239],  ['Tilburg', 51.5614, 5.0786],
    ['Utrecht', 52.0894, 5.1099],    ['Velp', 51.9978, 5.9828],
    ['Zeist', 52.0878, 5.2336],      ['Zoetermeer', 52.0633, 4.4953],
  ].map(([city, lat, lng]) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: { city },
  })),
};

// ── MapLibre layer specs ──────────────────────────────────────────────────────

const FILL_COLOR = [
  'case', ['==', ['get', 'listing_count'], 0], '#dde4ea',
  ['interpolate', ['linear'], ['get', 'listing_count'],
    1, '#c6dff3', 5, '#7db8d8', 15, '#2980b9', 30, '#1a5276', 50, '#0d2137'],
];

const LAYER_FILL    = { id: 'housing-fill',    type: 'fill',   source: SOURCE_ID, paint: { 'fill-color': FILL_COLOR, 'fill-opacity': 0.55 } };
const LAYER_OUTLINE = { id: 'housing-outline', type: 'line',   source: SOURCE_ID, paint: { 'line-color': '#fff', 'line-width': 1.5, 'line-opacity': 0.9 } };
const LAYER_LABELS  = {
  id: 'housing-labels', type: 'symbol', source: SOURCE_ID,
  layout: {
    'text-field': ['format', ['get', 'city'], { 'font-scale': 0.75 }, '\n', {}, ['to-string', ['get', 'listing_count']], { 'font-scale': 1.1 }, ' avail.', { 'font-scale': 0.8 }],
    'text-font': ['Noto Sans Bold', 'Arial Unicode MS Bold'],
    'text-size': 11, 'text-allow-overlap': false,
  },
  paint: {
    'text-color': ['case', ['==', ['get', 'listing_count'], 0], '#6b7f8e', '#fff'],
    'text-halo-color': ['case', ['==', ['get', 'listing_count'], 0], 'rgba(255,255,255,0.6)', 'rgba(0,40,80,0.4)'],
    'text-halo-width': 1.5,
  },
};

const LAYER_LISTINGS = {
  id: 'housing-listing-icons', type: 'symbol', source: LISTINGS_SRC,
  layout: {
    'icon-image': [
      'case',
      ['==', ['downcase', ['coalesce', ['get', 'property_type'], '']], 'studio'], 'icon-house-studio',
      ['==', ['get', 'property_type'], '1'], 'icon-house-1bed',
      'icon-house-2bed',
    ],
    'icon-size': 1,
    'icon-allow-overlap': true,
    'icon-anchor': 'bottom',
  },
};

const LAYER_STATIONS = {
  id: 'housing-stations', type: 'symbol', source: STATIONS_SRC,
  layout: {
    'text-field': '🚉',
    'text-size': 18,
    'text-allow-overlap': true,
    'text-anchor': 'center',
  },
  paint: {
    'text-halo-color': '#ffffff',
    'text-halo-width': 1.5,
  },
};

// ── Component ─────────────────────────────────────────────────────────────────

const popupStyle = {
  position: 'absolute', background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(8px)',
  borderRadius: '10px', padding: '12px 16px', boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
  fontSize: '13px', lineHeight: '1.6', maxWidth: '200px', pointerEvents: 'none', zIndex: 10,
};

const countBarStyle = (n) => ({
  height: '6px', borderRadius: '3px', marginTop: '6px', width: '100%',
  background: n === 0 ? '#dde4ea' : `linear-gradient(90deg,#c6dff3,#2980b9 ${Math.min(n / 50 * 100, 100)}%)`,
});

export default function HousingLayer({ map, isReady, provider = 'h2s', direction = 'future', onCityClick, highlightCities }) {
  const months = direction === 'past' ? 1 : 3;
  const [popup, setPopup]         = useState(null);
  const [loading, setLoading]     = useState(false);
  const fullChoroplethRef         = useRef(null);

  function applyFilter(choropleth, cities) {
    if (!cities?.length) return choropleth;
    return {
      ...choropleth,
      features: choropleth.features.filter(f => cities.includes(f.properties.city)),
    };
  }

  useEffect(() => {
    if (!isReady || !map) return;
    let mounted = true;

    async function load() {
      setLoading(true);
      try {
        const choropleth = await api.fetchHousingChoropleth(provider, months, direction);
        if (!mounted) return;
        fullChoroplethRef.current = choropleth;

        if (map.getSource(SOURCE_ID)) {
          map.getSource(SOURCE_ID).setData(applyFilter(choropleth, highlightCities));
        } else {
          map.addImage('icon-house-studio', makeHouseIcon('#f97316', 28), { pixelRatio: 2 });
          map.addImage('icon-house-1bed',   makeHouseIcon('#2980b9', 28), { pixelRatio: 2 });
          map.addImage('icon-house-2bed',   makeHouseIcon('#27ae60', 28), { pixelRatio: 2 });

          map.addSource(SOURCE_ID,    { type: 'geojson', data: applyFilter(choropleth, highlightCities) });
          map.addSource(LISTINGS_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
          map.addSource(STATIONS_SRC, { type: 'geojson', data: STATIONS_GEOJSON });

          map.addLayer(LAYER_FILL);
          map.addLayer(LAYER_OUTLINE);
          map.addLayer(LAYER_LISTINGS);
          map.addLayer(LAYER_STATIONS);
          map.addLayer(LAYER_LABELS);

          map.on('click', LAYER_FILL.id, (e) => {
            const p = e.features[0].properties;
            setPopup({ x: e.point.x, y: e.point.y, city: p.city, count: p.listing_count });
            if (onCityClick) onCityClick(p.city);
          });
          map.on('mouseenter', LAYER_FILL.id, () => { map.getCanvas().style.cursor = 'pointer'; });
          map.on('mouseleave', LAYER_FILL.id, () => { map.getCanvas().style.cursor = ''; });
          map.on('click', (e) => {
            if (!map.queryRenderedFeatures(e.point, { layers: [LAYER_FILL.id] }).length) setPopup(null);
          });
        }
        console.log('[HousingLayer] choropleth loaded', choropleth.features.length);
      } catch (err) {
        console.error('[HousingLayer] choropleth failed', err);
      } finally {
        if (mounted) setLoading(false);
      }

      // Listings icons — only in future mode; past listings are gone from H2S once booked.
      // Choropleth (Upstash ETL) is the authoritative source for past data.
      if (direction === 'future') {
        try {
          const cities = highlightCities?.length ? highlightCities : null;
          const listings = await api.fetchHousingListings(provider, cities, months, direction);
          if (!mounted) return;
          map.getSource(LISTINGS_SRC)?.setData(listings);
          console.log('[HousingLayer] listings loaded', listings.features.length);
        } catch (err) {
          console.error('[HousingLayer] listings failed (non-fatal)', err);
        }
      }
    }

    load();

    return () => {
      mounted = false;
      [LAYER_LABELS.id, LAYER_STATIONS.id, LAYER_LISTINGS.id, LAYER_OUTLINE.id, LAYER_FILL.id].forEach(id => {
        if (map.getLayer(id)) map.removeLayer(id);
      });
      [SOURCE_ID, LISTINGS_SRC, STATIONS_SRC].forEach(id => {
        if (map.getSource(id)) map.removeSource(id);
      });
      ['icon-house-studio', 'icon-house-1bed', 'icon-house-2bed'].forEach(id => {
        if (map.hasImage(id)) map.removeImage(id);
      });
      setPopup(null);
    };
  }, [isReady, map, provider, months, direction]);

  // Re-filter choropleth and reload listings when highlightCities changes.
  // direction/months/provider are included to avoid stale closure.
  useEffect(() => {
    if (!isReady || !map) return;
    const src = map.getSource(SOURCE_ID);
    if (src && fullChoroplethRef.current) {
      src.setData(applyFilter(fullChoroplethRef.current, highlightCities));
    }
    if (direction === 'future') {
      const cities = highlightCities?.length ? highlightCities : null;
      api.fetchHousingListings(provider, cities, months, direction)
        .then(listings => map.getSource(LISTINGS_SRC)?.setData(listings))
        .catch(() => {});
    }
  }, [highlightCities, isReady, map, provider, months, direction]);

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
            {popup.count} {direction === 'past' ? 'new listings last month' : 'available next 3 months'}
          </div>
          <div style={countBarStyle(popup.count)} />
        </div>
      )}

      <div style={{
        position: 'absolute', bottom: 24, right: 20,
        background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)',
        borderRadius: '12px', padding: '10px 14px', zIndex: 20,
        boxShadow: '0 2px 12px rgba(0,0,0,0.1)', fontSize: '11px', lineHeight: '2',
      }}>
        {[
          [null, '#f97316', 'Studio'],
          [null, '#2980b9', '1-bed'],
          [null, '#27ae60', '2-bed+'],
        ].map(([, color, label]) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ width: 10, height: 10, borderRadius: '2px', background: color, display: 'inline-block' }} />
            <span style={{ color: '#444' }}>{label}</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 13 }}>🚉</span>
          <span style={{ color: '#444' }}>Central Station</span>
        </div>
      </div>
    </>
  );
}
