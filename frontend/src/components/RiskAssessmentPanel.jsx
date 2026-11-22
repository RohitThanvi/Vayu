import { useState, useEffect, useCallback } from 'react';
import { fmt } from '../lib/riskFormat.js';

// Detailed risk assessment (v2) for the Agri Score tab: stress indicators, land vulnerability, soil condition, sensitivity, confidence,
// each with its source, plus the full PDF report. Sits BELOW the original score card, which is unchanged (alerts still use it).
// Every number shown comes straight from POST /agri/risk-assessment; an unavailable indicator is listed with its reason, never as zero.
const S = {
  bg: 'var(--vayu-bg)', surface: 'var(--vayu-surface)', surface2: 'var(--vayu-surface2)', border: 'var(--vayu-border)',
  text: 'var(--vayu-text)', text2: 'var(--vayu-text2)', text3: 'var(--vayu-text3)', accent: 'var(--vayu-accent)',
  mono: "'JetBrains Mono', 'Courier New', monospace",
};
const RISK_COLORS = ['#4a7c59', '#c9933a', '#c96a3a', '#8b2020'];   // Low, Moderate, High, Very high
const RISK_ORDER = ['Low', 'Moderate', 'High', 'Very high'];
const riskColor = (cls) => { const i = RISK_ORDER.indexOf(cls); return i < 0 ? S.text3 : RISK_COLORS[i]; };


