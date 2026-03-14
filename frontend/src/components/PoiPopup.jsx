import React from 'react';

const PoiPopup = ({ poi }) => {
  const { tags } = poi;
  if (!tags) return null;

  return (
    <div style={containerStyle}>
      <h4 style={titleStyle}>
        {tags.name || 'Unnamed Location'}
      </h4>
      
      <div style={contentStyle}>
        <div style={infoRowStyle}>
          <span style={labelStyle}>🏠 Address</span>
          <span style={valueStyle}>
            {tags['addr:street']} {tags['addr:housenumber']}{tags['addr:city'] ? `, ${tags['addr:city']}` : ''}
          </span>
        </div>

        {tags.opening_hours && (
          <div style={infoRowStyle}>
            <span style={labelStyle}>⏰ Hours</span>
            <span style={valueStyle}>{tags.opening_hours}</span>
          </div>
        )}

        {tags.website && (
          <a href={tags.website} target="_blank" rel="noreferrer" style={linkStyle}>
            Visit Website ↗
          </a>
        )}
      </div>
    </div>
  );
};

// --- 样式定义 ---
const containerStyle = {
  padding: '12px',
  minWidth: '200px',
  backgroundColor: '#fff',
  // 组件内部圆角，确保内容不会溢出
  borderRadius: '12px', 
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
};

const titleStyle = { 
  margin: '0 0 10px 0', 
  color: '#1a1a1a', 
  fontSize: '15px',
  fontWeight: '600',
  lineHeight: '1.2'
};

const contentStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: '8px'
};

const infoRowStyle = { 
  display: 'flex', 
  flexDirection: 'column',
  gap: '2px'
};

const labelStyle = { 
  fontWeight: '500', 
  color: '#8e8e93', // 经典 iOS 灰色
  fontSize: '11px', 
  textTransform: 'uppercase',
  letterSpacing: '0.02em'
};

const valueStyle = { 
  color: '#3a3a3c', 
  fontSize: '13px',
  lineHeight: '1.4'
};

const linkStyle = { 
  display: 'inline-block', 
  marginTop: '4px', 
  color: '#007AFF', 
  textDecoration: 'none', 
  fontWeight: '600',
  fontSize: '13px'
};

export default PoiPopup;