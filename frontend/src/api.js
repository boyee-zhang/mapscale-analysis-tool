import axios from 'axios';

const client = axios.create({
  baseURL: import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000',
  timeout: 100000
});

const PROFILE_MAP = {
  'walking': 'foot-walking',
  'cycling': 'cycling-regular',
  'driving-car': 'driving-car' // 保持一致
};

export const api = {
  fetchIsochrone: (lng, lat, minutes, profile) => 
    client.get('/api/isochrone', { params: { lng, lat, minutes, profile } }).then(res => res.data),
    
  fetchPOIs: (lng, lat, minutes, profile) => 
    client.get('/api/pois', { params: { lng, lat, minutes, profile } }).then(res => res.data.elements || []),
    
  fetchDirections: (start, end, mode) => 
    client.get('/api/directions', { 
      params: { start_lng: start.lng, start_lat: start.lat, end_lng: end.lng, end_lat: end.lat, mode } 
    }).then(res => res.data)
};