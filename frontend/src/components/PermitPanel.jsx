import React, { useMemo, useRef, useState } from 'react';
import {
  STAGE_ORDER, STAGE, stageColor, stageLabel,
  noiseColor, noiseBand, NOISE_FILTERS, NOISE_RAMP,
  ACTIVITEIT_LABEL, DOC_TYPE_LABEL, WINDOWS, monthTick,
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
const PermitPanel = ({ data, loading, windowMonths, onSetWindow,
                       noiseFilter, onSetNoiseFilter, onClose }) => {
  const [month, setMonth] = useState(null);

  const months = data?.months || [];
  const peak = useMemo(() => Math.max(1, ...months.map(m => m.count)), [months]);
  const items = useMemo(
    () => (data?.items || []).filter(it => !month || it.published_at.startsWith(month)),
    [data, month]
  );
  const monthTotal = months.find(m => m.month === month)?.count ?? null;
  // The counts on the filter buttons come from the last UNFILTERED response, not from the
  // current one. `by_noise` is computed over the filtered set, so reading it live made
  // "Quiet" show 0 the moment "Noisy" was selected — the button said there were no quiet
  // notices while 1,678 of them had just been filtered out. A button's count has to describe
  // what clicking it would give you, which is a property of the unfiltered set.
  const unfilteredNoise = useRef({});
  const byNoise = useMemo(() => {
    const live = Object.fromEntries((data?.by_noise || []).map(b => [b.noise, b.count]));
    if (data && !noiseFilter) unfilteredNoise.current = live;
    return unfilteredNoise.current;
  }, [data, noiseFilter]);
  // A list, because a partial rescore can leave two model versions in play. One is normal.
  const model = data?.noise_models?.[0] || null;

  return (
    <div style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <h3 style={titleStyle}>Public notices</h3>
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

      <div style={noiseRowStyle}>
        {NOISE_FILTERS.map(f => {
          const on = JSON.stringify(f.value) === JSON.stringify(noiseFilter);
          return (
            <button
              key={f.label}
              onClick={() => { setMonth(null); onSetNoiseFilter(f.value); }}
              style={{ ...pillStyle, ...(on ? pillOnStyle : null) }}
            >
              {f.label}
              {byNoise && f.value?.length === 1 ? ` ${byNoise[f.value[0]] ?? 0}` : ''}
            </button>
          );
        })}
      </div>

      {/* Said in the panel, not only in a code comment, because the number next to a
          publication looks like a fact unless something says otherwise. The binary cut is
          close to a permit-type lookup; the score's real power is ordering within a type. */}
      {model && (
        <p style={modelNoteStyle}>
          Noise is predicted, not published — precision {model.metrics.precision.toFixed(2)},
          recall {model.metrics.recall.toFixed(2)} on {model.metrics.n_noisy
          ? `${model.n_train ?? 695} hand-labelled publications` : 'a hand-labelled sample'}.
          The score ranks within a permit type; the noisy/quiet cut itself is close to a
          permit-type lookup.
        </p>
      )}

      {model && (
        <div style={legendStyle}>
          <div style={{
            ...legendBarStyle,
            backgroundImage: `linear-gradient(to right, ${NOISE_RAMP.map(
              ([v, c]) => `${c} ${Math.round(v * 100)}%`).join(', ')})`,
          }} />
          <div style={legendLabelRowStyle}>
            <span>Quieter</span>
            <span>Noisier</span>
          </div>
        </div>
      )}

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

          {/* What KIND of notice. This IS a partition — one publication has exactly one
              doc_type — so unlike the activity row below, these sum to the total. It is the
              primary breakdown since the building-permit filter was lifted: before that
              every row shared one doc_type and this would have been a single chip. */}
          {(data.by_type || []).length > 1 && (
            <div style={actRowStyle}>
              {data.by_type.map(t => (
                <span key={t.doc_type} style={actChipStyle}>
                  {DOC_TYPE_LABEL[t.doc_type] || t.doc_type} {t.count}
                </span>
              ))}
            </div>
          )}

          {/* activity mix. These do NOT sum to the total: one permit can carry several, AND
              only omgevingsvergunning rows carry any at all — so with every doc_type in
              scope this covers a minority of the list, which is what the note says. */}
          {(data.by_activiteit || []).length > 1 && (
            <div style={actRowStyle}>
              {data.by_activiteit.map(a => (
                <span key={a.activiteit} style={actChipStyle}>
                  {ACTIVITEIT_LABEL[a.activiteit] || a.activiteit} {a.count}
                </span>
              ))}
              <span style={derivedNoteStyle}>
                Building/environment permits only, and one can carry several — these overlap
                and do not sum to the total.
              </span>
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
              Listing {data.sort === 'noise' ? 'the 500 highest-scoring' : 'the 500 most recent'}
              {' '}of {data.total}. The bars above count all of them.
            </p>
          )}

          <ul style={listStyle}>
            {items.slice(0, 200).map(it => (
              <li key={it.identifier} style={itemStyle}>
                <span style={{ ...swatchStyle, background: noiseColor(it.noise_score),
                               marginTop: '5px' }} />
                <div style={{ minWidth: 0 }}>
                  <a href={it.url} target="_blank" rel="noreferrer" style={itemTitleStyle}>
                    {it.title}
                  </a>
                  <div style={itemMetaStyle}>
                    {/* The words carry the ranking too, so the colour is never the only
                        channel — and they say "noisier than most" rather than a percentage,
                        because the score is a logistic output on 695 rows, not a calibrated
                        probability. */}
                    <span style={{ color: noiseColor(it.noise_score), fontWeight: 600 }}>
                      {noiseBand(it.noise_score)}
                      {it.noise_score == null ? '' : ` ${it.noise_score}`}
                    </span>
                    {' · '}{it.published_at} · {stageLabel(it.stage)} (from title)
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

const windowRowStyle = { display: 'flex', gap: '4px', margin: '12px 0 6px' };
const noiseRowStyle = { display: 'flex', gap: '4px', margin: '0 0 8px' };
const modelNoteStyle = {
  margin: '0 0 8px', fontSize: '10px', lineHeight: 1.45, color: '#86868b',
};
const legendStyle = { margin: '0 0 10px' };
// The gradient's stops are placed at the ramp's own score positions, not spread evenly, so
// the legend is a true picture of the encoding rather than a decorative swatch.
const legendBarStyle = { height: '6px', borderRadius: '3px' };
const legendLabelRowStyle = {
  display: 'flex', justifyContent: 'space-between',
  fontSize: '9px', color: '#86868b', marginTop: '3px',
};
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
