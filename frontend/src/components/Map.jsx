import React, { useRef, useEffect } from 'react';
import { useMap } from '../hooks/useMap';
import PoiMarker from './PoiMarker';

const Map = ({ pois, isoData, params, onMapClick, loading }) => {
  const mapRef = useRef(null);
  const { map, isReady } = useMap(mapRef);

  // 处理等时线渲染
  useEffect(() => {
    if (!isReady || !isoData) return;
    const source = map.getSource('iso-source');
    if (source) {
      source.setData(isoData);
    } else {
      map.addSource('iso-source', { type: 'geojson', data: isoData });
      map.addLayer({
        id: 'iso-layer', type: 'fill', source: 'iso-source',
        paint: { 'fill-color': '#6a0dad', 'fill-opacity': 0.15 }
      });
    }
  }, [isReady, isoData]);

  // 处理地图点击事件
  useEffect(() => {
    if (!isReady) return;
    const onClick = (e) => onMapClick(e.lngLat.lng, e.lngLat.lat);
    map.on('click', onClick);
    return () => map.off('click', onClick);
  }, [isReady, onMapClick]);

  return (
    <div style={{ width: '100%', height: '100vh', position: 'relative' }}>
      <div ref={mapRef} style={{ width: '100%', height: '100%' }} />
      
      {/* 渲染 Markers */}
      {/* {isReady && pois.map(poi => (
        <PoiMarker 
          key={poi.id} 
          poi={poi} 
          mapInstance={map} 
          centerLoc={params.center} 
          travelMode={params.mode}
        />
      ))}
       */}
      {loading && <div style={{position:'absolute', bottom: 20, right: 20}}>Processing...</div>}
    </div>
  );
};

export default Map;