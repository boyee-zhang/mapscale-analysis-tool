import React from 'react';
import { POI_GROUPS } from '../constants/poiCategories';

/**
 * Legend for what is actually on the map right now.
 *
 * It used to be a hardcoded three-row list whose colours did not match what PoiMarker drew
 * (#FFCC00 vs #FFD700, #FF3B30 vs #FF4500). Both sides now read the same constants, and
 * only groups with a selected category are listed — an empty legend row for a type nobody
 * asked for is just noise.
 */
const LegendPanel = ({ categories = [] }) => {
  const activeGroups = POI_GROUPS.filter(g =>
    g.categories.some(([key]) => categories.includes(key))
  );

  const legendItems = [
    ...activeGroups.map(g => ({ label: g.label, color: g.color, type: 'dot' })),
    { label: 'Reachable', color: '#AF52DE', type: 'area' },
  ];

  return (
    <div style={applePanelStyle}>
      <div style={dragHandleStyle} /> {/* 模拟 iOS 的小拉条，增加精致感 */}
      <h4 style={titleStyle}>MAP LAYERS</h4>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {legendItems.map((item, index) => (
          <div key={index} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={labelStyle}>{item.label}</span>
            {item.type === 'dot' ? (
              <div style={{
                width: '10px', height: '10px',
                backgroundColor: item.color,
                borderRadius: '50%',
                boxShadow: `0 0 4px ${item.color}66` // 微弱的发光效果
              }} />
            ) : (
              <div style={{
                width: '24px', height: '10px',
                backgroundColor: `${item.color}33`, // 极低透明度背景
                borderRadius: '4px',
                border: `1.5px solid ${item.color}AA` // 半透明边框
              }} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

// --- Apple Design Language Styles ---
const applePanelStyle = {
  position: 'absolute',
  bottom: '40px',
  left: '20px',
  zIndex: 10,
  // 核心：毛玻璃效果
  backgroundColor: 'rgba(255, 255, 255, 0.7)',
  backdropFilter: 'blur(20px) saturate(180%)',
  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
  
  padding: '16px',
  borderRadius: '20px', // 更圆润
  width: '160px',
  boxShadow: '0 8px 32px rgba(0,0,0,0.08)',
  border: '1px solid rgba(255,255,255,0.4)',
};

const dragHandleStyle = {
  width: '36px', height: '4px',
  backgroundColor: 'rgba(0,0,0,0.1)',
  borderRadius: '2px',
  margin: '-8px auto 12px auto'
};

const titleStyle = {
  margin: '0 0 12px 0',
  fontSize: '10px',
  fontWeight: '600',
  color: 'rgba(0,0,0,0.4)', // 这种浅灰色标题是 Apple 常用手法
  letterSpacing: '0.1em',
  textAlign: 'center'
};

const labelStyle = {
  fontSize: '13px',
  fontWeight: '500',
  color: '#1d1d1f', // Apple 官方文字黑
};

export default LegendPanel;