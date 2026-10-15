/** India food-and-fuel pressure: each indicator against its OWN last 12 months, side by side. Deliberately no combined index. */
import { S, useJson, num, BAND_COLOR } from './theme';
import { Change, Panel, DataStamp, Empty, Spark } from './ui';

const GROUP_LABEL = { fuel: 'Fuel', currency: 'Currency', food: 'Food' };

function Gauge({ pct }) {
  return (
    <div style={{ position: 'relative', height: 5, background: S.surface2, borderRadius: 3, marginTop: 5 }} aria-label={`Percentile ${pct}`}>
      <div style={{ position: 'absolute', left: `${Math.min(100, Math.max(0, pct ?? 0))}%`, top: -3, width: 3, height: 11, background: pct >= 80 ? '#e0c23c' : S.accent, borderRadius: 1 }} />
    </div>
  );
}

export default function IndiaRiskPanel({ apiUrl }) {
  const { data, error } = useJson(apiUrl, '/api/v1/intel/india-risk', 10 * 60 * 1000);
  return (
    <Panel title="India · food & fuel pressure" right={<DataStamp quality={data?.data_quality} />} style={{ margin: '10px 14px' }}>
      {!data && !error && <Empty>Loading…</Empty>}
      {error && !data && <Empty>India indicators are unavailable right now.</Empty>}
      {data && !data.indicators?.length && <Empty>No indicator data available this cycle ({data.data_quality?.last_error || 'upstream unavailable'}).</Empty>}
      {data?.indicators?.length > 0 && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: 8 }}>
            {data.indicators.map((i) => (
              <div key={i.id} title={i.why} style={{ border: `1px solid ${S.border}`, borderRadius: 3, padding: '8px 10px', background: S.surface2 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 11, color: S.text2 }}>{i.label}</span>
                  <span style={{ fontSize: 9, color: S.text3, textTransform: 'uppercase' }}>{GROUP_LABEL[i.group] || i.group}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 4 }}>
                  <div>
                    <div style={{ fontFamily: S.mono, fontSize: 15, color: S.text }}>{num(i.latest)}</div>
                    <div style={{ fontSize: 9.5, color: S.text3 }}>{i.unit}</div>
                  </div>
                  <Spark values={i.sparkline} />
                </div>
                <Gauge pct={i.percentile_12m} />
                <div style={{ fontSize: 10, color: S.text2, marginTop: 4 }}>{i.position} · 1m <Change pct={i.change_1m_pct} /> · 3m <Change pct={i.change_3m_pct} /></div>
              </div>
            ))}
          </div>
          {data.chokepoints?.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 10, color: S.text3 }}>Chokepoints on India-linked trade routes:</span>
              {data.chokepoints.map((c) => (
                <span key={c.chokepoint} style={{ fontSize: 10.5, border: `1px solid ${BAND_COLOR[c.band] || S.border}`, color: BAND_COLOR[c.band] || S.text2, borderRadius: 3, padding: '1px 6px' }}>{c.display} · {c.band}</span>
              ))}
            </div>
          )}
          <div style={{ fontSize: 9.5, color: S.text3, marginTop: 8, lineHeight: 1.5 }}>
            {data.method} Hover a card for why it matters. For mandi prices, rainfall and drought, use the Agri tab.
          </div>
        </>
      )}
    </Panel>
  );
}
