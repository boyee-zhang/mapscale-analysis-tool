import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';

// 注意这里必须有 export 关键字
export const useMap = (containerRef) => {
  const mapInstance = useRef(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    if (!containerRef.current || mapInstance.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [4.936, 52.338],
      zoom: 12
    });

    map.on('load', () => {
      mapInstance.current = map;
      setIsReady(true);
    });

    return () => map.remove();
  }, []);

  return { map: mapInstance.current, isReady };
};