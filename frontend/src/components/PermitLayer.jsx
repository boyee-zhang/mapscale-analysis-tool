import { useEffect } from 'react';
import maplibregl from 'maplibre-gl';
import { STAGE_ORDER, STAGE, stageLabel } from '../constants/bekendmaking';

/**
 * Announcements as a MapLibre circle layer, not as `<PermitMarker>` DOM elements.
 *
 * PoiMarker mounts one DOM node per POI, which is fine for the few dozen a walking
 * isochrone yields. This endpoint returns up to 500 items and a driving isochrone hits
 * that cap every time, so these go into a GeoJSON source and a single paint expression —
 * one node instead of 500, and MapLibre does the hit-testing.
 *
 * Renders nothing: the component is an imperative wrapper around the map, like the
 * isochrone effect in MapContainer. All mutations are guarded on `map` being ready.
 */

const SOURCE = 'permits-source';
const LAYER = 'permits-layer';
const LAYER_HALO = 'permits-halo';

// Both layers are interactive and both must be in MapContainer's click guard. The dot is
// 2-7px depending on zoom; the halo around it is 4-18px and is most of what a person
// actually aims at. Hit-testing only the dot meant a click that visibly landed on a permit
// fell through to the map handler and moved the isochrone instead of opening the popup —
// found by clicking one in the browser, not by reading the code.
export const PERMIT_LAYER_IDS = [LAYER, LAYER_HALO];

// stage → colour, as a MapLibre `match` expression. Built from the same constant the
// legend and the panel read, so a colour cannot drift between the three.
const STAGE_COLOR = [
  'match', ['get', 'stage'],
  ...STAGE_ORDER.flatMap(s => [s, STAGE[s].color]),
  STAGE.overig.color,
];

const toFeatureCollection = (items) => ({
  type: 'FeatureCollection',
  features: items.map(it => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [it.lon, it.lat] },
    // MapLibre feature properties are flat: `activiteit` is an array and would arrive at
    // the popup as "[object Object]" through the expression layer, so it is joined here.
    properties: {
      identifier: it.identifier,
      title: it.title,
      stage: it.stage,
      published_at: it.published_at,
      gemeente: it.gemeente || '',
      activiteit: (it.activiteit || []).join(', '),
      url: it.url,
    },
  })),
});

const popupHtml = (p) => {
  const esc = (v) => String(v ?? '').replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
  ));
  return `
    <div style="font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:280px">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
        <span style="width:8px;height:8px;border-radius:50%;background:${STAGE[p.stage]?.color || STAGE.overig.color}"></span>
        <strong style="font-size:11px;letter-spacing:.3px;text-transform:uppercase;color:#86868b">
          ${esc(stageLabel(p.stage))}
        </strong>
        <span style="margin-left:auto;font-size:11px;color:#86868b">${esc(p.published_at)}</span>
      </div>
      <div style="color:#1d1d1f;margin-bottom:6px">${esc(p.title)}</div>
      <div style="font-size:11px;color:#86868b">
        ${esc(p.activiteit || '—')}${p.gemeente ? ` · ${esc(p.gemeente)}` : ''}
      </div>
      <a href="${esc(p.url)}" target="_blank" rel="noreferrer"
         style="display:inline-block;margin-top:8px;font-size:11px;font-weight:600;color:#007AFF;text-decoration:none">
        Official publication ↗
      </a>
    </div>`;
};

const PermitLayer = ({ map, isReady, items, visible }) => {
  // Add the source and layers once, then only ever setData. Re-adding them on every data
  // change would drop the popup and re-trigger the paint transition.
  useEffect(() => {
    if (!isReady || !map) return;

    if (!map.getSource(SOURCE)) {
      map.addSource(SOURCE, { type: 'geojson', data: toFeatureCollection([]) });
      // A wide translucent halo under the dot: at a city-wide zoom the dots overlap into
      // a solid mass, and the halo is what still reads as density rather than as fill.
      map.addLayer({
        id: LAYER_HALO,
        type: 'circle',
        source: SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 4, 14, 10, 17, 18],
          'circle-color': STAGE_COLOR,
          'circle-opacity': 0.18,
        },
      });
      map.addLayer({
        id: LAYER,
        type: 'circle',
        source: SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2, 14, 4.5, 17, 7],
          'circle-color': STAGE_COLOR,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#ffffff',
        },
      });
    }

    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '300px', offset: 10 });

    const onClick = (e) => {
      const f = e.features?.[0];
      if (!f) return;
      // MapLibre dispatches layer handlers and the bare map handler from the same event,
      // and stopPropagation on the DOM event does not stop its internal dispatch. So the
      // real guard lives in MapContainer, which skips a click that hit PERMIT_LAYER_ID
      // before moving the centre. This line only keeps the DOM event from travelling
      // further; it is not what prevents the double action.
      e.originalEvent?.stopPropagation();
      popup.setLngLat(f.geometry.coordinates.slice()).setHTML(popupHtml(f.properties)).addTo(map);
    };
    const onEnter = () => { map.getCanvas().style.cursor = 'pointer'; };
    const onLeave = () => { map.getCanvas().style.cursor = ''; };

    PERMIT_LAYER_IDS.forEach(id => {
      map.on('click', id, onClick);
      map.on('mouseenter', id, onEnter);
      map.on('mouseleave', id, onLeave);
    });

    return () => {
      PERMIT_LAYER_IDS.forEach(id => {
        map.off('click', id, onClick);
        map.off('mouseenter', id, onEnter);
        map.off('mouseleave', id, onLeave);
      });
      popup.remove();
    };
  }, [isReady, map]);

  useEffect(() => {
    if (!isReady || !map || !map.getSource(SOURCE)) return;
    map.getSource(SOURCE).setData(toFeatureCollection(visible ? (items || []) : []));
  }, [isReady, map, items, visible]);

  useEffect(() => {
    if (!isReady || !map) return;
    PERMIT_LAYER_IDS.forEach(id => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    });
  }, [isReady, map, visible]);

  return null;
};

export default PermitLayer;
