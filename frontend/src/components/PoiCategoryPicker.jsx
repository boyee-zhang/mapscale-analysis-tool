import React, { useState } from 'react';
import { POI_GROUPS } from '../constants/poiCategories';

/**
 * Collapsible multi-select over the 37 poi_category keys, grouped by colour.
 *
 * Collapsed by default and summarised as a count: expanded, 37 rows would be taller than
 * the panel it sits in. Each group header toggles its whole group, so the common case
 * ("show me all transport") is one click rather than seven.
 */
const PoiCategoryPicker = ({ selected, onChange, disabled }) => {
  const [open, setOpen] = useState(false);

  const isOn = (key) => selected.includes(key);

  const toggleOne = (key) =>
    onChange(isOn(key) ? selected.filter(c => c !== key) : [...selected, key]);

  const toggleGroup = (group) => {
    const keys = group.categories.map(([k]) => k);
    const allOn = keys.every(isOn);
    onChange(allOn
      ? selected.filter(c => !keys.includes(c))
      : [...new Set([...selected, ...keys])]);
  };

  return (
    <div style={sectionStyle}>
      <button onClick={() => setOpen(v => !v)} disabled={disabled} style={headerButtonStyle}>
        <span style={labelStyle}>POI TYPES</span>
        <span style={countStyle}>
          {selected.length} selected {open ? '▾' : '▸'}
        </span>
      </button>

      {open && (
        <div style={listStyle}>
          {POI_GROUPS.map(group => {
            const keys = group.categories.map(([k]) => k);
            const allOn = keys.every(isOn);
            const someOn = !allOn && keys.some(isOn);
            return (
              <div key={group.key} style={groupStyle}>
                <button onClick={() => toggleGroup(group)} style={groupHeaderStyle}>
                  <span style={{ ...swatchStyle, backgroundColor: group.color }} />
                  <span style={groupLabelStyle}>{group.label}</span>
                  <span style={groupStateStyle}>
                    {allOn ? 'all' : someOn ? 'some' : ''}
                  </span>
                </button>
                <div style={chipWrapStyle}>
                  {group.categories.map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => toggleOne(key)}
                      style={{
                        ...chipStyle,
                        ...(isOn(key) ? { background: group.color, color: 'white', borderColor: group.color } : null),
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}

          <div style={footerStyle}>
            <button onClick={() => onChange([])} style={textButtonStyle}>Clear all</button>
          </div>
        </div>
      )}
    </div>
  );
};

// --- styles (kept out of the JSX per the project's UI rule) ---

const sectionStyle = { marginBottom: '12px' };

const labelStyle = {
  fontSize: '11px', fontWeight: '600', color: '#86868b',
  letterSpacing: '0.5px', textTransform: 'uppercase',
};

const headerButtonStyle = {
  width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '8px 0', background: 'none', border: 'none', cursor: 'pointer',
};

const countStyle = { fontSize: '11px', fontWeight: '600', color: '#007AFF' };

const listStyle = {
  maxHeight: '260px', overflowY: 'auto',
  borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: '8px',
};

const groupStyle = { marginBottom: '10px' };

const groupHeaderStyle = {
  width: '100%', display: 'flex', alignItems: 'center', gap: '6px',
  padding: '2px 0', background: 'none', border: 'none', cursor: 'pointer',
};

const swatchStyle = {
  width: '10px', height: '10px', borderRadius: '50%', flexShrink: 0,
};

const groupLabelStyle = { fontSize: '12px', fontWeight: '600', color: '#1d1d1f' };

const groupStateStyle = { fontSize: '10px', color: '#86868b', marginLeft: 'auto' };

const chipWrapStyle = {
  display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px', paddingLeft: '16px',
};

const chipStyle = {
  padding: '3px 8px', borderRadius: '10px',
  border: '1px solid rgba(0,0,0,0.12)', background: 'rgba(0,0,0,0.03)',
  fontSize: '10px', fontWeight: '500', color: '#1d1d1f',
  cursor: 'pointer', transition: 'all 0.12s ease',
};

const footerStyle = { display: 'flex', justifyContent: 'flex-end', paddingTop: '4px' };

const textButtonStyle = {
  background: 'none', border: 'none', cursor: 'pointer',
  fontSize: '11px', fontWeight: '600', color: '#FF3B30',
};

export default PoiCategoryPicker;
