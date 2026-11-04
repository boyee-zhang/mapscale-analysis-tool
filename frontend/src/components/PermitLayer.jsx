import { useEffect } from 'react';
import maplibregl from 'maplibre-gl';
import {
  stageLabel,
  noiseBand, noiseColor, NOISE_RAMP, NOISE_UNSCORED_COLOR,
} from '../constants/bekendmaking';

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

// noise → colour, as a MapLibre `match` expression. Built from the same constant the legend
// and the panel read, so a colour cannot drift between the three.
//
// The dots used to be coloured by `stage` (applied / draft / decided). They are coloured by
// the noise prediction instead because that is the question the layer now answers, and two
// categorical colour scales on one set of dots would mean neither is readable. `stage` did not
// go away: it is in the popup and in the panel list, where a label can say what it is.
// MEASURED, and it is why this is a gradient and not three buckets: the model's BINARY
// verdict is a four-value doc_type lookup. `doc_type IN (omgevingsvergunning,
// evenementenvergunning, omgevingsmelding, andere beschikking)` agrees with it on 99.61% of
// the corpus (61,611 of 61,853) and scores the identical precision 0.821 / recall 0.936 /
// F1 0.875 on the 695 hand-labelled rows. So a red/grey dot would dress a permit-type lookup
// up as a prediction.
//
// The SCORE is a different matter. Inside one doc_type, where the lookup is constant and
// carries no information at all, it separates hand-labelled noisy from quiet at AUC 0.911
// (omgevingsvergunning, 202 vs 36), 0.965 (evenementenvergunning) and 1.000 (andere
// vergunning). That ranking is the part the embedding actually earned, so the map shows the
// ranking: one hue, pale for quiet and deep for loud, with the stops at measured quantiles
// of the corpus. The ramp and the reasoning behind its stops live in
// constants/bekendmaking.js — do not inline a different one here.
const NOISE_COLOR = [
  'case',
  ['<', ['get', 'noise_p'], 0], NOISE_UNSCORED_COLOR,
  ['interpolate', ['linear'], ['get', 'noise_p'], ...NOISE_RAMP.flat()],
];

// The second channel. Unscored dots are hollow — white fill, grey ring — so "no prediction"
// is not merely a paler step of "quiet". Colour alone would make the two a matter of degree,
// and they are a difference in kind: one is an answer and the other is the absence of one.
const NOISE_STROKE = [
  'case', ['<', ['get', 'noise_p'], 0], NOISE_UNSCORED_COLOR, '#ffffff',
];
const NOISE_FILL = [
  'case', ['<', ['get', 'noise_p'], 0], '#ffffff', NOISE_COLOR,
];

// Unscored dots are also held back in opacity, so they recede behind the scored ones
// instead of competing with them for attention.
const NOISE_OPACITY = [
  'case', ['<', ['get', 'noise_p'], 0], 0.45, 1,
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
      noise: it.noise || 'unscored',
      // `noise_score` is null for an unscored row, and MapLibre coerces null in a feature
      // property to 0 inside an expression — which would read as "confidently quiet". It is
      // only ever displayed, never used in a paint expression, and the popup tests for the
      // empty string rather than for a falsy number.
      noise_score: it.noise_score == null ? '' : String(it.noise_score),
      // The numeric the paint ramp reads. -1, not null: MapLibre coerces a null feature
      // property to 0 inside an expression, and 0 on this ramp is "confidently quiet" --
      // the exact confusion between "no prediction" and "a negative prediction" that the
      // `unscored` bucket exists to prevent. A sentinel below the domain is caught by the
      // `case` above before interpolation ever sees it.
      noise_p: it.noise_score == null ? -1 : it.noise_score,
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
        <span style="width:8px;height:8px;border-radius:50%;background:${noiseColor(p.noise_score === '' ? null : Number(p.noise_score))}"></span>
        <strong style="font-size:11px;letter-spacing:.3px;text-transform:uppercase;color:#86868b">
          ${esc(noiseBand(p.noise_score === '' ? null : Number(p.noise_score)))}${p.noise_score ? ` · ${esc(p.noise_score)}` : ''}
        </strong>
        <span style="margin-left:auto;font-size:11px;color:#86868b">${esc(p.published_at)}</span>
      </div>
      <div style="color:#1d1d1f;margin-bottom:6px">${esc(p.title)}</div>
      <div style="font-size:11px;color:#86868b">
        ${esc(stageLabel(p.stage))} (from title) · ${esc(p.activiteit || '—')}${p.gemeente ? ` · ${esc(p.gemeente)}` : ''}
      </div>
      <div style="font-size:10px;color:#aeaeb2;margin-top:4px">
        Model prediction, not a published field. The score ranks against other
        publications; the noisy/quiet cut itself is close to a permit-type lookup.
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
          'circle-color': NOISE_COLOR,
          'circle-opacity': ['*', 0.2, NOISE_OPACITY],
        },
      });
      map.addLayer({
        id: LAYER,
        type: 'circle',
        source: SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2, 14, 4.5, 17, 7],
          'circle-color': NOISE_FILL,
          'circle-opacity': NOISE_OPACITY,
          'circle-stroke-width': 1.2,
          'circle-stroke-color': NOISE_STROKE,
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
