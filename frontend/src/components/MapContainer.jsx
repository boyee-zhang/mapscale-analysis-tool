import React, { useRef, useState, useEffect } from 'react';
import { useMap } from '../hooks/useMap';
import { api } from '../api.js';
import PoiMarker from './PoiMarker';
import CenterMarker from './CenterMarker.jsx';
import AnalysisPanel from './AnalysisPanel.jsx';
import LegendPanel from './LegendPanel';
import AnalysisResultPanel from './AnalysisResultPanel';
import SearchBar from './SearchBar';
import { DEFAULT_CATEGORIES } from '../constants/poiCategories';
import PermitLayer, { PERMIT_LAYER_IDS } from './PermitLayer';
import PermitPanel from './PermitPanel';
import { DEFAULT_WINDOW_MONTHS } from '../constants/bekendmaking';

// TomTom iconCategory → color (https://developer.tomtom.com/traffic-api/documentation/product-information/introduction)
const INCIDENT_COLORS = [
  'match', ['get', 'iconCategory'],
  1,  '#FF3B30',  // Accident
  3,  '#FF9500',  // Dangerous Conditions
  6,  '#FF3B30',  // Traffic Jam
  7,  '#FF9500',  // Lane Closed
  8,  '#FF3B30',  // Road Closed
  9,  '#FFCC00',  // Road Works
  11, '#007AFF',  // Flooding
  '#8E8E93'       // default (fog, wind, unknown)
];

