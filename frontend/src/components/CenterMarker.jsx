import { useEffect } from 'react';
import maplibregl from 'maplibre-gl';

const CenterMarker = ({ map, pos }) => {
  useEffect(() => {
    if (!map || !pos) return;

    // 创建中心点 DOM
    const el = document.createElement('div');
    el.className = 'center-marker';
    
    Object.assign(el.style, {
      width: '24px',
      height: '24px',
      backgroundColor: '#3bc223ff', // 苹果蓝，很醒目
      borderRadius: '50%',
      border: '4px solid white',
      boxShadow: '0 0 10px rgba(0,0,0,0.3)',
      cursor: 'pointer',
      zIndex: '1000'
    });

    // 也可以给它加一个小标签提示“Your Location”
    el.innerHTML = '<div style="position:absolute; top:-25px; left:-15px; background:black; color:white; padding:2px 6px; border-radius:4px; font-size:10px; white-space:nowrap;">Current Location</div>';

    const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat([pos.lng, pos.lat])
      .addTo(map);

    return () => marker.remove();
  }, [map, pos]); // 当点击地图改变 pos 时，旧的移除，新的创建

  return null;
};

export default CenterMarker;