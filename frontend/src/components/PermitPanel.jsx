import React, { useMemo, useState } from 'react';
import {
  STAGE_ORDER, STAGE, stageColor, stageLabel,
  ACTIVITEIT_LABEL, WINDOWS, monthTick,
} from '../constants/bekendmaking';

/**
 * "What has been happening here, month by month" for the current isochrone.
 *
 * The month series comes from the backend and covers every match; the list below it is
 * capped at 500 items. So the bars are authoritative and the list is a sample — clicking
 * a bar filters the sample, and the bar's own count stays the real one. The panel says so
 * when the two disagree, because a filtered list shorter than the bar it came from looks
 * like a bug otherwise.
 */
const PermitPanel = ({ data, loading, windowMonths, onSetWindow, onClose }) => {
  const [month, setMonth] = useState(null);

  const months = data?.months || [];
  const peak = useMemo(() => Math.max(1, ...months.map(m => m.count)), [months]);
  const items = useMemo(
    () => (data?.items || []).filter(it => !month || it.published_at.startsWith(month)),
    [data, month]
  );
  const monthTotal = months.find(m => m.month === month)?.count ?? null;

  return (
    <div style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <h3 style={titleStyle}>Building permits</h3>
          <span style={subtitleStyle}>
            {data?.window ? `${data.window.from} → ${data.window.to}` : '—'}
          </span>
        </div>
        <button onClick={onClose} style={closeStyle} aria-label="Close">×</button>
      </div>

      <div style={windowRowStyle}>
        {WINDOWS.map(w => (
          <button
            key={w.months}
            onClick={() => { setMonth(null); onSetWindow(w.months); }}
            style={{ ...pillStyle, ...(w.months === windowMonths ? pillOnStyle : null) }}
          >
            {w.label}
          </button>
        ))}
      </div>

      {data?.shape === 'unavailable' && (
        <p style={noticeStyle}>
          No local database configured, so there is nothing to show here — this is not
          “zero permits”.
        </p>
      )}

      {loading && <p style={noticeStyle}>Loading…</p>}

      {data && data.shape !== 'unavailable' && (
        <>
          <div style={totalRowStyle}>
            <span style={totalNumStyle}>{data.total}</span>
            <span style={totalLabelStyle}>
              permits{data.shape === 'circle' ? ' · radius fallback, no isochrone' : ''}
            </span>
          </div>

          {/* stage mix — derived from the title, and labelled as such */}
          <div style={stageRowStyle}>
            {STAGE_ORDER
              .map(s => ({ s, n: data.by_stage?.find(x => x.stage === s)?.count || 0 }))
              .filter(({ n }) => n > 0)
              .map(({ s, n }) => (
                <span key={s} style={stageChipStyle} title={STAGE[s].hint}>
                  <span style={{ ...swatchStyle, background: stageColor(s) }} />
                  {stageLabel(s)} <strong>{n}</strong>
                </span>
              ))}
          </div>
          <p style={derivedNoteStyle}>Stage is read from the publication title, not a published field.</p>

          {/* month histogram — click to filter the list */}
          {months.length > 0 && (
            <div style={chartStyle}>
              {months.map(m => (
                <button
                  key={m.month}
                  onClick={() => setMonth(month === m.month ? null : m.month)}
                  title={`${monthTick(m.month)} — ${m.count}`}
                  style={{ ...barWrapStyle, ...(month === m.month ? barWrapOnStyle : null) }}
                >
                  <span style={barCountStyle}>{m.count}</span>
                  <span style={{ ...barStyle, height: `${Math.max(3, (m.count / peak) * 72)}px` }} />
                  <span style={barTickStyle}>{monthTick(m.month).slice(0, 3)}</span>
                </button>
              ))}
            </div>
          )}

          {/* activity mix. These do NOT sum to the total: one permit can carry several. */}
          {(data.by_activiteit || []).length > 1 && (
            <div style={actRowStyle}>
              {data.by_activiteit.map(a => (
                <span key={a.activiteit} style={actChipStyle}>
                  {ACTIVITEIT_LABEL[a.activiteit] || a.activiteit} {a.count}
                </span>
              ))}
              <span style={derivedNoteStyle}>One permit can cover several activities, so these overlap.</span>
            </div>
          )}

          {month && (
            <p style={filterNoteStyle}>
              Showing {monthTick(month)}
              {monthTotal !== null && items.length < monthTotal
                ? ` — ${items.length} of ${monthTotal}, the rest is past the 500-item cap`
                : ''}
              {' · '}
              <button onClick={() => setMonth(null)} style={linkButtonStyle}>clear</button>
            </p>
          )}

          {data.truncated && !month && (
            <p style={filterNoteStyle}>
              Listing the 500 most recent of {data.total}. The bars above count all of them.
            </p>
          )}

          <ul style={listStyle}>
            {items.slice(0, 200).map(it => (
              <li key={it.identifier} style={itemStyle}>
                <span style={{ ...swatchStyle, background: stageColor(it.stage), marginTop: '5px' }} />
                <div style={{ minWidth: 0 }}>
                  <a href={it.url} target="_blank" rel="noreferrer" style={itemTitleStyle}>
                    {it.title}
                  </a>
                  <div style={itemMetaStyle}>
                    {it.published_at} · {stageLabel(it.stage)}
                    {it.gemeente ? ` · ${it.gemeente}` : ''}
                  </div>
                </div>
              </li>
            ))}
            {items.length === 0 && <li style={emptyStyle}>Nothing in this window.</li>}
          </ul>
        </>
      )}
    </div>
  );
};

