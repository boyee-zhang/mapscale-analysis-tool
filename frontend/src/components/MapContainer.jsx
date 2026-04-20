import React, { useRef, useState, useMemo, useEffect } from 'react';
import * as turf from '@turf/turf';
import { useMap } from '../hooks/useMap';
import { api } from '../api.js';
import PoiMarker from './PoiMarker';
import CenterMarker from './CenterMarker.jsx';
import AnalysisPanel from './AnalysisPanel.jsx';
import LegendPanel from './LegendPanel';
import AnalysisResultPanel from './AnalysisResultPanel'; 

const MapContainer = () => {
  const containerRef = useRef(null);
  const { map, isReady } = useMap(containerRef);
  const [analysisData, setAnalysisData] = useState(null);

  // 1. 统一状态
  const [data, setData] = useState({ iso: null, pois: [], loading: false });
  const [params, setParams] = useState({ 
    center: { lng: 4.936, lat: 52.338 }, 
    minutes: 10, 
    mode: 'walking' 
  });
  const [hoveredRoute, setHoveredRoute] = useState(null);

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

  // 4. Traffic incidents 图层 (WebGL circle layer，随 isochrone bbox 更新)
  useEffect(() => {
    if (!isReady || !data.iso) return;

    const bbox = data.iso.features[0]?.bbox;
    if (!bbox) return; // ORS isochrone 返回的 FeatureCollection 带有 bbox

    const [min_lng, min_lat, max_lng, max_lat] = bbox;

    api.fetchTrafficIncidents({ min_lng, min_lat, max_lng, max_lat })
      .then(geojson => {
        console.log('[MapContainer] incidents loaded', geojson.features.length);
        const source = map.getSource('incidents-source');
        if (source) {
          source.setData(geojson);
        } else {
          map.addSource('incidents-source', { type: 'geojson', data: geojson });
          map.addLayer({
            id: 'incidents-layer',
            type: 'circle',
            source: 'incidents-source',
            paint: {
              'circle-radius': 8,
              'circle-color': '#FF3B30',
              'circle-opacity': 0.85,
              'circle-stroke-width': 2,
              'circle-stroke-color': '#fff',
            }
          });
        }
      })
      .catch(err => console.error('[MapContainer] incidents fetch failed', err));
  }, [isReady, data.iso]);

  // 5. 等时线图层渲染 (Apple Style 修正版)
  useEffect(() => {
    if (!isReady || !data.iso) return;

    const sourceId = 'iso-source'; // 定义变量名
    const source = map.getSource(sourceId);

    if (source) {
      source.setData(data.iso);
    } else {
      // 必须先添加 Source
      map.addSource(sourceId, {
        type: 'geojson',
        data: data.iso
      });

      // 底层：填充层 (Apple 紫色薄雾感)
      map.addLayer({
        id: 'iso-layer',
        type: 'fill',
        source: sourceId,
        paint: {
          'fill-color': '#AF52DE',
          'fill-opacity': 0.12     
        }
      });

      // 上层：细致描边 (增加层次感)
      map.addLayer({
        id: 'iso-outline',
        type: 'line',
        source: sourceId,
        paint: {
          'line-color': '#cca5dfff',
          'line-width': 2,
          'line-opacity': 0.4
        }
      });
    }
  }, [isReady, data.iso]);

  // 6. 基于等时线过滤 POI
  const filteredPois = useMemo(() => {
    if (!data.iso || !data.pois.length) return [];
    const polygon = data.iso.features[0];
    return data.pois.filter(p => {
      // 容错处理：确保坐标存在
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
      
      {/* 业务控制面板 */}
      <AnalysisPanel 
        params={params} 
        setParams={setParams} 
        poiCount={filteredPois.length}
        loading={data.loading}
        onAIAnalysis={handleAIAnalysis}
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