const MapContainer = () => {
  const containerRef = useRef(null);
  const { map, isReady } = useMap(containerRef);
  const [analysisData, setAnalysisData] = useState(null);
  const [showTraffic, setShowTraffic] = useState(false);
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES);
  const [showPermits, setShowPermits] = useState(false);
  const [permitWindow, setPermitWindow] = useState(DEFAULT_WINDOW_MONTHS);
  // null = every bucket. Server-side rather than filtering `items` in the browser, because
  // `items` is capped at 500: filtering the page we were given would show "the noisy ones
  // among the 500 most recent", which is not the question and would disagree with the facets.
  const [permitNoise, setPermitNoise] = useState(null);
  // Semantic search results, when a query is active. Held here rather than in the panel so
  // the MAP shows what was searched for too — a ranked list the dots disagree with would be
  // two answers to one question.
  const [notice, setNotice] = useState({ q: '', items: null, loading: false, error: null });
  const [permits, setPermits] = useState({ data: null, loading: false });

  // 1. 统一状态
  const [data, setData] = useState({ iso: null, pois: [], loading: false });
  const [params, setParams] = useState({
    center: { lng: 4.936, lat: 52.338 },
    minutes: 10,
    mode: 'walking'
  });

  // 2. 响应式数据同步
  //
  // Two effects, not one, and the split is about the ORS quota rather than tidiness: the
  // free tier is 50 requests/day/endpoint, and changing a POI category does not move the
  // isochrone. Keeping categories out of the isochrone effect's deps stops a type toggle
  // from re-spending an isochrone call.
  useEffect(() => {
    if (!isReady) return;
    let cancelled = false;

    (async () => {
      setData(d => ({ ...d, loading: true }));
      try {
        const iso = await api.fetchIsochrone(params.center.lng, params.center.lat, params.minutes, params.mode);
        if (!cancelled) setData(d => ({ ...d, iso, loading: false }));
      } catch (err) {
        console.error('[MapContainer] isochrone failed', err);
        if (!cancelled) setData(d => ({ ...d, loading: false }));
      }
    })();

    return () => { cancelled = true; };
  }, [isReady, params.center, params.minutes, params.mode]);

  // POIs refetch on category change too. Debounced because the picker is a row of chips —
  // without it, selecting five types fires five requests, and /pois still costs an ORS
  // isochrone server-side (it clips to the real polygon, and the Upstash cache in front of
  // that call is currently dead, so nothing absorbs the repeats).
  useEffect(() => {
    if (!isReady) return;
    // An empty selection means "show nothing" — it is not a request for the backend's
    // default set. The distinction has to live here, because an omitted `categories` param
    // and an empty one are indistinguishable over HTTP. Returning early also skips a
    // request that would have cost a server-side ORS isochrone for zero markers.
    if (!categories || categories.length === 0) {
      setData(d => ({ ...d, pois: [], loading: false }));
      return;
    }
    let cancelled = false;

    const timer = setTimeout(async () => {
      setData(d => ({ ...d, loading: true }));
      try {
        const pois = await api.fetchPOIs(
          params.center.lng, params.center.lat, params.minutes, params.mode, categories
        );
        if (!cancelled) setData(d => ({ ...d, pois, loading: false }));
      } catch (err) {
        console.error('[MapContainer] pois failed', err);
        if (!cancelled) setData(d => ({ ...d, pois: [], loading: false }));
      }
    }, 400);

    return () => { cancelled = true; clearTimeout(timer); };
  }, [isReady, params.center, params.minutes, params.mode, categories]);

  // Building permits. A third effect rather than a branch in the POI one: it is toggled
  // off by default, it has its own time window, and it must not re-run when a POI type is
  // toggled. It does share the isochrone — /bekendmakingen clips to the same polygon
  // server-side, so the shape on screen and the shape queried cannot drift apart.
  useEffect(() => {
    if (!isReady) return;
    if (!showPermits) { setPermits({ data: null, loading: false }); return; }
    let cancelled = false;

    // The window is computed here rather than sent as a month count so the request is a
    // pair of absolute dates: the same URL means the same thing tomorrow.
    const to = new Date();
    const from = new Date(to);
    from.setMonth(from.getMonth() - permitWindow);
    const iso = (d) => d.toISOString().slice(0, 10);

    const timer = setTimeout(async () => {
      setPermits(p => ({ ...p, loading: true }));
      try {
        const data = await api.fetchBekendmakingen({
          // Sort by score ONLY while a noise filter is on. `items` is capped at 500 server
          // side, and an unfiltered isochrone matches ~2,500, so sorting the whole set by
          // score means the 500 returned are the 500 loudest and the map can never draw a
          // quiet dot — the map goes solid red again, for a new reason. Measured at
          // Nieuwmarkt: 2,481 matches, 803 noisy / 1,678 quiet, of which an unfiltered
          // score-sorted page shows 500 noisy and 0 quiet.
          //
          // So: no filter means "show me what is around here", which wants a date order and
          // a representative sample; picking Noisy means "show me the loudest", which wants
          // the ranking and for the cap to select on it. The caption follows `data.sort` and
          // says which of the two the list is.
          noise: permitNoise, sort: permitNoise ? 'noise' : 'recent',
          lng: params.center.lng, lat: params.center.lat,
          minutes: params.minutes, profile: params.mode,
          // No doc_type filter. This used to send `buildingPermits: true`, which pinned the
          // panel to omgevingsvergunning + bouwen — and that is precisely the class the noise
          // model flags at 99.95%, so every dot was red and the Quiet filter always returned
          // zero. MEASURED at one coordinate: with the filter, 476 matches and 476 noisy;
          // without it, 2,547 matches split 834 noisy / 1,713 quiet. The filter, not the
          // model, was what made the verdict carry no information.
          dateFrom: iso(from), dateTo: iso(to),
        });
        if (!cancelled) setPermits({ data, loading: false });
      } catch (err) {
        console.error('[MapContainer] permits failed', err);
        if (!cancelled) setPermits({ data: null, loading: false });
      }
    }, 300);

    return () => { cancelled = true; clearTimeout(timer); };
  }, [isReady, showPermits, permitWindow, permitNoise,
      params.center, params.minutes, params.mode]);

  // 3. 点击地图更新中心点
  useEffect(() => {
    if (!isReady) return;
    const handleClick = (e) => {
      // A click on a permit dot opens its popup and must not also move the centre, which
      // would re-run the isochrone and unmount the popup. MapLibre delivers the same click
      // to the layer handler and to this one, so the layer has to be excluded here —
      // stopPropagation inside the layer handler does not reach this listener.
      const present = PERMIT_LAYER_IDS.filter(id => map.getLayer(id));
      if (present.length && map.queryRenderedFeatures(e.point, { layers: present }).length) return;
      const { lng, lat } = e.lngLat;
      setParams(p => ({ ...p, center: { lng, lat } }));
    };
    map.on('click', handleClick);
    return () => map.off('click', handleClick);
  }, [isReady]);

  // 4. 等时线图层渲染
  useEffect(() => {
    if (!isReady || !data.iso) return;

    const sourceId = 'iso-source';
    const source = map.getSource(sourceId);

    if (source) {
      source.setData(data.iso);
    } else {
      map.addSource(sourceId, { type: 'geojson', data: data.iso });
      map.addLayer({
        id: 'iso-layer',
        type: 'fill',
        source: sourceId,
        paint: { 'fill-color': '#AF52DE', 'fill-opacity': 0.12 }
      });
      map.addLayer({
        id: 'iso-outline',
        type: 'line',
        source: sourceId,
        paint: { 'line-color': '#cca5dfff', 'line-width': 2, 'line-opacity': 0.4 }
      });
    }
  }, [isReady, data.iso]);

  // 5. Traffic 图层 — 受按钮控制，showTraffic 变化时加载/移除
  useEffect(() => {
    if (!isReady || !data.iso) return;

    const removeLayers = () => {
      ['incidents-layer', 'flow-tile-layer'].forEach(id => {
        if (map.getLayer(id)) map.removeLayer(id);
      });
      ['incidents-source', 'flow-tile-source'].forEach(id => {
        if (map.getSource(id)) map.removeSource(id);
      });
    };

    if (!showTraffic) {
      removeLayers();
      return;
    }

    const bbox = data.iso.bbox;
    if (!bbox) return;
    const [min_lng, min_lat, max_lng, max_lat] = bbox;

    const loadTraffic = async () => {
      try {
        const [{ flowTileUrl }, incidents] = await Promise.all([
          api.fetchTrafficTileUrl(),
          api.fetchTrafficIncidents({ min_lng, min_lat, max_lng, max_lat }),
        ]);
        console.log('[MapContainer] traffic loaded — incidents:', incidents.features.length);

        // Flow tile layer (raster — all roads colored green/yellow/red)
        if (!map.getSource('flow-tile-source')) {
          map.addSource('flow-tile-source', {
            type: 'raster',
            tiles: [flowTileUrl],
            tileSize: 256,
          });
          map.addLayer({
            id: 'flow-tile-layer',
            type: 'raster',
            source: 'flow-tile-source',
            paint: { 'raster-opacity': 0.75 },
          }, 'iso-layer'); // insert below isochrone fill so it doesn't cover POIs
        }

        // Incidents as colored LineStrings (accidents, road works, closures)
        if (!map.getSource('incidents-source')) {
          map.addSource('incidents-source', { type: 'geojson', data: incidents });
          map.addLayer({
            id: 'incidents-layer',
            type: 'line',
            source: 'incidents-source',
            paint: {
              'line-color': INCIDENT_COLORS,
              'line-width': 4,
              'line-opacity': 0.9,
            },
          });
        } else {
          map.getSource('incidents-source').setData(incidents);
        }

      } catch (err) {
        console.error('[MapContainer] traffic load failed', err);
      }
    };

    loadTraffic();
  }, [isReady, showTraffic, data.iso]);

  const handleAIAnalysis = async () => {
    try {
      console.log('[MapContainer] fetching region for', params.center);
      const { name, regionCode } = await api.fetchRegionCode(
        params.center.lng,
        params.center.lat
      );
      console.log('[MapContainer] region resolved:', name, regionCode);
      const result = await api.analyzeArea(name, regionCode);
      setAnalysisData(result);
    } catch (err) {
      console.error("AI Analysis Failed", err);
      alert("Analysis failed. Please try again.");
    }
  };

  return (
    <div style={{ width: '100%', height: '100vh', position: 'relative', overflow: 'hidden' }}>
      {/* 地图舞台 */}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {/* 地址搜索栏 */}
      <SearchBar
        onSelect={({ lng, lat }) => {
          setParams(p => ({ ...p, center: { lng, lat } }));
          map?.flyTo({ center: [lng, lat], zoom: 15, duration: 1200 });
        }}
      />

      {/* 业务控制面板 */}
      <AnalysisPanel
        params={params}
        setParams={setParams}
        poiCount={data.pois.length}
        loading={data.loading}
        onAIAnalysis={handleAIAnalysis}
        showTraffic={showTraffic}
        onToggleTraffic={() => setShowTraffic(v => !v)}
        categories={categories}
        onSetCategories={setCategories}
        showPermits={showPermits}
        onTogglePermits={() => setShowPermits(v => !v)}
      />

      <AnalysisResultPanel
        data={analysisData}
        onClose={() => setAnalysisData(null)}
      />

      {/* 图例组件 */}
      <LegendPanel categories={categories} />

      {/* 建设许可：地图图层 + 按月统计面板 */}
      <PermitLayer
        map={map}
        isReady={isReady}
        // Only the results above the relevance floor go on the MAP. The weak tail is
        // reachable in the panel behind an explicit disclosure, but a dot is an assertion
        // that something is there — padding the map is worse than padding a list, because a
        // reader cannot see a similarity score on a 3px circle.
        // `strong == null` means the backend could not measure the per-query baseline, so the
        // split is UNKNOWN rather than empty -- show everything in that case, since hiding
        // results because a diagnostic failed is the worse of the two errors.
        items={notice.items
          ? (notice.strong == null ? notice.items : notice.items.slice(0, notice.strong))
          : permits.data?.items}
        visible={showPermits}
      />
      {showPermits && (
        <PermitPanel
          data={permits.data}
          loading={permits.loading}
          windowMonths={permitWindow}
          notice={notice}
          onSearchNotices={async (q) => {
            if (!q) { setNotice({ q: '', items: null, strong: 0, loading: false, error: null }); return; }
            setNotice(n => ({ ...n, q, loading: true, error: null }));
            try {
              const to = new Date();
              const from = new Date(to);
              from.setMonth(from.getMonth() - permitWindow);
              const iso = (d) => d.toISOString().slice(0, 10);
              const r = await api.searchNotices({
                q, lng: params.center.lng, lat: params.center.lat,
                minutes: params.minutes, profile: params.mode,
                dateFrom: iso(from), dateTo: iso(to), limit: 30,
              });
              setNotice({ q, items: r.items, strong: r.strong, loading: false, error: null });
            } catch (e) {
              // `unavailable` is carried through from a 503 so the panel can say the encoder
              // is missing instead of showing "no results", which would be a different fact.
              setNotice({ q, items: null, strong: 0, loading: false,
                          error: e.unavailable ? e.message : 'Search failed' });
            }
          }}
          noiseFilter={permitNoise}
          onSetNoiseFilter={setPermitNoise}
          onSetWindow={setPermitWindow}
          onClose={() => setShowPermits(false)}
        />
      )}

      {/* 渲染 Markers */}
      {isReady && <CenterMarker map={map} pos={params.center} />}

      {isReady && data.pois.map(poi => (
        <PoiMarker
          // Not poi.id alone: a node and a way can share an id, and the backend now
          // returns both. Overpass only ever returned nodes, which hid the collision.
          key={`${poi.type}/${poi.id}`}
          poi={poi}
          map={map}
        />
      ))}
    </div>
  );
};

export default MapContainer;