// --- styles (kept out of the JSX per the project's UI rule) ---

const panelStyle = {
  position: 'absolute', top: '20px', right: '20px', width: '340px',
  maxHeight: 'calc(100vh - 40px)', overflowY: 'auto', zIndex: 5,
  padding: '16px 18px 18px', borderRadius: '20px',
  background: 'rgba(255,255,255,0.86)', backdropFilter: 'blur(20px)',
  boxShadow: '0 12px 40px rgba(0,0,0,0.14)', border: '1px solid rgba(255,255,255,0.6)',
};

const headerStyle = { display: 'flex', alignItems: 'flex-start', gap: '8px' };
const titleStyle = { margin: 0, fontSize: '16px', fontWeight: 700, color: '#1d1d1f' };
const subtitleStyle = { fontSize: '11px', color: '#86868b' };
const closeStyle = {
  marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer',
  fontSize: '22px', lineHeight: 1, color: '#86868b', padding: 0,
};

const windowRowStyle = { display: 'flex', gap: '4px', margin: '12px 0 10px' };
// Longhand border properties, not the `border` shorthand: pillOnStyle overrides only the
// colour, and React warns (and can mis-apply) when a rerender drops a longhand while a
// conflicting shorthand is set. Caught from the browser console, not from the diff.
const pillStyle = {
  flex: 1, padding: '5px 0', borderRadius: '10px', cursor: 'pointer',
  borderWidth: '1px', borderStyle: 'solid', borderColor: 'rgba(0,0,0,0.1)',
  background: 'rgba(0,0,0,0.03)',
  fontSize: '11px', fontWeight: 600, color: '#1d1d1f',
};
const pillOnStyle = { background: '#007AFF', color: 'white', borderColor: '#007AFF' };

const totalRowStyle = { display: 'flex', alignItems: 'baseline', gap: '6px', marginBottom: '8px' };
const totalNumStyle = { fontSize: '26px', fontWeight: 700, color: '#1d1d1f' };
const totalLabelStyle = { fontSize: '11px', color: '#86868b' };

const stageRowStyle = { display: 'flex', flexWrap: 'wrap', gap: '6px' };
const stageChipStyle = {
  display: 'inline-flex', alignItems: 'center', gap: '5px',
  padding: '3px 8px', borderRadius: '10px', background: 'rgba(0,0,0,0.04)',
  fontSize: '11px', color: '#1d1d1f',
};
const swatchStyle = { width: '8px', height: '8px', borderRadius: '50%', flexShrink: 0 };

const derivedNoteStyle = { margin: '6px 0 0', fontSize: '10px', color: '#a1a1a6', lineHeight: 1.4 };

const chartStyle = {
  display: 'flex', alignItems: 'flex-end', gap: '2px',
  margin: '12px 0 4px', paddingTop: '14px',
  borderTop: '1px solid rgba(0,0,0,0.06)',
};
const barWrapStyle = {
  flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px',
  background: 'none', border: 'none', padding: '2px 0', cursor: 'pointer', borderRadius: '6px',
};
const barWrapOnStyle = { background: 'rgba(0,122,255,0.1)' };
const barStyle = { width: '100%', maxWidth: '18px', borderRadius: '3px', background: '#007AFF' };
const barCountStyle = { fontSize: '9px', color: '#86868b' };
const barTickStyle = { fontSize: '9px', color: '#86868b' };

const actRowStyle = { display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '10px' };
const actChipStyle = {
  padding: '2px 7px', borderRadius: '9px', background: 'rgba(0,122,255,0.08)',
  fontSize: '10px', color: '#0051a8',
};

const filterNoteStyle = { margin: '10px 0 0', fontSize: '11px', color: '#86868b' };
const linkButtonStyle = {
  background: 'none', border: 'none', padding: 0, cursor: 'pointer',
  fontSize: '11px', fontWeight: 600, color: '#007AFF',
};
const noticeStyle = { margin: '10px 0 0', fontSize: '12px', color: '#86868b', lineHeight: 1.5 };

const listStyle = {
  listStyle: 'none', margin: '12px 0 0', padding: '12px 0 0',
  borderTop: '1px solid rgba(0,0,0,0.06)',
};
const itemStyle = { display: 'flex', gap: '8px', padding: '6px 0' };
const itemTitleStyle = {
  display: 'block', fontSize: '12px', lineHeight: 1.4, color: '#1d1d1f',
  textDecoration: 'none', overflowWrap: 'anywhere',
};
const itemMetaStyle = { fontSize: '10px', color: '#86868b', marginTop: '2px' };
const emptyStyle = { fontSize: '12px', color: '#86868b' };

export default PermitPanel;
