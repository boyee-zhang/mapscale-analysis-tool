import React from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';


// --- Helpers ---
const outlookColor = {
  'Bullish':            { bg: '#f0fff4', text: '#34C759' },
  'Cautiously Bullish': { bg: '#fffbf0', text: '#FF9500' },
  'Neutral':            { bg: '#f5f5f7', text: '#86868b' },
  'Bearish':            { bg: '#fff0f0', text: '#FF3B30' },
};

const confidenceLabel = { low: '⚠ Low', medium: '~ Medium', high: '✓ High' };

// Per-metric: does a value ABOVE national avg mean good (green)?
// Safety Index   → higher = safer         → true
// Commute km/min → higher = better reach  → true
// Education %    → higher = better        → true
const HIGHER_IS_BETTER = { Safety: true, Commute: true, Education: true };

const BenchmarkCard = ({ label, value, bench }) => {
  const vsNational  = bench?.vs_national || null;
  const rating      = bench?.rating      || null;
  const nationalAvg = bench?.national_avg != null ? bench.national_avg : null;
  const unit        = bench?.unit        || '';
  const isAbove     = vsNational?.toLowerCase().includes('above');
  const isBelow     = vsNational?.toLowerCase().includes('below');
  const higherGood  = HIGHER_IS_BETTER[label] ?? true;
  const vsColor     = isAbove
    ? (higherGood ? '#34C759' : '#FF3B30')
    : isBelow
    ? (higherGood ? '#FF3B30' : '#34C759')
    : '#86868b';

  return (
    <div style={metricCard}>
      <label style={labelStyle}>{label}</label>
      <div style={metricValue}>{value}{typeof value === 'number' && unit ? ` ${unit.split(' ')[0]}` : ''}</div>
      {nationalAvg != null && (
        <div style={{ fontSize: '9px', color: '#86868b', marginTop: '2px' }}>
          Nat. avg {nationalAvg}
        </div>
      )}
      {vsNational && (
        <div style={{ fontSize: '9px', fontWeight: '600', color: vsColor, marginTop: '2px', lineHeight: '1.2' }}>
          {vsNational}
        </div>
      )}
      {rating && (
        <div style={{ fontSize: '8px', color: '#86868b', marginTop: '3px', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
          {rating}
        </div>
      )}
    </div>
  );
};

const HorizonCard = ({ label, horizon }) => {
  if (!horizon) return null;
  const isPositive = horizon.price_change_pct >= 0;
  return (
    <div style={horizonCardStyle}>
      <span style={horizonLabelStyle}>{label}</span>
      <div style={{ ...horizonPctStyle, color: isPositive ? '#34C759' : '#FF3B30' }}>
        {isPositive ? '▲' : '▼'} {Math.abs(horizon.price_change_pct)}%
      </div>
      <div style={horizonPriceStyle}>
        €{(horizon.price_target_eur || 0).toLocaleString()}
      </div>
      <p style={horizonRationaleStyle}>{horizon.rationale}</p>
    </div>
  );
};

// --- Main Component ---
const AnalysisResultPanel = ({ data: responseData, onClose }) => {
  if (!responseData) return null;

  // v1.1 schema:
  //   responseData.data          → analysis scores, prediction
  //   responseData.raw_features  → raw time-series and per-dimension stats
  const payload     = responseData.data         || {};
  const raw         = responseData.raw_features || {};
  const request     = responseData.request      || {};
  const generatedAt = responseData.generated_at ? new Date(responseData.generated_at).toLocaleString() : null;
  // data_sources lives inside data (not a separate meta node)
  const dataSources = payload.data_sources || [];
  const displayCity = request.city || raw.population?.summary?.region_name || 'Unknown';

  // Raw time-series live in raw_features
  const housingTrend  = raw.housing?.trend || [];
  const housingSignal = raw.housing?.ai_signal || '';

  // Benchmark data from dimensional_analysis
  const dim             = payload.dimensional_analysis || {};
  const safetyBench     = dim.safety?.benchmark     || null;
  const commuteBench    = dim.connectivity?.benchmark || null;
  const educationBench  = dim.population?.benchmark  || null;

  // Fallback to raw_features if benchmark not yet present
  const safetyVal    = safetyBench?.value    ?? raw.safety?.safety_index    ?? '—';
  const commuteVal   = commuteBench?.value   ?? raw.traffic?.summary?.commute_efficiency ?? '—';
  const educationVal = educationBench?.value ?? raw.population?.social_status?.high_education_rate ?? '—';

  // Analysis & scores live in data
  const prediction     = payload.prediction || {};
  const horizon        = prediction.horizon || {};
  const overallOutlook = prediction.overall_outlook || 'Neutral';
  const outlookStyle   = outlookColor[overallOutlook] || outlookColor['Neutral'];

  return (
    <div style={panelStyle} className="fade-in">

      {/* Header */}
      <div style={headerStyle}>
        <div>
          <h2 style={{ margin: 0, fontSize: '20px' }}>✨ {displayCity}</h2>
          <span style={{ fontSize: '12px', color: '#86868b' }}>
            {housingSignal} · {request.region_code}
          </span>
        </div>
        <button onClick={onClose} style={closeButtonStyle}>✕</button>
      </div>

      <div style={contentStyle}>

        {/* 1. Housing Price Trend */}
        <section style={cardStyle}>
          <h4 style={sectionTitleStyle}>Housing Price Trend</h4>
          <div style={{ width: '100%', height: 180, marginTop: '10px' }}>
            <ResponsiveContainer>
              <LineChart data={housingTrend}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                <XAxis dataKey="period" fontSize={10} tickLine={false} axisLine={false} />
                <YAxis hide domain={['auto', 'auto']} />
                <Tooltip
                  contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
                  formatter={(val) => [`€${val.toLocaleString()}`, 'Price']}
                />
                <Line
                  type="monotone" dataKey="price" stroke="#007AFF"
                  strokeWidth={3} dot={{ r: 4, fill: '#007AFF' }}
                  activeDot={{ r: 6, strokeWidth: 0 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>

        {/* 2. Key Metrics with National Benchmark */}
        <div style={metricsGrid}>
          <BenchmarkCard label="Safety"    value={safetyVal}    bench={safetyBench} />
          <BenchmarkCard label="Commute"   value={commuteVal}   bench={commuteBench} />
          <BenchmarkCard label="Education" value={educationVal} bench={educationBench} />
        </div>

        {/* 3. AI Recommendation */}
        <section style={{ ...cardStyle, background: 'linear-gradient(135deg, #f5f7ff 0%, #ffffff 100%)' }}>
          <h4 style={sectionTitleStyle}>AI Recommendation</h4>
          <p style={recommendationStyle}>
            {payload.investment_grade ?? '—'} · Score {payload.investment_score ?? '—'}
          </p>
          <p style={detailStyle}>{payload.executive_summary ?? '—'}</p>
        </section>

        {/* 4. Strengths / Risks */}
        <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
          <div style={{ ...statusColumn, background: '#f6fff8' }}>
            <span style={{ fontSize: '10px', color: '#34C759', fontWeight: 'bold' }}>STRENGTHS</span>
            <ul style={minListStyle}>
              {(prediction.key_drivers || []).slice(0, 2).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </div>
          <div style={{ ...statusColumn, background: '#fff6f6' }}>
            <span style={{ fontSize: '10px', color: '#FF3B30', fontWeight: 'bold' }}>CONCERNS</span>
            <ul style={minListStyle}>
              {(prediction.key_risks || []).slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        </div>

        {/* 5. Price Prediction */}
        <section style={cardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h4 style={{ ...sectionTitleStyle, margin: 0 }}>Price Prediction</h4>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <span style={{ fontSize: '10px', color: '#86868b' }}>
                {confidenceLabel[prediction.confidence] || '—'} confidence
              </span>
              <span style={{
                fontSize: '11px', fontWeight: '600', padding: '2px 8px',
                borderRadius: '20px', background: outlookStyle.bg, color: outlookStyle.text
              }}>
                {overallOutlook}
              </span>
            </div>
          </div>

          {/* Horizon cards: 1yr / 3yr / 5yr */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px', marginBottom: '16px' }}>
            <HorizonCard label="1 Year"  horizon={horizon['1yr']} />
            <HorizonCard label="3 Years" horizon={horizon['3yr']} />
            <HorizonCard label="5 Years" horizon={horizon['5yr']} />
          </div>

          {/* Key Drivers */}
          {(prediction.key_drivers || []).length > 0 && (
            <div style={{ marginBottom: '10px' }}>
              <span style={{ ...labelStyle, display: 'block', marginBottom: '6px' }}>Key Drivers</span>
              <ul style={minListStyle}>
                {prediction.key_drivers.slice(0, 3).map((d, i) => (
                  <li key={i} style={{ color: '#34C759' }}>
                    <span style={{ color: '#3A3A3C' }}>{d}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Key Risks */}
          {(prediction.key_risks || []).length > 0 && (
            <div>
              <span style={{ ...labelStyle, display: 'block', marginBottom: '6px' }}>Key Risks</span>
              <ul style={minListStyle}>
                {prediction.key_risks.slice(0, 3).map((r, i) => (
                  <li key={i} style={{ color: '#FF3B30' }}>
                    <span style={{ color: '#3A3A3C' }}>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        {/* 6. Metrics Glossary */}
        <div style={glossaryStyle}>
          <span style={sourcesHeaderStyle}>HOW TO READ THESE METRICS</span>
          <div style={glossaryGridStyle}>
            <div style={glossaryItemStyle}>
              <span style={glossaryTermStyle}>Safety Index <span style={{ color: '#34C759' }}>↑ higher = safer</span></span>
              <span style={glossaryDescStyle}>
                Score from 0–100 representing neighborhood safety. 100 = zero crime; 0 = extremely high crime.
                Green = above Dutch national avg (≈ 37); red = below.
              </span>
            </div>
            <div style={glossaryItemStyle}>
              <span style={glossaryTermStyle}>Commute (km/min) <span style={{ color: '#34C759' }}>↑ higher = better reach</span></span>
              <span style={glossaryDescStyle}>
                How many km you cover per minute of travel. Higher means better transport connectivity.
                Green = above Dutch national avg (≈ 19.5 km/min); red = below.
              </span>
            </div>
            <div style={glossaryItemStyle}>
              <span style={glossaryTermStyle}>Education (%) <span style={{ color: '#34C759' }}>↑ higher = more skilled</span></span>
              <span style={glossaryDescStyle}>
                Share of residents holding an HBO or university (WO) degree.
                Green = above Dutch national avg (≈ 32%); red = below.
              </span>
            </div>
          </div>
        </div>

        {/* 7. Data Sources & Attribution */}
        <div style={sourcesFooterStyle}>
          <span style={sourcesHeaderStyle}>DATA SOURCES</span>

          {/* CBS table badges */}
          <div style={sourcesBadgeRowStyle}>
            {dataSources.map((src) => (
              <a
                key={src.table_id}
                href={src.url}
                target="_blank"
                rel="noopener noreferrer"
                style={sourceBadgeStyle}
                title={src.description}
              >
                <span style={sourceBadgeCodeStyle}>{src.table_id}</span>
                <span style={sourceBadgeLabelStyle}>{src.name}</span>
              </a>
            ))}
          </div>

          {/* AI model attribution + timestamp */}
          <div style={attributionRowStyle}>
            <span style={attributionTextStyle}>
              Analysis by <strong>DeepSeek</strong>
            </span>
            {generatedAt && (
              <span style={attributionTextStyle}>{generatedAt}</span>
            )}
          </div>
        </div>

      </div>
    </div>
  );
};

// --- Glossary Styles ---
const glossaryStyle     = { background: '#f5f5f7', borderRadius: '16px', padding: '14px 16px', marginBottom: '16px' };
const glossaryGridStyle = { display: 'flex', flexDirection: 'column', gap: '10px' };
const glossaryItemStyle = { display: 'flex', flexDirection: 'column', gap: '2px' };
const glossaryTermStyle = { fontSize: '11px', fontWeight: '600', color: '#1d1d1f' };
const glossaryDescStyle = { fontSize: '10px', color: '#86868b', lineHeight: '1.5' };

// --- Sources Footer Styles ---
const sourcesFooterStyle    = { borderTop: '1px solid rgba(0,0,0,0.05)', paddingTop: '16px', marginBottom: '8px' };
const sourcesHeaderStyle    = { fontSize: '9px', fontWeight: '600', color: '#86868b', letterSpacing: '0.06em', textTransform: 'uppercase', display: 'block', marginBottom: '10px' };
const sourcesBadgeRowStyle  = { display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' };
const sourceBadgeStyle      = { background: '#f0f4ff', borderRadius: '8px', padding: '4px 8px', display: 'flex', flexDirection: 'column', gap: '1px', textDecoration: 'none', cursor: 'pointer' };
const sourceBadgeCodeStyle  = { fontSize: '9px', fontWeight: '700', color: '#007AFF', letterSpacing: '0.02em' };
const sourceBadgeLabelStyle = { fontSize: '9px', color: '#86868b' };
const attributionRowStyle   = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '4px' };
const attributionTextStyle  = { fontSize: '10px', color: '#86868b' };

// --- Horizon Card Styles ---
const horizonCardStyle = {
  background: '#f5f5f7', borderRadius: '12px', padding: '10px 8px',
  display: 'flex', flexDirection: 'column', gap: '3px'
};
const horizonLabelStyle  = { fontSize: '9px', color: '#86868b', fontWeight: '600', textTransform: 'uppercase', letterSpacing: '0.04em' };
const horizonPctStyle    = { fontSize: '16px', fontWeight: '700' };
const horizonPriceStyle  = { fontSize: '11px', color: '#1d1d1f', fontWeight: '600' };
const horizonRationaleStyle = { fontSize: '10px', color: '#86868b', margin: 0, lineHeight: '1.3' };

// --- Shared Styles ---
const panelStyle = {
  position: 'absolute', right: 20, top: 20, bottom: 20, width: '380px',
  backgroundColor: 'rgba(255, 255, 255, 0.85)', backdropFilter: 'blur(25px)',
  borderRadius: '24px', boxShadow: '0 10px 40px rgba(0,0,0,0.15)',
  display: 'flex', flexDirection: 'column', zIndex: 1000,
  overflow: 'hidden', border: '1px solid rgba(255,255,255,0.5)'
};
const headerStyle       = { padding: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(0,0,0,0.05)' };
const contentStyle      = { padding: '20px', overflowY: 'auto', flex: 1 };
const cardStyle         = { backgroundColor: 'white', borderRadius: '16px', padding: '16px', marginBottom: '16px', boxShadow: '0 2px 8px rgba(0,0,0,0.04)' };
const sectionTitleStyle = { margin: '0 0 10px 0', fontSize: '10px', color: '#86868b', textTransform: 'uppercase', letterSpacing: '0.05em' };
const metricsGrid       = { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', marginBottom: '16px' };
const metricCard        = { background: 'white', padding: '12px', borderRadius: '16px', boxShadow: '0 2px 6px rgba(0,0,0,0.03)', textAlign: 'center' };
const metricValue       = { fontSize: '16px', fontWeight: '700', color: '#1d1d1f', marginTop: '4px' };
const statusColumn      = { flex: 1, padding: '12px', borderRadius: '16px', display: 'flex', flexDirection: 'column', gap: '8px' };
const minListStyle      = { paddingLeft: '14px', margin: 0, fontSize: '11px', color: '#3A3A3C', lineHeight: '1.4' };
const recommendationStyle = { fontSize: '15px', fontWeight: '700', color: '#007AFF', marginBottom: '6px' };
const detailStyle       = { fontSize: '12px', color: '#1d1d1f', lineHeight: '1.5' };
const labelStyle        = { fontSize: '9px', color: '#86868b', textTransform: 'uppercase' };
const closeButtonStyle  = { background: 'none', border: 'none', fontSize: '20px', cursor: 'pointer', color: '#86868b' };

export default AnalysisResultPanel;
