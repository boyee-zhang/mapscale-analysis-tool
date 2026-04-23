import React, { useRef, useState, useMemo, useEffect } from 'react';
import * as turf from '@turf/turf';
import { useMap } from '../hooks/useMap';
import { api } from '../api.js';
import PoiMarker from './PoiMarker';
import CenterMarker from './CenterMarker.jsx';
import AnalysisPanel from './AnalysisPanel.jsx';
import LegendPanel from './LegendPanel';
import AnalysisResultPanel from './AnalysisResultPanel';
import SearchBar from './SearchBar';

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

  // 1. 统一状态
  const [data, setData] = useState({ iso: null, pois: [], loading: false });
  const [params, setParams] = useState({
    center: { lng: 4.936, lat: 52.338 },
    minutes: 10,
    mode: 'walking'
  });
  const [, setHoveredRoute] = useState(null);

  // 2. 响应式数据同步
  useEffect(() => {
    if (!isReady) return;

    const fetchData = async () => {
      setData(d => ({ ...d, loading: true }));
      try {
        const [iso, pois] = await Promise.all([
          api.fetchIsochrone(params.center.lng, params.center.lat, params.minutes, params.mode),
          api.fetchPOIs(params.center.lng, params.center.lat, params.minutes, params.mode)
        ]);
        setData({ iso, pois, loading: false });
      } catch (err) {
        console.error("Fetch Error:", err);
        setData(d => ({ ...d, loading: false }));
      }
    };

    fetchData();
  }, [isReady, params.center, params.minutes, params.mode]);

  // 3. 点击地图更新中心点
  useEffect(() => {
    if (!isReady) return;
    const handleClick = (e) => {
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

  // 6. 基于等时线过滤 POI
  const filteredPois = useMemo(() => {
    if (!data.iso || !data.pois.length) return [];
    const polygon = data.iso.features[0];
    return data.pois.filter(p => {
      if (!p.lon || !p.lat) return false;
      return turf.booleanPointInPolygon(turf.point([p.lon, p.lat]), polygon);
    });
  }, [data.iso, data.pois]);

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
        poiCount={filteredPois.length}
        loading={data.loading}
        onAIAnalysis={handleAIAnalysis}
        showTraffic={showTraffic}
        onToggleTraffic={() => setShowTraffic(v => !v)}
      />

      <AnalysisResultPanel
        data={analysisData}
        onClose={() => setAnalysisData(null)}
      />

      {/* 图例组件 */}
      <LegendPanel />

      {/* 渲染 Markers */}
      {isReady && <CenterMarker map={map} pos={params.center} />}

      {isReady && filteredPois.map(poi => (
        <PoiMarker
          key={poi.id}
          poi={poi}
          map={map}
          centerLoc={params.center}
          mode={params.mode}
          setHoveredRoute={setHoveredRoute}
        />
      ))}
    </div>
  );
};

export default MapContainer;
