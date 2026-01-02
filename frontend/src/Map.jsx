import maplibregl from 'maplibre-gl';
import axios from 'axios';
import 'maplibre-gl/dist/maplibre-gl.css';
import * as turf from '@turf/turf';
import React, { useEffect, useRef, useState, useMemo } from 'react';
import ConfigurableOpeningHours from 'opening_hours';
/**
 * POI Marker Component
 * Think of this as a "View Controller" for a single point.
 * It manages its own lifecycle (Create on mount, Destroy on unmount).
 */
const PoiMarker = ({ poi, mapInstance, centerLoc, travelMode, setHoveredRoute }) => {
  const { tags, lon, lat } = poi;
  const lastRequestRef = useRef(null);

  useEffect(() => {
    if (!mapInstance) return;

    const getStatus = (ohString) => {
      if (!ohString) return { label: 'Unknown', color: '#999' };
      try {
        const oh = new ConfigurableOpeningHours(ohString);
        return oh.isOpen() ? { label: 'Open Now', color: '#27ae60' } : { label: 'Closed', color: '#e74c3c' };
      } catch (e) {
        return { label: 'Info', color: '#3498db' };
      }
    };

    const status = getStatus(tags.opening_hours);
    const isShop = !!tags.shop;
    const typeColor = isShop ? '#FFD700' : '#FF4500';

    const container = document.createElement('div');
    container.style.width = '14px';
    container.style.height = '14px';
    container.style.cursor = 'pointer';
    container.style.zIndex = '5';

    const dot = document.createElement('div');
    dot.style.width = '14px';
    dot.style.height = '14px';
    dot.style.backgroundColor = typeColor;
    dot.style.borderRadius = '50%';
    dot.style.border = '2px solid white';
    dot.style.transition = 'all 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
    container.appendChild(dot);

    const popupHtml = `
      <div style="font-family: sans-serif; padding: 8px; min-width: 150px;">
        <b style="font-size: 13px;">${tags.name || 'Unnamed'}</b>
        <div style="font-size: 10px; color: ${status.color}; font-weight: bold; margin-top: 4px;">
          ${status.label} • ${isShop ? '🛒 Shop' : '🏋️ Gym'}
        </div>
      </div>
    `;

    const popup = new maplibregl.Popup({ offset: 15, closeButton: false, closeOnClick: false }).setHTML(popupHtml);
    const marker = new maplibregl.Marker({ element: container }).setLngLat([lon, lat]).addTo(mapInstance);

    const onEnter = async () => {
      dot.style.transform = 'scale(2.2)'; 
      dot.style.boxShadow = '0 0 10px rgba(0,0,0,0.3)';
      popup.setLngLat([lon, lat]).addTo(mapInstance);

      if (centerLoc) {
        const currentKey = `${lon},${lat}`;
        if (lastRequestRef.current === currentKey) return;

        try {
          const res = await axios.get('http://localhost:8000/api/directions', {
            params: {
              start_lng: centerLoc.lng,
              start_lat: centerLoc.lat,
              end_lng: lon,
              end_lat: lat,
              mode: travelMode
            }
          });
          setHoveredRoute(res.data);
          lastRequestRef.current = currentKey;
        } catch (err) {
          console.error("Path API error:", err.message);
        }
      }
    };

    const onLeave = () => {
      dot.style.transform = 'scale(1.0)';
      dot.style.boxShadow = 'none';
      popup.remove();
      setHoveredRoute(null);
      lastRequestRef.current = null;
    };

    container.addEventListener('mouseenter', onEnter);
    container.addEventListener('mouseleave', onLeave);

    return () => {
      container.removeEventListener('mouseenter', onEnter);
      container.removeEventListener('mouseleave', onLeave);
      marker.remove();
      popup.remove();
    };
  }, [poi, mapInstance, centerLoc, travelMode, setHoveredRoute]);

  return null;
};

const CenterMarker = ({ loc, mapInstance }) => {
  useEffect(() => {
    if (!loc || !mapInstance) return;
    const marker = new maplibregl.Marker({ color: '#6a0dad' })
      .setLngLat([loc.lng, loc.lat])
      .addTo(mapInstance);
    return () => marker.remove();
  }, [loc, mapInstance]);
  return null;
};

