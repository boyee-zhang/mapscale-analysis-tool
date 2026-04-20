import axios from 'axios';

// 此时 baseURL 指向前端自己，Vite 会通过前缀自动分发流量
const client = axios.create({
  baseURL: '', // 留空，默认访问当前域名（localhost:5173）
  timeout: 100000
});

export const api = {
  // 走代理的 /api/main 路径 -> 最终去 8000 端口
  fetchIsochrone: (lng, lat, minutes, profile) => 
    client.get('/api/main/isochrone', { params: { lng, lat, minutes, profile } }).then(res => res.data),
    
  fetchPOIs: (lng, lat, minutes, profile) => 
    client.get('/api/main/pois', { params: { lng, lat, minutes, profile } }).then(res => res.data.elements || []),
    
  /**
   * @param {{ lng: number, lat: number }} start
   * @param {{ lng: number, lat: number }} end
   * @param {'walking'|'cycling'|'driving'} mode
   * @returns {Promise<object>} ORS GeoJSON route
   */
  fetchDirections: async (start, end, mode) => {
    try {
      const res = await client.get('/api/main/directions', {
        params: {
          start_lng: start.lng,
          start_lat: start.lat,
          end_lng: end.lng,
          end_lat: end.lat,
          mode
        },
        timeout: 15000
      });
      return res.data;
    } catch (err) {
      console.error('[api] fetchDirections failed', err);
      throw err;
    }
  },

  /**
   * @param {number} lng
   * @param {number} lat
   * @returns {Promise<{ name: string, regionCode: string }>}
   */
  fetchRegionCode: async (lng, lat) => {
    try {
      const res = await client.get('/api/main/region', { params: { lng, lat } });
      console.log('[api] fetchRegionCode', res.data);
      return res.data;
    } catch (err) {
      console.error('[api] fetchRegionCode failed', err);
      throw err;
    }
  },

  /**
   * @param {number} lng
   * @param {number} lat
   * @returns {Promise<{ currentSpeed: number, freeFlowSpeed: number, congestionFactor: number, confidence: number }>}
   */
  fetchTrafficFlow: (lng, lat) =>
    client.get('/api/main/traffic/flow', { params: { lng, lat } }).then(res => res.data),

  /**
   * @param {{ min_lng: number, min_lat: number, max_lng: number, max_lat: number }} bbox
   * @returns {Promise<GeoJSON.FeatureCollection>}
   */
  fetchTrafficIncidents: (bbox) =>
    client.get('/api/main/traffic/incidents', { params: bbox }).then(res => res.data),

  /** @returns {Promise<{ flowTileUrl: string }>} */
  fetchTrafficTileUrl: () =>
    client.get('/api/main/traffic/tile-url').then(res => res.data),

  // 走代理的 /api/ai 路径 -> 最终去 3000 端口
  analyzeArea: async (city, regionCode) => {
    try {
      const res = await client.post('/api/ai/analyze', { city, regionCode });
      const payload = res.data;
      const version = payload?.schema_version;
      if (!version || !['1.0', '1.1'].includes(version)) {
        console.warn('[api] analyzeArea: unexpected schema_version:', version, '— rendering may be incomplete');
      }
      console.log('[api] analyzeArea response', { city, regionCode, schema_version: version });
      return payload;
    } catch (err) {
      console.error('[api] analyzeArea failed', err);
      throw err;
    }
  }
};