const Label = ({ children }) => <div style={{ fontSize: 11, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase', color: S.text3, marginTop: 4 }}>{children}</div>;
const Box = ({ children, color, testid }) => <div data-testid={testid} style={{ background: S.surface2, border: `1px solid ${color || S.border}`, padding: '9px 11px', fontSize: 13, color: S.text2, lineHeight: 1.55 }}>{children}</div>;

function IndicatorRow({ i }) {
  const ok = i.status === 'ok';
  return (
    <details data-testid={`indicator-${i.id}`} style={{ background: S.surface2, border: `1px solid ${S.border}`, padding: '7px 10px', fontSize: 13, color: S.text2 }}>
      <summary style={{ cursor: 'pointer', lineHeight: 1.5 }}>
        <b style={{ color: S.text }}>{i.name}</b>{' '}
        {ok ? <span>: {fmt(i.value, 2)}{i.unit && i.unit !== '0-100' ? ` ${i.unit}` : ''} · {i.class} · {fmt(i.stress, 0)} stress pts</span>
            : <span style={{ color: S.text3 }}>: not available, not scored</span>}
      </summary>
      <div style={{ marginTop: 6, fontSize: 12, color: S.text3, lineHeight: 1.6 }}>
        {ok ? (
          <>
            <div>Baseline: {i.baseline}</div>
            {i.percentile != null && <div>Percentile: {fmt(i.percentile)}</div>}
            <div>Reliability: {i.reliability} · Resolution: {i.resolution} · Window: {i.window}</div>
            <div>Source: {i.source}</div>
            {i.note && <div>{i.note}</div>}
          </>
        ) : (<><div>Reason: {i.reason}</div><div>Source: {i.source}</div></>)}
      </div>
    </details>
  );
}

const km2 = (v) => (v == null ? 'n/a' : `${fmt(v, 2)} km²`);
const pc = (v) => (v == null ? 'n/a' : `${fmt(v)}%`);

// The original three indicators (drought extent, vegetation loss, soil moisture) with their raw metrics, weights and sub-scores: part of the ONE assessment, not a separate report.
function OriginalIndicators({ lg }) {
  const rm = lg.raw_metrics || {}, d = rm.drought || {}, v = rm.vegetation || {}, m = rm.soil_moisture || {}, ss = lg.sub_scores || {}, w = lg.weights || {};
  const missing = (k, src) => {
    if (ss[k] != null) return null;
    if (k === 'drought' && d.assessed_area_pct != null && lg.min_assessed_pct != null && d.assessed_area_pct < lg.min_assessed_pct)
      return `Not assessed: only ${pc(d.assessed_area_pct)} of the area could be scored (the rest is bare soil, water or cloud).`;
    return (rm[src] || (lg.inputs_failed || []).includes(k)) ? 'Not assessed: no usable reading for this area and period.' : 'Not assessed.';
  };
  const rows = [
    ['drought', 'Drought extent (NDDI)', 'drought', [
      ['Drought-affected area (NDDI above 0.5)', `${km2(d.drought_affected_km2)} (${pc(d.drought_affected_pct)} of the area)`],
      ['Share of the assessable area under drought', pc(d.drought_pct_of_assessed)],
      ['Severe drought area (NDDI above 0.7)', km2(d.severe_drought_km2)],
      ['Area where NDDI could be computed', pc(d.assessed_area_pct)],
      ['Mean NDDI start / end / change', `${fmt(d.avg_nddi_start, 3)} / ${fmt(d.avg_nddi_end, 3)} / ${fmt(d.nddi_change, 3)}`]]],
    ['vegetation_loss', 'Vegetation loss (Sentinel-2 NDVI)', 'vegetation', [
      ['Vegetation lost', `${km2(v.vegetation_loss_km2)} (${pc(v.loss_pct)} of the initial vegetated area)`],
      ['Vegetation gained', km2(v.vegetation_gain_km2)], ['Initial vegetated area', `${km2(v.initial_vegetation_km2)} (${pc(v.vegetation_pct_of_aoi)} of the area)`],
      ...(v.small_base_caveat ? [['Caution', 'Vegetation covers under 2% of the area, so the loss percentage rests on very few pixels.']] : [])]],
    ['moisture_deficit', 'Soil moisture (NASA SMAP)', 'soil_moisture', [
      ['Average moisture start / end', `${fmt(m.start_avg_soil_moisture, 3)} / ${fmt(m.end_avg_soil_moisture, 3)} m³/m³`], ['Change', `${fmt(m.moisture_change, 3)} m³/m³`],
      ['Dry-stress share (below 0.1 m³/m³)', `${pc(m.dry_stress_pct)} (${km2(m.dry_stress_area_km2)})`],
      ...(m.window_overlap_caveat ? [['Caution', 'The start and end windows overlap, so the change understates real change.']] : []), ...(m.sampling_note ? [['Coverage', m.sampling_note]] : [])]],
  ];
  return (
    <div data-testid="original-indicators" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Label>Original three indicators</Label>
      <Box>
        Composite {fmt(lg.risk_score, 0)}/100 ({lg.band}), confidence {fmt(lg.confidence, 0)}%{lg.reason ? `: ${lg.reason}` : ''}. This is the score alerts and the watchlist use.
      </Box>
      {rows.map(([k, name, src, items]) => {
        const why = missing(k, src);
        return (
          <details key={k} data-testid={`original-${k}`} style={{ background: S.surface2, border: `1px solid ${S.border}`, padding: '7px 10px', fontSize: 13, color: S.text2 }}>
            <summary style={{ cursor: 'pointer', lineHeight: 1.5 }}>
              <b style={{ color: S.text }}>{name}</b>: {why ? <span style={{ color: S.text3 }}>not assessed</span> : `${fmt(ss[k])}/100`}{w[k] != null && ` · weight ${fmt(w[k] * 100, 0)}%`}
            </summary>
            <div style={{ marginTop: 6, fontSize: 12, color: S.text3, lineHeight: 1.6 }}>
              {why && <div>{why}</div>}
              {items.map(([a, b]) => <div key={a}>{a}: {b}</div>)}
            </div>
          </details>
        );
      })}
    </div>
  );
}

export default function RiskAssessmentPanel({ drawnAOI, apiUrl, regionName }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState(null);

  // A different drawn area makes the old assessment wrong for the map: clear it rather than show it under the new AOI.
  useEffect(() => { setResult(null); setError(null); setPdfError(null); }, [drawnAOI]);

  const body = useCallback((withName) => JSON.stringify({ aoi_geojson: drawnAOI, ...(withName && regionName ? { region_name: regionName } : {}) }), [drawnAOI, regionName]);

  const run = useCallback(async () => {
    if (!drawnAOI) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/risk-assessment`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body(false) });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Risk assessment failed');
      setResult(await resp.json());
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, [drawnAOI, apiUrl, body]);

  const download = useCallback(async () => {
    if (!drawnAOI) return;
    setPdfLoading(true); setPdfError(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/risk-assessment/pdf`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body(true) });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Could not build the PDF report');
      const url = URL.createObjectURL(await resp.blob());
      const a = document.createElement('a');
      a.href = url; a.download = `Vayu_risk_assessment_${result?.as_of || 'latest'}.pdf`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { setPdfError(e.message); } finally { setPdfLoading(false); }
  }, [drawnAOI, apiUrl, body, result]);

  const btn = (disabled) => ({
    padding: '10px', fontSize: 14, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
    background: disabled ? S.surface2 : 'rgba(126,184,212,0.1)', border: `1px solid ${disabled ? S.border : S.accent}`,
    color: disabled ? S.text3 : S.accent, cursor: disabled ? 'not-allowed' : 'pointer',
  });
  const r = result, risk = r?.risk, color = riskColor(risk?.class);

  return (
    <div data-testid="risk-assessment" style={{ display: 'flex', flexDirection: 'column', gap: 9, marginTop: 6, paddingTop: 12, borderTop: `1px solid ${S.border}` }}>
      <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
        Every figure shows its source. Indicative only; not validated against yield or loss data.
      </div>
      <button onClick={run} disabled={!drawnAOI || loading} aria-label="Run risk assessment" style={btn(!drawnAOI || loading)}>
        {loading ? 'ASSESSING...' : 'RUN RISK ASSESSMENT'}
      </button>
      {error && <div data-testid="assessment-error" style={{ background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '9px 11px', fontSize: 14, color: S.text2 }}>{error}</div>}
      {r && (
        <>
          <div data-testid="assessment-headline" style={{ padding: 12, background: S.surface2, border: `1px solid ${color}` }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <div data-testid="risk-class" style={{ fontSize: 22, fontFamily: S.mono, fontWeight: 700, color }}>{risk?.class || 'Cannot assess'}</div>
              <div style={{ fontSize: 12, color: S.text3 }}>risk class · as of {r.as_of}</div>
            </div>
            <div style={{ fontSize: 13, color: S.text2, marginTop: 4 }}>
              Stress {r.stress?.class}{r.stress?.score != null && ` (${fmt(r.stress.score)}/100)`} · Land vulnerability {r.vulnerability?.class}{r.vulnerability?.score != null && ` (${fmt(r.vulnerability.score)}/100)`} · Confidence {r.confidence?.label} ({fmt(r.confidence?.score, 2)})
            </div>
            <div style={{ fontSize: 12, color: S.text3, marginTop: 4, lineHeight: 1.5 }}>{risk?.rule}</div>
          </div>

          {r.summary?.drivers?.length > 0 && <Box testid="drivers"><b style={{ color: S.text }}>Driving it:</b> {r.summary.drivers.join('; ')}</Box>}
          {r.summary?.unavailable?.length > 0 && <Box testid="unavailable" color="rgba(201,147,58,0.5)"><b style={{ color: S.text }}>Not available (not scored, never treated as zero):</b> {r.summary.unavailable.join('; ')}</Box>}

          <Label>Stress indicators</Label>
          {r.indicators?.map((i) => <IndicatorRow key={i.id} i={i} />)}

          <Label>Land vulnerability</Label>
          {r.vulnerability?.factors?.length > 0 ? (
            <Box testid="vulnerability">
              {r.vulnerability.factors.map((f) => (
                <div key={f.id}><b style={{ color: S.text }}>{f.name}:</b> {f.value} · {f.class} · {fmt(f.points, 0)} pts</div>
              ))}
              <div style={{ fontSize: 12, color: S.text3, marginTop: 4 }}>{r.vulnerability.factors_used} of {r.vulnerability.of} factors could be rated.</div>
            </Box>
          ) : <Box testid="vulnerability">Land profile unavailable, so vulnerability is unknown and does not change the risk class.</Box>}

          {r.soil_condition?.properties?.length > 0 && (
            <>
              <Label>Soil condition (described, not scored)</Label>
              <Box testid="soil">
                {r.soil_condition.properties.map((p) => <div key={p.id}><b style={{ color: S.text }}>{p.name}:</b> {typeof p.value === 'number' && p.id === 'ph' ? p.value.toFixed(1) : String(p.value)} · {p.class}</div>)}
                <div style={{ fontSize: 12, color: S.text3, marginTop: 4 }}>{r.soil_condition.advice}</div>
              </Box>
            </>
          )}

          {r.sensitivity?.range != null && (
            <Box testid="sensitivity">
              <b style={{ color: S.text }}>Sensitivity:</b> the stress score ranges {fmt(r.sensitivity.min)} to {fmt(r.sensitivity.max)} across alternative weightings, {r.sensitivity.robust ? 'so the result is robust to how the families are weighted.' : 'so it depends noticeably on the weighting. Read the class, not the exact number.'}
            </Box>
          )}

          {r.legacy && <OriginalIndicators lg={r.legacy} />}

          <button onClick={download} disabled={pdfLoading} aria-label="Download full assessment report (PDF)" style={btn(pdfLoading)}>
            {pdfLoading ? 'PREPARING PDF...' : 'FULL REPORT WITH ALL METRICS (PDF)'}
          </button>
          {pdfError && <div data-testid="pdf-error" style={{ fontSize: 12, color: S.text2, background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '6px 9px' }}>{pdfError}</div>}
        </>
      )}
    </div>
  );
}
