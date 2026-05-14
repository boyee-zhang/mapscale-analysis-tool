import { useEffect, useState } from 'react';

const TYPE_COLORS = { Studio: '#f97316', '1-bed': '#2980b9', '2-bed+': '#27ae60' };

const panelStyle = {
  position: 'absolute',
  top: 20,
  right: 20,
  width: 320,
  maxHeight: 'calc(100vh - 40px)',
  overflowY: 'auto',
  background: 'rgba(255,255,255,0.92)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  borderRadius: '20px',
  padding: '20px',
  boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
  border: '1px solid rgba(255,255,255,0.4)',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  zIndex: 200,
};

const closeBtnStyle = {
  position: 'absolute',
  top: 14,
  right: 14,
  background: 'rgba(0,0,0,0.07)',
  border: 'none',
  borderRadius: '50%',
  width: 28,
  height: 28,
  cursor: 'pointer',
  fontSize: 14,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: '#555',
};

const skeletonStyle = {
  background: 'linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)',
  backgroundSize: '200% 100%',
  animation: 'shimmer 1.4s infinite',
  borderRadius: 8,
};

function StatRow({ label, count, price, area, distance, color }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span style={{ fontWeight: 700, fontSize: 13, color: '#1d1d1f' }}>{label}</span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: '#86868b' }}>{count} listings</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
        {[
          { label: 'Avg rent', value: price ? `€${Math.round(price)}/mo` : '—' },
          { label: 'Avg area', value: area ? `${area}m²` : '—' },
          { label: 'To station', value: distance ? `${distance}km` : '—' },
        ].map(({ label: l, value }) => (
          <div key={l} style={{ background: 'rgba(0,0,0,0.03)', borderRadius: 10, padding: '8px 6px', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: '#86868b', marginBottom: 2 }}>{l}</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#1d1d1f' }}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function CityReportPanel({ data, loading, onClose }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (loading || data) setVisible(true);
  }, [loading, data]);

  if (!visible) return null;

  return (
    <>
      <style>{`
        @keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
      `}</style>
      <div style={panelStyle}>
        <button style={closeBtnStyle} onClick={() => { setVisible(false); onClose(); }}>✕</button>

        {loading ? (
          <>
            <div style={{ ...skeletonStyle, height: 20, width: '60%', marginBottom: 16 }} />
            <div style={{ ...skeletonStyle, height: 14, width: '40%', marginBottom: 20 }} />
            {[1, 2, 3].map(i => (
              <div key={i} style={{ marginBottom: 16 }}>
                <div style={{ ...skeletonStyle, height: 14, width: '50%', marginBottom: 8 }} />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
                  {[1, 2, 3].map(j => <div key={j} style={{ ...skeletonStyle, height: 48, borderRadius: 10 }} />)}
                </div>
              </div>
            ))}
            <div style={{ ...skeletonStyle, height: 80, marginTop: 16 }} />
          </>
        ) : data ? (
          <>
            <h3 style={{ margin: '0 0 4px', fontSize: 18, fontWeight: 700, color: '#1d1d1f', paddingRight: 32 }}>
              {data.stats.city}
            </h3>
            <p style={{ margin: '0 0 18px', fontSize: 12, color: '#86868b' }}>
              {data.stats.total} active listings
            </p>

            <div style={{ borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 16, marginBottom: 16 }}>
              {Object.entries(data.stats.by_type).map(([type, s]) => (
                <StatRow
                  key={type}
                  label={type}
                  count={s.count}
                  price={s.avg_price}
                  area={s.avg_area_m2}
                  distance={s.avg_distance_km}
                  color={TYPE_COLORS[type] || '#888'}
                />
              ))}
            </div>

            <div style={{
              background: 'rgba(41,128,185,0.06)',
              borderRadius: 12,
              padding: '12px 14px',
              borderLeft: '3px solid #2980b9',
            }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#2980b9', marginBottom: 6, letterSpacing: '0.04em' }}>
                AI MARKET OVERVIEW
              </div>
              {data.summary.split('\n\n').map((para, i) => {
                const html = para.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
                return (
                  <p key={i} style={{ margin: i === 0 ? 0 : '8px 0 0', fontSize: 12, lineHeight: 1.6, color: '#2c3e50' }}
                    dangerouslySetInnerHTML={{ __html: html }} />
                );
              })}
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}
