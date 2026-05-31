import React, { useState } from 'react';
import { api } from '../api';

const TYPE_COLORS = { Studio: '#f97316', '1-bed': '#2980b9', '2-bed+': '#27ae60' };

function normalizeType(raw) {
  if (!raw) return '2-bed+';
  const s = raw.toLowerCase();
  if (s === 'studio') return 'Studio';
  if (s === '1') return '1-bed';
  return '2-bed+';
}

function formatDate(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
}

// ── Markdown renderer ─────────────────────────────────────────────────────────
// Handles **bold**, - bullet lists, and plain paragraphs.

function boldify(text) {
  return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function renderMarkdown(text, textStyle, listStyle, itemStyle) {
  if (!text) return null;
  const lines = text.split('\n');
  const result = [];
  let listItems = [];

  const flushList = (key) => {
    if (!listItems.length) return;
    result.push(
      <ul key={`ul-${key}`} style={listStyle}>
        {listItems.map((item, j) => (
          <li key={j} style={itemStyle} dangerouslySetInnerHTML={{ __html: boldify(item) }} />
        ))}
      </ul>
    );
    listItems = [];
  };

  lines.forEach((line, i) => {
    if (line.startsWith('- ')) {
      listItems.push(line.slice(2));
    } else {
      flushList(i);
      if (line.trim()) {
        result.push(
          <p key={i} style={textStyle} dangerouslySetInnerHTML={{ __html: boldify(line) }} />
        );
      }
    }
  });
  flushList('end');
  return result;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatRow({ label, count, price, area }) {
  const color = TYPE_COLORS[label] || '#888';
  return (
    <div style={statRowStyle}>
      <div style={statRowHeader}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span style={statLabelStyle}>{label}</span>
        <span style={statCountStyle}>{count} listings</span>
      </div>
      <div style={statGridStyle}>
        {[
          { label: 'Avg rent', value: price ? `€${Math.round(price)}/mo` : '—' },
          { label: 'Avg area', value: area  ? `${Math.round(area)}m²`     : '—' },
        ].map(({ label: l, value }) => (
          <div key={l} style={statCellStyle}>
            <div style={statCellLabelStyle}>{l}</div>
            <div style={statCellValueStyle}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ListingRow({ feature }) {
  const p = feature.properties;
  const type  = normalizeType(p.property_type);
  const color = TYPE_COLORS[type] || '#888';
  const date  = formatDate(p.available_from);
  const name  = p.name || p.city || '—';

  return (
    <a href={p.url} target="_blank" rel="noopener noreferrer" style={listingRowStyle}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0, marginTop: 4 }} />
      <div style={listingInfoStyle}>
        <div style={listingNameStyle} title={name}>{name.length > 34 ? name.slice(0, 32) + '…' : name}</div>
        <div style={listingMetaStyle}>
          <span>{p.price_display || '—'}</span>
          {p.area_m2 && <span>· {Math.round(p.area_m2)}m²</span>}
          {date && <span>· from {date}</span>}
        </div>
      </div>
      <span style={listingArrowStyle}>↗</span>
    </a>
  );
}

function CityCard({ report }) {
  return (
    <div style={cityCardStyle}>
      <div style={cityCardHeader}>
        <span style={cityNameStyle}>{report.stats.city}</span>
        <span style={cityTotalStyle}>{report.stats.total} active</span>
      </div>
      {Object.entries(report.stats.by_type).map(([type, s]) => (
        <StatRow key={type} label={type} count={s.count} price={s.avg_price} area={s.avg_area_m2} />
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function HousingChatPanel({ onResult, onClear }) {
  const [query, setQuery]                   = useState('');
  const [loading, setLoading]               = useState(false);
  const [summary, setSummary]               = useState(null);
  const [cityReports, setCityReports]       = useState([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [mapPreparing, setMapPreparing]     = useState(false);
  const [listings, setListings]             = useState([]);
  const [error, setError]                   = useState(null);

  const hasResults = summary !== null || cityReports.length > 0;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    setError(null);
    setSummary(null);
    setCityReports([]);
    setListings([]);
    setMapPreparing(false);
    try {
      const result = await api.chatHousing(query.trim());
      setSummary(result.summary);

      if (result.cities.length) {
        setMapPreparing(true);
        onResult(result.cities);

        setReportsLoading(true);
        const [reports, listingsGeo] = await Promise.all([
          Promise.all(result.cities.map(city => api.fetchCityReport(city).catch(() => null))),
          api.fetchHousingListings('h2s', result.cities, 3, 'future').catch(() => ({ features: [] })),
        ]);
        setCityReports(reports.filter(Boolean));
        setListings(listingsGeo.features ?? []);
        setReportsLoading(false);
        setMapPreparing(false);
      } else {
        onResult([]);
      }
    } catch {
      setError('Search failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setQuery('');
    setSummary(null);
    setCityReports([]);
    setListings([]);
    setReportsLoading(false);
    setMapPreparing(false);
    setError(null);
    onClear();
  };

  const panelStyle = hasResults ? panelExpanded : panelCollapsed;

  return (
    <div style={panelStyle}>
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }`}</style>
      <div style={headerStyle}>
        <span style={{ fontSize: '14px' }}>🏠</span>
        <span style={titleStyle}>Housing Search</span>
      </div>

      <form onSubmit={handleSubmit} style={formStyle}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="e.g. 我想找乌特勒支和海牙的房子…"
          style={inputStyle}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !query.trim()} style={btnStyle(loading)}>
          {loading ? '⏳' : '→'}
        </button>
      </form>

      {error && <p style={errorStyle}>{error}</p>}

      {hasResults && (
        <div style={resultsStyle}>
          {summary && (
            <div style={summaryBlockStyle}>
              {renderMarkdown(summary, summaryParaStyle, summaryUlStyle, summaryLiStyle)}
            </div>
          )}

          {mapPreparing && (
            <div style={mapHintStyle}>
              <span style={mapHintDotStyle} />
              地图可视化正在准备中…
            </div>
          )}

          {reportsLoading && (
            <div style={reportLoadingStyle}>正在加载城市数据…</div>
          )}

          {cityReports.map(report => (
            <CityCard key={report.stats.city} report={report} />
          ))}

          {listings.length > 0 && (
            <div style={listingsSectionStyle}>
              <div style={listingsHeaderStyle}>Available Listings ({listings.length})</div>
              <div style={listingsScrollStyle}>
                {listings.map((f, i) => (
                  <ListingRow key={f.properties?.id ?? i} feature={f} />
                ))}
              </div>
            </div>
          )}

          <button onClick={handleClear} style={clearStyle}>Clear</button>
        </div>
      )}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const panelBase = {
  position: 'absolute', top: 20, right: 20, zIndex: 100,
  background: 'rgba(255,255,255,0.92)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  borderRadius: '20px',
  padding: '14px 16px',
  boxShadow: '0 8px 32px rgba(0,0,0,0.1)',
  border: '1px solid rgba(255,255,255,0.4)',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  transition: 'width 0.25s ease',
};
const panelCollapsed = { ...panelBase, width: 260 };
const panelExpanded  = { ...panelBase, width: 320, maxHeight: 'calc(100vh - 120px)', overflowY: 'auto' };

const headerStyle = { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 };
const titleStyle  = { fontSize: '13px', fontWeight: 600, color: '#1d1d1f' };
const formStyle   = { display: 'flex', gap: 6 };

const inputStyle = {
  flex: 1, padding: '8px 10px', borderRadius: '12px',
  border: '1px solid rgba(0,0,0,0.12)', background: 'rgba(255,255,255,0.6)',
  fontSize: '12px', outline: 'none',
};

const btnStyle = (isLoading) => ({
  padding: '8px 12px', borderRadius: '12px', border: 'none',
  background: isLoading ? 'rgba(0,0,0,0.08)' : 'linear-gradient(135deg, #f97316, #8b5cf6)',
  color: isLoading ? '#999' : 'white',
  fontWeight: 700, fontSize: '14px',
  cursor: isLoading ? 'default' : 'pointer',
  transition: 'all 0.15s ease',
});

const errorStyle = { marginTop: 8, fontSize: '12px', color: '#FF3B30' };

const resultsStyle = {
  marginTop: 10, borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 10,
};

// Summary markdown styles
const summaryBlockStyle = { marginBottom: 10 };
const summaryParaStyle  = { margin: '0 0 6px', fontSize: '12px', color: '#1d1d1f', lineHeight: '1.65' };
const summaryUlStyle    = { margin: '2px 0 6px', paddingLeft: 16, listStyle: 'disc' };
const summaryLiStyle    = { fontSize: '12px', color: '#1d1d1f', lineHeight: '1.7', marginBottom: 2 };

// Map preparing hint
const mapHintStyle = {
  display: 'flex', alignItems: 'center', gap: 6,
  fontSize: '11px', color: '#2980b9', fontWeight: 500,
  background: 'rgba(41,128,185,0.07)', borderRadius: 8,
  padding: '6px 10px', marginBottom: 8,
};
const mapHintDotStyle = {
  width: 6, height: 6, borderRadius: '50%', background: '#2980b9',
  flexShrink: 0,
  animation: 'pulse 1.2s ease-in-out infinite',
};

const reportLoadingStyle = {
  textAlign: 'center', padding: '8px 0', fontSize: 12, color: '#86868b',
};

const clearStyle = {
  marginTop: 10, padding: '4px 10px', borderRadius: '8px',
  border: '1px solid rgba(0,0,0,0.1)', background: 'transparent',
  fontSize: '11px', color: '#86868b', cursor: 'pointer', display: 'block',
};

// City card styles
const cityCardStyle   = { marginBottom: 8, borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 12 };
const cityCardHeader  = { display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 10 };
const cityNameStyle   = { fontWeight: 700, fontSize: 14, color: '#1d1d1f' };
const cityTotalStyle  = { fontSize: 11, color: '#86868b' };

const statRowStyle    = { marginBottom: 10 };
const statRowHeader   = { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 };
const statLabelStyle  = { fontWeight: 700, fontSize: 12, color: '#1d1d1f' };
const statCountStyle  = { marginLeft: 'auto', fontSize: 11, color: '#86868b' };
const statGridStyle   = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 };
const statCellStyle   = { background: 'rgba(0,0,0,0.03)', borderRadius: 8, padding: '6px', textAlign: 'center' };
const statCellLabelStyle = { fontSize: 10, color: '#86868b', marginBottom: 1 };
const statCellValueStyle = { fontSize: 12, fontWeight: 600, color: '#1d1d1f' };

// Listings panel styles
const listingsSectionStyle = {
  marginTop: 12, borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 10,
};
const listingsHeaderStyle = {
  fontSize: 11, fontWeight: 600, color: '#86868b',
  letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 6,
};
const listingsScrollStyle = {
  maxHeight: 220, overflowY: 'auto',
  display: 'flex', flexDirection: 'column', gap: 2,
};
const listingRowStyle = {
  display: 'flex', alignItems: 'flex-start', gap: 8,
  padding: '8px 10px', borderRadius: 10,
  background: 'rgba(0,0,0,0.02)',
  textDecoration: 'none', color: 'inherit',
  cursor: 'pointer',
  transition: 'background 0.12s ease',
};
const listingInfoStyle = { flex: 1, minWidth: 0 };
const listingNameStyle = {
  fontSize: 12, fontWeight: 500, color: '#1d1d1f',
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  marginBottom: 2,
};
const listingMetaStyle = {
  display: 'flex', gap: 5, fontSize: 11, color: '#86868b', flexWrap: 'wrap',
};
const listingArrowStyle = {
  fontSize: 12, color: '#86868b', flexShrink: 0, marginTop: 2,
};

