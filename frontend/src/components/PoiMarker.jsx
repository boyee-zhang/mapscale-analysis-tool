import React, { useEffect } from 'react';
import maplibregl from 'maplibre-gl';
import { renderToStaticMarkup } from 'react-dom/server'; 
import { api } from '../api';
import PoiPopup from './PoiPopup'; 

const PoiMarker = ({ poi, map, centerLoc, mode, setHoveredRoute }) => {
  useEffect(() => {
    if (!map) return;

    // 用来存放延迟关闭的定时器
    let hideTimer = null;

    const { lon, lat, tags = {} } = poi;
    const isShop = !!tags.shop;

    const container = document.createElement('div');
    Object.assign(container.style, {
      width: '20px', height: '20px', display: 'flex',
      alignItems: 'center', justifyContent: 'center', cursor: 'pointer'
    });

    const dot = document.createElement('div');
    Object.assign(dot.style, {
      width: '14px', height: '14px',
      backgroundColor: isShop ? '#FFD700' : '#FF4500',
      borderRadius: '50%', border: '2px solid white',
      boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
      transition: 'transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275)'
    });
    container.appendChild(dot);

    const marker = new maplibregl.Marker({ element: container, anchor: 'center' })
      .setLngLat([Number(lon), Number(lat)])
      .addTo(map);

    const popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 15,
      maxWidth: '300px'
    });

    // 定义关闭逻辑
    const closePopup = () => {
      dot.style.transform = 'scale(1)';
      setHoveredRoute(null);
      popup.remove();
    };

    const onEnter = async () => {
      // 1. 如果还在倒计时准备关闭，立即取消关闭
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }

      dot.style.transform = 'scale(1.8)';
      
      const html = renderToStaticMarkup(<PoiPopup poi={poi} />);
      popup.setLngLat([Number(lon), Number(lat)]).setHTML(html).addTo(map);

      // 2. 关键：给气泡本身的 DOM 也加上监听，防止鼠标移入气泡时气泡消失
      const popupElement = popup.getElement();
      if (popupElement) {
        popupElement.addEventListener('mouseenter', () => {
          if (hideTimer) {
            clearTimeout(hideTimer);
            hideTimer = null;
          }
        });
        popupElement.addEventListener('mouseleave', onLeave);
      }

      if (centerLoc) {
        try {
          const route = await api.fetchDirections(centerLoc, { lng: lon, lat }, mode);
          setHoveredRoute(route);
        } catch (e) {
          console.error("Route fetching failed:", e);
        }
      }
    };
    
    const onLeave = () => {
      // 3. 设置延迟消失，给用户 300ms 的反应时间
      hideTimer = setTimeout(() => {
        closePopup();
      }, 300); 
    };

    container.addEventListener('mouseenter', onEnter);
    container.addEventListener('mouseleave', onLeave);

    return () => {
      if (hideTimer) clearTimeout(hideTimer);
      container.removeEventListener('mouseenter', onEnter);
      container.removeEventListener('mouseleave', onLeave);
      marker.remove();
      popup.remove();
    };
  }, [poi, map, centerLoc, mode, setHoveredRoute]);

  return null;
};

export default PoiMarker;