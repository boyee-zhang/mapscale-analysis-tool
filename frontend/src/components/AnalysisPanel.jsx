// src/components/AnalysisPanel.jsx
import React, { useState } from 'react';

const AnalysisPanel = ({ params, setParams, poiCount, loading, onAIAnalysis, showTraffic, onToggleTraffic }) => {
  // AI 分析的内部加载状态
  const [isAnalysing, setIsAnalysing] = useState(false);

  const handleAIClick = async () => {
    if (isAnalysing) return;
    setIsAnalysing(true);
    try {
      // 执行传入的 AI 分析逻辑
      await onAIAnalysis();
    } finally {
      setIsAnalysing(false);
    }
  };

  return (
    <div className="analysis-panel" style={applePanelStyle}>
      <div style={dragHandleStyle} />
      
      <h3 style={titleStyle}>Explore Area</h3>
      
      {/* 模式选择 */}
      <div style={sectionStyle}>
        <label style={labelStyle}>TRAVEL MODE</label>
        <select 
          value={params.mode} 
          onChange={e => setParams(p => ({...p, mode: e.target.value}))}
          style={inputStyle}
        >
          <option value="walking">🚶 Walking</option>
          <option value="cycling">🚴 Cycling</option>
          <option value="driving-car">🚗 Driving</option>
        </select>
      </div>

      {/* 时间选择 */}
      <div style={sectionStyle}>
        <label style={labelStyle}>
          TIME RANGE: <span style={{ color: '#007AFF', fontWeight: 'bold' }}>{params.minutes} mins</span>
        </label>
        <input 
          type="range" min="5" max="60" step="5"
          value={params.minutes} 
          onChange={e => setParams(p => ({...p, minutes: parseInt(e.target.value)}))} 
          className="apple-slider"
          style={sliderStyle}
        />
      </div>

      {/* 实时交通按钮 */}
      <div style={{ marginBottom: '12px' }}>
        <button
          onClick={onToggleTraffic}
          disabled={loading}
          style={{
            ...trafficButtonStyle,
            background: showTraffic
              ? 'linear-gradient(135deg, #FF3B30 0%, #FF9500 100%)'
              : 'rgba(0,0,0,0.06)',
            color: showTraffic ? 'white' : '#1d1d1f',
            boxShadow: showTraffic ? '0 4px 12px rgba(255,59,48,0.3)' : 'none',
          }}
        >
          <span style={{ fontSize: '14px' }}>🚦</span>
          <span style={{ marginLeft: '8px' }}>
            {showTraffic ? 'Hide Traffic' : 'Live Traffic'}
          </span>
        </button>
      </div>

      {/* --- AI 分析按钮 --- */}
      <div style={{ marginBottom: '20px' }}>
        <button 
          onClick={handleAIClick}
          disabled={loading || isAnalysing}
          style={{
            ...aiButtonStyle,
            opacity: (loading || isAnalysing) ? 0.6 : 1,
            transform: isAnalysing ? 'scale(0.98)' : 'scale(1)'
          }}
        >
          <span style={{ fontSize: '16px' }}>{isAnalysing ? '⏳' : '✨'}</span>
          <span style={{ marginLeft: '8px' }}>
            {isAnalysing ? 'AI Analyzing...' : 'Investment Analysis'}
          </span>
        </button>
      </div>

      {/* 统计信息 */}
      <div style={footerStyle}>
        <div style={statBoxStyle}>
          <span style={{ fontSize: '18px', fontWeight: '700', color: '#1d1d1f' }}>{poiCount}</span>
          <span style={{ fontSize: '10px', color: '#86868b', marginLeft: '5px' }}>LOCATIONS FOUND</span>
        </div>
        
        {(loading || isAnalysing) && (
          <div className="fade-in" style={loadingStyle}>
            <span style={spinnerStyle}>⚙️</span> Updating data...
          </div>
        )}
      </div>
    </div>
  );
};

const trafficButtonStyle = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: '14px',
  border: 'none',
  fontSize: '13px',
  fontWeight: '600',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
};

const aiButtonStyle = {
  width: '100%',
  padding: '12px',
  borderRadius: '14px',
  border: 'none',
  background: 'linear-gradient(135deg, #007AFF 0%, #AF52DE 100%)', // Apple 风格渐变
  color: 'white',
  fontSize: '13px',
  fontWeight: '600',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  boxShadow: '0 4px 12px rgba(0, 122, 255, 0.3)',
  transition: 'all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
};

// --- 原有样式保持不变 ---
const applePanelStyle = {
  position: 'absolute', top: 20, left: 20, zIndex: 100, width: '240px',
  padding: '16px 20px 20px 20px', borderRadius: '24px', 
  backgroundColor: 'rgba(255, 255, 255, 0.72)', backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  boxShadow: '0 8px 32px rgba(0,0,0,0.08)', border: '1px solid rgba(255, 255, 255, 0.4)',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
};
const dragHandleStyle = { width: '36px', height: '4px', backgroundColor: 'rgba(0,0,0,0.08)', borderRadius: '2px', margin: '0 auto 12px auto' };
const titleStyle = { marginTop: 0, fontSize: '17px', fontWeight: '600', color: '#1d1d1f', letterSpacing: '-0.02em', marginBottom: '15px' };
const sectionStyle = { marginBottom: '18px' };
const labelStyle = { display: 'block', fontSize: '10px', fontWeight: '600', color: '#86868b', marginBottom: '8px', letterSpacing: '0.05em' };
const inputStyle = { width: '100%', padding: '10px 12px', borderRadius: '12px', border: '1px solid rgba(0,0,0,0.1)', backgroundColor: 'rgba(255,255,255,0.5)', fontSize: '14px', outline: 'none', cursor: 'pointer', appearance: 'none', backgroundImage: 'url("data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\'%3E%3Cpolyline points=\'6 9 12 15 18 9\'%3E%3C/polyline%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 10px center', backgroundSize: '14px' };
const sliderStyle = { width: '100%', cursor: 'pointer', accentColor: '#007AFF' };
const footerStyle = { borderTop: '1px solid rgba(0,0,0,0.05)', paddingTop: '15px', display: 'flex', flexDirection: 'column', gap: '8px' };
const statBoxStyle = { display: 'flex', alignItems: 'baseline' };
const loadingStyle = { color: '#007AFF', fontSize: '11px', fontWeight: '600', display: 'flex', alignItems: 'center', gap: '4px' };
const spinnerStyle = { display: 'inline-block' }; // 简单的图标

export { AnalysisPanel as default };