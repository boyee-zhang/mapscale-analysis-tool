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
const PoiMarker = ({ poi, mapInstance }) => {
  const { tags, lon, lat } = poi;

useEffect(() => {
    if (!mapInstance) return;

    // --- 1. 营业状态判定逻辑 ---
    const getStatus = (ohString) => {
      if (!ohString) return { label: 'Unknown', color: '#999', text: '数据缺失' };
      
      try {
        // 实例化解析器
        const oh = new ConfigurableOpeningHours(ohString);
        // oh.isOpen() 会根据当前系统时间判断
        const isOpen = oh.isOpen(); 
        
        return isOpen 
          ? { label: 'Open Now', color: '#27ae60', text: '正在营业' } 
          : { label: 'Closed', color: '#e74c3c', text: '已关门' };
      } catch (e) {
        // 如果 OSM 字符串格式太奇怪无法解析，回退到未知状态
        console.warn("Hours parse error for:", tags.name, e);
        return { label: 'Info', color: '#3498db', text: '查看详情' };
      }
    };

    const status = getStatus(tags.opening_hours);
    const isShop = !!tags.shop;
    const typeColor = isShop ? '#FFD700' : '#FF4500';

    // --- 2. 创建 Marker 元素 (保持原有圆点逻辑) ---
    const container = document.createElement('div');
    container.style.width = '14px';
    container.style.height = '14px';
    container.style.cursor = 'pointer';

    const dot = document.createElement('div');
    dot.style.width = '14px';
    dot.style.height = '14px';
    dot.style.backgroundColor = typeColor;
    dot.style.borderRadius = '50%';
    dot.style.border = '2px solid white';
    dot.style.transition = 'transform 0.2s';
    container.appendChild(dot);

    // --- 3. 增强版 Popup HTML ---
    const popupHtml = `
      <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 8px; min-width: 160px;">
        <div style="margin-bottom: 4px;">
          <b style="color: #333; font-size: 14px;">${tags.name || 'Unnamed'}</b>
        </div>
        
        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 8px;">
          <span style="font-size: 10px; background: ${status.color}; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold; text-transform: uppercase;">
            ${status.label}
          </span>
          <span style="font-size: 11px; color: #666;">
            ${isShop ? '🛒 Shop' : '🏋️ Gym'}
          </span>
        </div>

        ${tags.opening_hours ? `
          <div style="font-size: 11px; color: #444; background: #f8f9fa; padding: 6px; border-radius: 4px; border-left: 3px solid ${status.color}; margin-bottom: 8px;">
            <div style="color: #888; font-size: 9px; margin-bottom: 2px;">SCHEDULE</div>
            ${tags.opening_hours}
          </div>
        ` : ''}

        ${tags['addr:street'] ? `
          <div style="font-size: 10px; color: #aaa; padding-top: 4px; border-top: 1px solid #eee;">
            📍 ${tags['addr:street']} ${tags['addr:housenumber'] || ''}
          </div>
        ` : ''}
      </div>
    `;

    const popup = new maplibregl.Popup({ 
      offset: 15, 
      closeButton: false, 
      closeOnClick: false 
    }).setHTML(popupHtml);

    // --- 4. 初始化与事件 (保持原有逻辑) ---
    const marker = new maplibregl.Marker({ element: container })
      .setLngLat([lon, lat])
      .addTo(mapInstance);

    const onEnter = () => {
      dot.style.transform = 'scale(1.8)';
      popup.setLngLat([lon, lat]).addTo(mapInstance);
    };

    const onLeave = () => {
      dot.style.transform = 'scale(1.0)';
      popup.remove();
    };

    container.addEventListener('mouseenter', onEnter);
    container.addEventListener('mouseleave', onLeave);

    return () => {
      container.removeEventListener('mouseenter', onEnter);
      container.removeEventListener('mouseleave', onLeave);
      marker.remove();
      popup.remove();
    };
  }, [poi, mapInstance, lon, lat, tags]);

  return null;
};

