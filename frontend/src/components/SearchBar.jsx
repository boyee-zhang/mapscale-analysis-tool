import React, { useState, useRef, useEffect } from 'react';
import { api } from '../api.js';

const SearchBar = ({ onSelect }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef(null);
  const containerRef = useRef(null);

  // close dropdown when clicking outside
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleChange = (e) => {
    const val = e.target.value;
    setQuery(val);

    clearTimeout(debounceRef.current);
    if (val.trim().length < 3) {
      setResults([]);
      setOpen(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchAddress(val);
        setResults(data);
        setOpen(data.length > 0);
        console.log('[SearchBar] results', data.length);
      } catch (err) {
        console.error('[SearchBar] search failed', err);
      } finally {
        setLoading(false);
      }
    }, 350);
  };

  const handleSelect = (item) => {
    setQuery(item.label);
    setOpen(false);
    setResults([]);
    onSelect({ lng: item.lng, lat: item.lat, label: item.label });
    console.log('[SearchBar] selected', item);
  };

  const triggerSearch = async () => {
    if (query.trim().length < 3) return;
    clearTimeout(debounceRef.current);
    setLoading(true);
    try {
      const data = await api.searchAddress(query.trim());
      setResults(data);
      setOpen(data.length > 0);
      if (data.length === 1) handleSelect(data[0]);
      console.log('[SearchBar] results', data.length);
    } catch (err) {
      console.error('[SearchBar] search failed', err);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') setOpen(false);
    if (e.key === 'Enter') triggerSearch();
  };

  return (
    <div ref={containerRef} style={wrapperStyle}>
      <div style={inputWrapStyle}>
        <span style={iconStyle}>
          {loading ? '⏳' : '🔍'}
        </span>
        <input
          type="text"
          placeholder="Search address…"
          value={query}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          style={inputStyle}
        />
        {query && (
          <button
            style={clearBtnStyle}
            onClick={() => { setQuery(''); setResults([]); setOpen(false); }}
          >
            ✕
          </button>
        )}
        <button style={searchBtnStyle} onClick={triggerSearch} disabled={loading}>
          Search
        </button>
      </div>

      {open && (
        <ul style={dropdownStyle}>
          {results.map((item, i) => (
            <li
              key={i}
              style={resultItemStyle}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,122,255,0.08)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              onMouseDown={() => handleSelect(item)}
            >
              <span style={resultIconStyle}>📍</span>
              <span style={resultLabelStyle}>{item.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const wrapperStyle = {
  position: 'absolute',
  top: 20,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 100,
  width: '420px',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

const inputWrapStyle = {
  display: 'flex',
  alignItems: 'center',
  backgroundColor: 'rgba(255,255,255,0.85)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  borderRadius: '16px',
  boxShadow: '0 4px 20px rgba(0,0,0,0.12)',
  border: '1px solid rgba(255,255,255,0.5)',
  padding: '0 12px',
  height: '46px',
};

const iconStyle = {
  fontSize: '15px',
  marginRight: '8px',
  flexShrink: 0,
};

const inputStyle = {
  flex: 1,
  border: 'none',
  background: 'transparent',
  outline: 'none',
  fontSize: '15px',
  color: '#1d1d1f',
  fontFamily: 'inherit',
};

const clearBtnStyle = {
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  color: '#86868b',
  fontSize: '13px',
  padding: '4px',
  flexShrink: 0,
};

const searchBtnStyle = {
  flexShrink: 0,
  marginLeft: '6px',
  padding: '6px 14px',
  borderRadius: '10px',
  border: 'none',
  background: '#007AFF',
  color: 'white',
  fontSize: '13px',
  fontWeight: '600',
  cursor: 'pointer',
  fontFamily: 'inherit',
};

const dropdownStyle = {
  listStyle: 'none',
  margin: '6px 0 0 0',
  padding: '6px',
  backgroundColor: 'rgba(255,255,255,0.90)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  borderRadius: '16px',
  boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
  border: '1px solid rgba(255,255,255,0.5)',
  maxHeight: '280px',
  overflowY: 'auto',
};

const resultItemStyle = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: '8px',
  padding: '10px 12px',
  borderRadius: '10px',
  cursor: 'pointer',
  background: 'transparent',
  transition: 'background 0.15s',
};

const resultIconStyle = {
  fontSize: '13px',
  flexShrink: 0,
  marginTop: '1px',
};

const resultLabelStyle = {
  fontSize: '13px',
  color: '#1d1d1f',
  lineHeight: '1.4',
};

export default SearchBar;
