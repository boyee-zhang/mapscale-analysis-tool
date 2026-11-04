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
    
  /**
   * @param {number} lng
   * @param {number} lat
   * @param {number} minutes
   * @param {'walking'|'cycling'|'driving'} profile
   * @param {string[]} [categories]  poi_category keys; omitted = backend's legacy default.
   *   Only honoured on the Postgres path — the Overpass fallback ignores it.
   * @returns {Promise<Array<{ type, id, lon, lat, category?, tags }>>}
   */
  fetchPOIs: (lng, lat, minutes, profile, categories) => {
    // Built by hand because axios serialises an array as `categories[]=a&categories[]=b`
    // (verified, axios 1.13.2), while FastAPI's `list[str] = Query(...)` only reads the
    // bracket-less repeated form. Passing the array straight through silently fell back to
    // the backend's default three categories instead of erroring.
    const params = new URLSearchParams({ lng, lat, minutes, profile });
    // An empty array appends nothing, so the request looks identical to one that never
    // mentioned categories — and the backend treats an omitted param as "use the default
    // three". That is why callers must not reach here with an empty selection; MapContainer
    // returns before fetching instead. (This comment previously described that fallback as
    // intended behaviour; it was the bug.)
    (categories || []).forEach(c => params.append('categories', c));
    return client.get('/api/main/pois', { params }).then(res => res.data.elements || []);
  },
    
  /**
   * Official government announcements whose geometry meets the same isochrone /pois uses.
   *
   * @param {object} q
   * @param {number} q.lng
   * @param {number} q.lat
   * @param {number} q.minutes
   * @param {'walking'|'cycling'|'driving'} q.profile
   * @param {string} q.dateFrom  ISO date, inclusive
   * @param {string} q.dateTo    ISO date, inclusive
   * @param {boolean} [q.buildingPermits]  shorthand for omgevingsvergunning + bouwen
   * @returns {Promise<{
   *   shape: 'isochrone'|'circle'|'unavailable'|'none',
   *   window: { from: string, to: string },
   *   total: number, truncated: boolean,
   *   months: Array<{ month: string, count: number }>,
   *   by_type: Array<{ doc_type: string, count: number }>,
   *   by_activiteit: Array<{ activiteit: string, count: number }>,
   *   by_stage: Array<{ stage: string, count: number }>,
   *   items: Array<{ identifier, title, doc_type, published_at, activiteit, stage,
   *                  gemeente, url, lon, lat }>
   * }>}
   *
   * `total` counts every match; `items` is capped by the backend at 500 and `truncated`
   * says whether that happened. The month histogram is computed over all matches, so a
   * truncated list does not under-report a month — do not re-derive it from `items`.
   */
  fetchBekendmakingen: async ({ lng, lat, minutes, profile, dateFrom, dateTo, buildingPermits,
                               noise, sort }) => {
    // Hand-built for the same reason as fetchPOIs: axios would send `doc_type[]=`, which
    // FastAPI's repeated-param reader ignores. No array params are sent today, but the
    // shorthand flag is here instead precisely so callers are not tempted to pass one.
    const params = new URLSearchParams({ lng, lat, minutes, profile });
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (buildingPermits) params.append('building_permits', 'true');
    // Repeated param, not `noise[]=` — same reason as the comment above. `noise` is an array
    // of buckets (`noisy` / `quiet` / `unscored`); omitting it means every bucket.
    (noise || []).forEach(n => params.append('noise', n));
    // `sort=noise` is not cosmetic: `items` is capped at 500 server-side, so a date order
    // fills the cap with the newest publications and the loudest ones may never be returned.
    if (sort) params.append('sort', sort);
    try {
      const res = await client.get('/api/main/bekendmakingen', { params });
      console.log('[api] fetchBekendmakingen', {
        shape: res.data.shape, total: res.data.total, truncated: res.data.truncated,
        sort: res.data.sort,
        byNoise: Object.fromEntries((res.data.by_noise || []).map(b => [b.noise, b.count])),
        byEventDate: res.data.by_event_date,
      });
      return res.data;
    } catch (err) {
      console.error('[api] fetchBekendmakingen failed', err);
      throw err;
    }
  },

  /**
   * Semantic search over publications: ranked by meaning, not by wording.
   *
   * Separate from `searchAddress`, which resolves a place name to a coordinate. Sharing a
   * method would make one look like a mode of the other.
   *
   * Throws on 503 with the server's reason attached. That is deliberate: the encoder is an
   * optional dependency, and "search is unavailable" must reach the user as itself rather
   * than as an empty result list.
   *
   * @param {{ q: string, lng?: number, lat?: number, minutes?: number, profile?: string,
   *           dateFrom?: string, dateTo?: string, limit?: number }} opts
   * @returns {Promise<{ shape: string, total: number, items: object[] }>}
   */
  searchNotices: async ({ q, lng, lat, minutes, profile, dateFrom, dateTo, limit }) => {
    const params = new URLSearchParams({ q });
    if (lng != null && lat != null) {
      params.append('lng', lng);
      params.append('lat', lat);
      if (minutes) params.append('minutes', minutes);
      if (profile) params.append('profile', profile);
    }
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (limit) params.append('limit', limit);
    try {
      const res = await client.get('/api/main/search/notices', { params });
      console.log('[api] searchNotices', {
        q, shape: res.data.shape, total: res.data.total,
        top: res.data.items?.[0]?.similarity,
      });
      return res.data;
    } catch (err) {
      const detail = err.response?.data?.detail;
      console.error('[api] searchNotices failed', detail || err.message);
      const e = new Error(detail || 'Search failed');
      e.unavailable = err.response?.status === 503;
      throw e;
    }
  },

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

  /**
   * @param {string} query  free-text address, e.g. "Jan Wolkerslaan 699"
   * @returns {Promise<Array<{ label: string, lng: number, lat: number }>>}
   */
  searchAddress: async (query) => {
    try {
      const res = await client.get('/api/main/search', { params: { q: query } });
      console.log('[api] searchAddress', { query, count: res.data.length });
      return res.data;
    } catch (err) {
      console.error('[api] searchAddress failed', err);
      throw err;
    }
  },

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