const CenterMarker = ({ loc, mapInstance }) => {
  useEffect(() => {
    if (!loc) return;

    // Use a simple purple marker
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

const Map = () => {
  const mapContainer = useRef(null);
  const map = useRef(null);

  // STATE: The "Single Source of Truth" for your UI.
  const [poiData, setPoiData] = useState([]);
  const [isoData, setIsoData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [centerLoc, setCenterLoc] = useState(null); 
  const [minutes, setMinutes] = useState(10);
  const [mode, setMode] = useState('walking'); // 'walking', 'driving', 'cycling'

const MODE_STYLE = {
  walking: { color: '#6a0dad', speed: 80 },  
  cycling: { color: '#f1c40f', speed: 250 }, 
  driving: { color: '#3498db', speed: 600 }  
};
  useEffect(() => {
    if (map.current) return;
    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [4.936, 52.338],
      zoom: 13
    });

    map.current.on('click', (e) => {
      fetchData(e.lngLat.lng, e.lngLat.lat);
    });
  }, []);

  
  useEffect(() => {
    if (centerLoc) {
      console.log("Parameters changed, auto-fetching for:", mode, minutes);
      fetchData(centerLoc.lng, centerLoc.lat);
    }
  }, [minutes, mode]); 

  const fetchData = async (lng, lat) => {
  setCenterLoc({ lng, lat });
  setLoading(true);
  try {
    const [isoResp, poiResp] = await Promise.all([
      axios.get(`http://localhost:8000/api/isochrone`, {
        params: { lng, lat, minutes: minutes, profile: mode }
      }),
      axios.get(`http://localhost:8000/api/pois`, {
        params: { lng, lat, minutes: minutes, profile: mode }
      })
    ]);

    setIsoData(isoResp.data);
    setPoiData(poiResp.data.elements || []);
  } catch (err) {
    console.error("Fetch error:", err);
  } finally {
    setLoading(false);
  }
};  

  const handleAddressSearch = async (address) => {
    try {
      // Nominatim API
      const response = await axios.get(`https://nominatim.openstreetmap.org/search`, {
        params: {
          q: address,
          format: 'json',
          limit: 1,
          countrycodes: 'nl' // 限制在荷兰境内，提高 Diemen Zuid 的匹配度
        }
      });

      if (response.data && response.data.length > 0) {
        const { lon, lat, display_name } = response.data[0];
        const newLng = parseFloat(lon);
        const newLat = parseFloat(lat);

        map.current.flyTo({
          center: [newLng, newLat],
          zoom: 15, 
          essential: true,
          speed: 1.5
        });
        fetchData(newLng, newLat);
        
        console.log(`Located: ${display_name}`);
      } else {
        alert("Address not found.");
      }
    } catch (err) {
      console.error("Geocoding failed:", err);
    }
  };

  const filteredPois = useMemo(() => {
  if (!isoData || poiData.length === 0) return [];

  try {
    const poly = isoData.features[0].geometry;


    return poiData.filter(poi => {
      const pt = turf.point([poi.lon, poi.lat]);
      return turf.booleanPointInPolygon(pt, poly);
    });
  } catch (e) {
    console.error("Filtering error:", e);
    return [];
  }
}, [poiData, isoData]);

  useEffect(() => {
  if (!map.current || !isoData) return;
  
  const source = map.current.getSource('iso');
  const targetColor = MODE_STYLE[mode].color;

  if (source) {
    source.setData(isoData);
    map.current.setPaintProperty('iso-layer', 'fill-color', targetColor);
  } else {
    map.current.addSource('iso', { type: 'geojson', data: isoData });
    map.current.addLayer({
      id: 'iso-layer',
      type: 'fill',
      source: 'iso',
      paint: { 
        'fill-color': targetColor, 
        'fill-opacity': 0.15, 
        'fill-outline-color': '#ffffff' 
      }
    });
  }
}, [isoData, mode]); 

  return (
    <div style={{ position: 'relative', width: '100%', height: '100vh', backgroundColor: '#eee' }}>
      <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />

      <SearchBar onSearch={handleAddressSearch} />
      {map.current && centerLoc && (
        <CenterMarker loc={centerLoc} mapInstance={map.current} />
      )}

      {/* RENDER LIST: Declaratively map data to components */}
      {map.current && filteredPois.map(poi => (
      <PoiMarker key={poi.id} poi={poi} mapInstance={map.current} />
    ))}

      {/* SIDEBAR: Reactive UI that updates automatically when state changes */}
      <div style={{
        position: 'absolute', top: '20px', left: '20px',
        backgroundColor: 'white', padding: '20px', borderRadius: '12px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.1)', zIndex: 10, width: '220px'
      }}>
        <h3 style={{ margin: '0 0 10px 0' }}>MapScale Analysis</h3>
        <AnalysisPanel 
          minutes={minutes} 
          setMinutes={setMinutes} 
          mode={mode} 
          setMode={setMode} 
          modeStyle={MODE_STYLE} 
        />

        {loading ? (
          <p style={{ color: '#bf252dff', fontSize: '18px' }}>Analyzing area...</p>
        ) : (
          <div>
            <p style={{ fontSize: '14px', color: '#444' }}>
              Found <b>{filteredPois.length}</b> locations within {minutes}min {mode}.
            </p>
            <ul style={{ padding: 0, listStyle: 'none', maxHeight: '200px', overflowY: 'auto' }}>
              {poiData.slice(0, 5).map(poi => (
                <li key={poi.id} style={{ fontSize: '11px', marginBottom: '5px', color: '#666' }}>
                  • {poi.tags.name || 'Unnamed store'}
                </li>
              ))}
              {poiData.length > 5 && <li style={{ fontSize: '10px', color: '#999' }}>And {poiData.length - 5} more...</li>}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};

export default Map;