const AnalysisPanel = ({ minutes, setMinutes, mode, setMode, modeStyle }) => {
  return (
    <div style={{ 
      padding: '15px', 
      background: '#f8f9fa', 
      borderRadius: '8px', 
      marginBottom: '20px',
      border: '1px solid #e9ecef'
    }}>
      <h4 style={{ margin: '0 0 12px 0', fontSize: '14px', color: '#333' }}>Analysis Params</h4>
      
      {/* Travel Mode Select */}
      <div style={{ marginBottom: '15px' }}>
        <label style={{ display: 'block', fontSize: '11px', color: '#666', marginBottom: '5px' }}>Mode</label>
        <select 
          value={mode} 
          onChange={(e) => setMode(e.target.value)}
          style={{ 
            width: '100%', 
            padding: '6px', 
            borderRadius: '4px', 
            border: '1px solid #ddd',
            fontSize: '13px'
          }}
        >
          <option value="walking">🚶 Walking</option>
          <option value="cycling">🚲 Cycling</option>
          <option value="driving">🚗 Driving</option>
        </select>
      </div>

      {/* Time Range Slider */}
      <div style={{ marginBottom: '10px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
          <label style={{ fontSize: '11px', color: '#666' }}>Time Range</label>
          <span style={{ fontSize: '12px', fontWeight: 'bold', color: modeStyle[mode].color }}>
            {minutes} mins
          </span>
        </div>
        <input 
          type="range" 
          min="5" 
          max="30" 
          step="5"
          value={minutes} 
          onChange={(e) => setMinutes(parseInt(e.target.value))}
          style={{ 
            width: '100%', 
            cursor: 'pointer',
            accentColor: modeStyle[mode].color 
          }}
        />
      </div>
      
      <p style={{ fontSize: '10px', color: '#999', margin: '5px 0 0 0' }}>
        * Click map to re-calculate after changes
      </p>
    </div>
  );
};

const SearchBar = ({ onSearch }) => {
  const [query, setQuery] = useState('');

  const handleAction = () => {
    if (query.trim()) {
      onSearch(query);
    }
  };

  return (
    <div style={{
      position: 'absolute', 
      top: '20px', 
      right: '20px',
      zIndex: 10, 
      display: 'flex', 
      gap: '8px',
      alignItems: 'center'
    }}>
      <div style={{ position: 'relative' }}>
        <input 
          type="text" 
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search address (e.g. 1112XC)..."
          onKeyDown={(e) => e.key === 'Enter' && handleAction()}
          style={{
            padding: '12px 15px', 
            borderRadius: '12px', 
            border: 'none',
            outline: 'none',
            fontSize: '14px',
            boxShadow: '0 4px 15px rgba(0,0,0,0.15)', 
            width: '280px',
            transition: 'all 0.3s'
          }}
        />
        {query && (
          <button 
            onClick={() => setQuery('')}
            style={{
              position: 'absolute', right: '10px', top: '50%',
              transform: 'translateY(-50%)', border: 'none',
              background: 'none', color: '#ccc', cursor: 'pointer'
            }}
          >✕</button>
        )}
      </div>
      <button 
        onClick={handleAction}
        style={{
          padding: '12px 20px', 
          borderRadius: '12px', 
          border: 'none',
          backgroundColor: '#6a0dad', 
          color: 'white', 
          cursor: 'pointer',
          fontWeight: 'bold',
          boxShadow: '0 4px 15px rgba(106, 13, 173, 0.3)',
          transition: 'transform 0.2s'
        }}
        onMouseEnter={(e) => e.target.style.transform = 'scale(1.05)'}
        onMouseLeave={(e) => e.target.style.transform = 'scale(1)'}
      >
        Search
      </button>
    </div>
  );
};

const handleAddressSearch = async (address) => {
  try {
    const res = await axios.get(`https://nominatim.openstreetmap.org/search`, {
      params: { q: address, format: 'json', limit: 1, countrycodes: 'nl' }
    });
    if (res.data.length > 0) {
      const { lon, lat } = res.data[0];
      const newLng = parseFloat(lon);
      const newLat = parseFloat(lat);
      mapInstance.current.flyTo({ center: [newLng, newLat], zoom: 15 });
      fetchData(newLng, newLat);
    }
  } catch (err) { console.error(err); }
};

const Map = () => {
  const mapContainer = useRef(null);
  const mapInstance = useRef(null);

  const [poiData, setPoiData] = useState([]);
  const [isoData, setIsoData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [centerLoc, setCenterLoc] = useState(null);
  const [minutes, setMinutes] = useState(10);
  const [mode, setMode] = useState('walking');
  const [hoveredRoute, setHoveredRoute] = useState(null);

  const MODE_STYLE = {
    walking: { color: '#6a0dad' },
    cycling: { color: '#f1c40f' },
    driving: { color: '#3498db' }
  };

  const fetchData = async (lng, lat) => {
    setCenterLoc({ lng, lat });
    setLoading(true);
    
    try {
      const [isoResp, poiResp] = await Promise.all([
        axios.get(`http://localhost:8000/api/isochrone`, { 
          params: { lng, lat, minutes, profile: mode } 
        }),
        axios.get(`http://localhost:8000/api/pois`, { 
          params: { lng, lat, minutes, profile: mode } 
        })
      ]);

      setIsoData(isoResp.data);
      setPoiData(poiResp.data.elements || []);
    } catch (err) {
      console.error("Fetch Data Error:", err);
      setPoiData([]);
      setIsoData(null);
    } finally {
      setLoading(false);
    }
  };

  const handleAddressSearch = async (address) => {
    try {
      const res = await axios.get(`https://nominatim.openstreetmap.org/search`, {
        params: { q: address, format: 'json', limit: 1, countrycodes: 'nl' }
      });
      if (res.data.length > 0) {
        const { lon, lat } = res.data[0];
        const newLng = parseFloat(lon);
        const newLat = parseFloat(lat);
        
        mapInstance.current?.flyTo({ center: [newLng, newLat], zoom: 14 });
        
        fetchData(newLng, newLat);
      }
    } catch (err) {
      console.error("Geocoding Error:", err);
    }
  };

  useEffect(() => {
    if (mapInstance.current) return;
    
    mapInstance.current = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [4.936, 52.338],
      zoom: 12
    });

    mapInstance.current.on('load', () => {
      mapInstance.current.addSource('route-preview', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
      });
      mapInstance.current.addLayer({
        id: 'route-preview-layer',
        type: 'line',
        source: 'route-preview',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 
          'line-color': '#3b82f6', 
          'line-width': 4, 
          'line-dasharray': [2, 1] 
        }
      });
    });

    mapInstance.current.on('click', (e) => {
      fetchData(e.lngLat.lng, e.lngLat.lat);
    });
  }, []);

  const filteredPois = useMemo(() => {
    if (!isoData || !isoData.features || isoData.features.length === 0 || poiData.length === 0) {
      return [];
    }
    const polygon = isoData.features[0];
    return poiData.filter(poi => 
      turf.booleanPointInPolygon(turf.point([poi.lon, poi.lat]), polygon)
    );
  }, [poiData, isoData]);

  useEffect(() => {
    const map = mapInstance.current;
    if (!map || !isoData) return;

    const source = map.getSource('iso');
    if (source) {
      source.setData(isoData);
      map.setPaintProperty('iso-layer', 'fill-color', MODE_STYLE[mode].color);
    } else {
      map.addSource('iso', { type: 'geojson', data: isoData });
      map.addLayer({
        id: 'iso-layer',
        type: 'fill',
        source: 'iso',
        paint: {
          'fill-color': MODE_STYLE[mode].color,
          'fill-opacity': 0.15,
          'fill-outline-color': MODE_STYLE[mode].color
        }
      }, 'route-preview-layer'); 
    }
  }, [isoData, mode]);

  useEffect(() => {
    const source = mapInstance.current?.getSource('route-preview');
    if (source) {
      source.setData(hoveredRoute || { type: 'FeatureCollection', features: [] });
    }
  }, [hoveredRoute]);

  useEffect(() => {
    if (centerLoc) {
      fetchData(centerLoc.lng, centerLoc.lat);
    }
  }, [minutes, mode]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100vh', backgroundColor: '#eee' }}>
      {/* Map engine container */}
      <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
      
      {/* Geocoding search bar */}
      <SearchBar onSearch={handleAddressSearch} />
      
      {/* Active center location marker */}
      {centerLoc && <CenterMarker loc={centerLoc} mapInstance={mapInstance.current} />}
      
      {/* Render POIs within the isochrone boundary */}
      {mapInstance.current && filteredPois.slice(0, 40).map(poi => (
        <PoiMarker 
          key={poi.id} 
          poi={poi} 
          mapInstance={mapInstance.current}
          centerLoc={centerLoc} 
          travelMode={mode}     
          setHoveredRoute={setHoveredRoute} 
        />
      ))}

      {/* Floating analysis control panel */}
      <div style={{ 
        position: 'absolute', 
        top: '20px', 
        left: '20px', 
        backgroundColor: 'white', 
        padding: '20px', 
        borderRadius: '12px', 
        boxShadow: '0 4px 12px rgba(0,0,0,0.1)', 
        zIndex: 10, 
        width: '220px' 
      }}>
        <h3 style={{ marginTop: 0 }}>MapScale Analysis</h3>
        <AnalysisPanel 
          minutes={minutes} 
          setMinutes={setMinutes} 
          mode={mode} 
          setMode={setMode} 
          modeStyle={MODE_STYLE} 
        />
        {loading ? (
          <p style={{ color: '#6a0dad', fontWeight: 'bold' }}>Analyzing Area...</p>
        ) : (
          <p>Found <b>{filteredPois.length}</b> locations.</p>
        )}
      </div>
    </div>
  );
};

export default Map;