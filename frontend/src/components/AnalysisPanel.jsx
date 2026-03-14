// src/components/AnalysisPanel.jsx
import React from 'react';

const AnalysisPanel = ({ params, setParams, poiCount, loading }) => {
  return (
    <div className="analysis-panel" style={applePanelStyle}>
      {/* 顶部指示条：Apple 风格面板的标准特征 */}
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

      {/* 统计信息 */}
      <div style={footerStyle}>
        <div style={statBoxStyle}>
          <span style={{ fontSize: '18px', fontWeight: '700', color: '#1d1d1f' }}>{poiCount}</span>
          <span style={{ fontSize: '10px', color: '#86868b', marginLeft: '5px' }}>LOCATIONS FOUND</span>
        </div>
        
        {loading && (
          <div className="fade-in" style={loadingStyle}>
            <span style={spinnerStyle}>⌛</span> Updating...
          </div>
        )}
      </div>
    </div>
  );
};

// --- Apple 设计规范样式 ---

const applePanelStyle = {
  position: 'absolute', 
  top: 20, 
  left: 20, 
  zIndex: 100,
  width: '240px',
  padding: '16px 20px 20px 20px',
  
  // 核心圆角与材质
  borderRadius: '24px', 
  backgroundColor: 'rgba(255, 255, 255, 0.72)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)', // 兼容 Safari
  
  boxShadow: '0 8px 32px rgba(0,0,0,0.08)',
  border: '1px solid rgba(255, 255, 255, 0.4)',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
};

const dragHandleStyle = {
  width: '36px', height: '4px',
  backgroundColor: 'rgba(0,0,0,0.08)',
  borderRadius: '2px',
  margin: '0 auto 12px auto'
};

const titleStyle = { 
  marginTop: 0, 
  fontSize: '17px', 
  fontWeight: '600', 
  color: '#1d1d1f',
  letterSpacing: '-0.02em',
  marginBottom: '15px'
};

const sectionStyle = { marginBottom: '18px' };

const labelStyle = { 
  display: 'block', 
  fontSize: '10px', 
  fontWeight: '600',
  color: '#86868b', 
  marginBottom: '8px',
  letterSpacing: '0.05em'
};

const inputStyle = { 
  width: '100%', 
  padding: '10px 12px', 
  borderRadius: '12px', // 统一圆角
  border: '1px solid rgba(0,0,0,0.1)',
  backgroundColor: 'rgba(255,255,255,0.5)',
  fontSize: '14px',
  outline: 'none',
  cursor: 'pointer',
  appearance: 'none', // 去掉默认箭头
  backgroundImage: 'url("data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\'%3E%3Cpolyline points=\'6 9 12 15 18 9\'%3E%3C/polyline%3E%3C/svg%3E")',
  backgroundRepeat: 'no-repeat',
  backgroundPosition: 'right 10px center',
  backgroundSize: '14px'
};

const sliderStyle = { 
  width: '100%', 
  cursor: 'pointer',
  accentColor: '#007AFF' // Apple 系统蓝
};

const footerStyle = { 
  borderTop: '1px solid rgba(0,0,0,0.05)', 
  paddingTop: '15px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px'
};

const statBoxStyle = {
  display: 'flex',
  alignItems: 'baseline'
};

const loadingStyle = { 
  color: '#007AFF', 
  fontSize: '11px', 
  fontWeight: '600',
  display: 'flex',
  alignItems: 'center',
  gap: '4px'
};

const spinnerStyle = {
  display: 'inline-block',
  animation: 'spin 2s linear infinite'
};

export default AnalysisPanel;