import { useState } from 'react';
import { useApi } from '../hooks/useApi';

function formatPct(v, decimals = 2) {
  if (v == null || isNaN(v)) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(decimals)}%`;
}

function formatPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

const LOOKBACK_OPTIONS = [
  { label: '7D', value: 7 },
  { label: '30D', value: 30 },
  { label: '90D', value: 90 },
  { label: 'ALL', value: 0 },
];

export default function Performance() {
  const [lookback, setLookback] = useState(0);
  const { data: summary, loading: loadingSummary } = useApi(
    `/api/performance/summary?lookback_days=${lookback}`, 15000
  );
  const { data: curveData, loading: loadingCurve } = useApi(
    `/api/equity-curve?lookback_days=${lookback}&max_points=500`, 15000
  );
  const { data: sessionData } = useApi('/api/performance/by-session', 30000);
  const { data: monthlyData } = useApi('/api/performance/monthly-breakdown', 30000);
  const { data: missedData } = useApi('/api/signals/missed-profit?limit=10', 30000);
  const { data: configData } = useApi('/api/system/config', 60000);
  const { data: regimeData } = useApi('/api/performance/regime-breakdown', 30000);

  const perf = summary || {};
  const curve = curveData?.curve || [];
  const sessions = sessionData || [];
  const monthly = monthlyData || [];
  const missed = missedData || [];
  const config = configData || {};
  const regimes = (regimeData || []).filter(r => !r.error);

  const totalNet = perf.total_net_pnl_percent || 0;
  const totalGross = perf.total_gross_pnl_percent || 0;
  const profitFactor = perf.profit_factor || 0;
  const expectancy = perf.expectancy_per_trade || 0;

  // Compute max drawdown from curve
  let maxDrawdown = 0;
  for (const pt of curve) {
    const dd = pt.drawdown_percent || 0;
    if (dd < maxDrawdown) maxDrawdown = dd;
  }

  return (
    <div>
      <div className="page-header">
        <h2>Performance Analytics</h2>
        <p>Fee-aware performance with regime analysis — Fees: {config.round_trip_fee_pct || 0.09}% round-trip</p>
      </div>

      {/* Timeframe Selector */}
      <div className="filter-bar" style={{ marginBottom: '1.5rem' }}>
        <span style={{ fontSize: '0.75rem', opacity: 0.6, marginRight: '0.5rem' }}>Lookback:</span>
        {LOOKBACK_OPTIONS.map(opt => (
          <button key={opt.value}
            className={`filter-btn ${lookback === opt.value ? 'active' : ''}`}
            onClick={() => setLookback(opt.value)}>
            {opt.label}
          </button>
        ))}
      </div>

      {/* KPI Cards */}
      <div className="kpi-grid" style={{ marginBottom: '1.5rem' }}>
        <div className={`kpi-card ${totalNet >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Net P&L (after fees)</div>
          <div className="kpi-value" style={{ color: totalNet >= 0 ? 'var(--bull)' : 'var(--bear)' }}>
            {formatPct(totalNet * 100, 3)}
          </div>
          <div className="kpi-note">Gross: {formatPct(totalGross * 100, 3)}</div>
        </div>
        <div className={`kpi-card ${profitFactor >= 1 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Profit Factor</div>
          <div className="kpi-value" style={{ color: profitFactor >= 1 ? 'var(--bull)' : 'var(--bear)' }}>
            {profitFactor.toFixed(2)}
          </div>
          <div className="kpi-note">Gross wins / Gross losses</div>
        </div>
        <div className={`kpi-card ${expectancy >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Expectancy / Trade</div>
          <div className="kpi-value" style={{ color: expectancy >= 0 ? 'var(--bull)' : 'var(--bear)' }}>
            {formatPct(expectancy * 100, 4)}
          </div>
          <div className="kpi-note">(WR × AvgWin) − (LR × AvgLoss)</div>
        </div>
        <div className="kpi-card accent-purple">
          <div className="kpi-label">Max Drawdown</div>
          <div className="kpi-value text-bear">{formatPct(maxDrawdown, 3)}</div>
          <div className="kpi-note">From equity curve peak</div>
        </div>
      </div>

      {/* Win Rate Row */}
      <div className="kpi-grid" style={{ marginBottom: '1.5rem' }}>
        <div className="kpi-card">
          <div className="kpi-label">Gross Win Rate</div>
          <div className="kpi-value">{formatPct(perf.gross_win_rate * 100)}</div>
          <div className="kpi-note">{perf.win_count || 0}W / {perf.loss_count || 0}L / {perf.expired_count || 0}E</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Net Win Rate</div>
          <div className="kpi-value" style={{ color: (perf.net_win_rate || 0) >= 0.4 ? 'var(--bull)' : 'var(--bear)' }}>
            {formatPct((perf.net_win_rate || 0) * 100)}
          </div>
          <div className="kpi-note">Wins where net P&L &gt; 0</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Avg Win</div>
          <div className="kpi-value text-bull">{formatPct((perf.avg_win_percent || 0) * 100, 4)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Avg Loss</div>
          <div className="kpi-value text-bear">{formatPct((perf.avg_loss_percent || 0) * 100, 4)}</div>
        </div>
      </div>

      {/* Equity Curve (text visualization) */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ marginBottom: '1rem' }}>Equity Curve (Net P&L)</h3>
        {loadingCurve ? (
          <div className="skeleton" style={{ height: 200 }}></div>
        ) : curve.length === 0 ? (
          <p style={{ color: 'var(--text-secondary)', textAlign: 'center' }}>No trade data yet.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <div style={{
              display: 'flex', alignItems: 'flex-end', gap: '2px',
              height: '180px', padding: '0.5rem 0'
            }}>
              {curve.map((pt, i) => {
                const cumNet = pt.cumulative_net_pnl || 0;
                const maxAbs = Math.max(
                  ...curve.map(p => Math.abs(p.cumulative_net_pnl || 0)),
                  0.001
                );
                const h = Math.abs(cumNet) / maxAbs * 150;
                const isUp = cumNet >= 0;
                return (
                  <div key={i} title={`Trade ${i + 1}: Net ${formatPct(cumNet * 100, 3)}`}
                    style={{
                      width: Math.max(2, 400 / curve.length),
                      height: `${Math.max(2, h)}px`,
                      background: isUp
                        ? 'linear-gradient(to top, rgba(0,212,170,0.3), rgba(0,212,170,0.8))'
                        : 'linear-gradient(to top, rgba(255,85,85,0.3), rgba(255,85,85,0.8))',
                      borderRadius: '1px',
                      alignSelf: isUp ? 'flex-end' : 'flex-start',
                      cursor: 'pointer',
                    }} />
                );
              })}
            </div>
            <div style={{
              borderTop: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between',
              fontSize: '0.7rem', color: 'var(--text-secondary)', paddingTop: '0.25rem'
            }}>
              <span>{curve.length > 0 ? curve[0].point_time?.slice(0, 10) : ''}</span>
              <span>{curveData?.downsampled ? `${curveData.displayed_points}/${curveData.total_trades} pts (LTTB)` : `${curve.length} trades`}</span>
              <span>{curve.length > 0 ? curve[curve.length - 1].point_time?.slice(0, 10) : ''}</span>
            </div>
          </div>
        )}
      </div>

      {/* Monthly Breakdown */}
      {monthly.length > 0 && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ marginBottom: '1rem' }}>Monthly Breakdown</h3>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Month</th>
                  <th>Signals</th>
                  <th>Win Rate</th>
                  <th>Net P&L</th>
                  <th>W/L</th>
                </tr>
              </thead>
              <tbody>
                {monthly.map(m => (
                  <tr key={m.year_month}>
                    <td className="font-mono">{m.year_month}</td>
                    <td>{m.signals}</td>
                    <td className={m.win_rate >= 0.5 ? 'text-bull' : 'text-bear'}>
                      {formatPct(m.win_rate * 100)}
                    </td>
                    <td className={`font-mono ${m.net_pnl_percent >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {formatPct(m.net_pnl_percent * 100, 4)}
                    </td>
                    <td className="text-muted">{m.wins}W / {m.losses}L</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Session / Regime Performance */}
      {sessions.length > 0 && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ marginBottom: '1rem' }}>Session Performance (Hourly)</h3>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Hour (UTC)</th>
                  <th>Side</th>
                  <th>Regime</th>
                  <th>Signals</th>
                  <th>Win Rate</th>
                  <th>Avg MFE</th>
                  <th>Avg MAE</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s, i) => (
                  <tr key={i}>
                    <td className="font-mono">{String(s.hour_utc).padStart(2, '0')}:00</td>
                    <td><span className={`badge ${s.side?.toLowerCase()}`}>{s.side}</span></td>
                    <td><span className="badge">{s.regime}</span></td>
                    <td>{s.total_signals}</td>
                    <td className={s.win_rate >= 0.5 ? 'text-bull' : 'text-bear'}>
                      {formatPct(s.win_rate * 100)}
                    </td>
                    <td className="font-mono text-bull">{formatPct(s.avg_mfe)}</td>
                    <td className="font-mono text-bear">{formatPct(s.avg_mae)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Missed Profit Signals */}
      {missed.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: '1rem', color: 'var(--warning)' }}>⚠ Missed Profit Signals</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
            EXPIRED signals where MFE exceeded 2× target distance — potential signal management issues.
          </p>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>Side</th>
                  <th>Session</th>
                  <th>MFE %</th>
                  <th>Target %</th>
                  <th>Entry</th>
                </tr>
              </thead>
              <tbody>
                {missed.map(m => (
                  <tr key={m.signal_id}>
                    <td className="font-mono" style={{ color: 'var(--accent-cyan)' }}>{m.signal_id?.slice(0, 8)}</td>
                    <td><span className={`badge ${m.side?.toLowerCase()}`}>{m.side}</span></td>
                    <td className="text-muted">{m.session_tag}</td>
                    <td className="font-mono text-bull">{formatPct(m.mfe_percent)}</td>
                    <td className="font-mono text-muted">
                      {formatPct(Math.abs((m.take_profit - m.entry_price) / m.entry_price * 100))}
                    </td>
                    <td className="font-mono">${formatPrice(m.entry_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Regime Performance */}
      {regimes.length > 0 && (
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="card-title">Volatility Regime Performance</div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(regimes.length, 4)}, 1fr)`, gap: '1rem' }}>
            {regimes.map((r, i) => {
              const wr = (r.win_rate || 0) * 100;
              const netPnl = r.avg_net_pnl_pct || 0;
              const barHeight = Math.max(10, wr);
              return (
                <div key={i} style={{
                  background: 'var(--bg-secondary)', borderRadius: '8px', padding: '1rem',
                  border: '1px solid var(--border)', textAlign: 'center',
                }}>
                  <div style={{ fontSize: '0.8rem', fontWeight: 700, marginBottom: '0.75rem', color: 'var(--accent-cyan)' }}>
                    {r.regime || 'UNKNOWN'}
                  </div>
                  <div style={{
                    width: '100%', height: '60px', display: 'flex', alignItems: 'flex-end',
                    justifyContent: 'center', marginBottom: '0.5rem',
                  }}>
                    <div style={{
                      width: '40px', height: `${barHeight}%`, borderRadius: '4px 4px 0 0',
                      background: wr >= 40 ? 'var(--bull)' : wr >= 30 ? 'var(--warning)' : 'var(--bear)',
                      transition: 'height 0.5s ease',
                    }} />
                  </div>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, color: wr >= 40 ? 'var(--bull)' : 'var(--bear)' }}>
                    {wr.toFixed(1)}%
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>Win Rate</div>
                  <div style={{
                    fontSize: '0.85rem', fontWeight: 600, marginTop: '0.5rem',
                    color: netPnl >= 0 ? 'var(--bull)' : 'var(--bear)',
                  }}>
                    {formatPct(netPnl, 3)}
                  </div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>Avg Net P&L</div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                    {r.total} trades | ~{Math.round(r.avg_duration_s || 0)}s avg
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Missed Profit Tracker */}
      {missed.length > 0 && (
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="card-title">Missed Profit Tracker — MFE vs Actual P&L</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
            The gap between green (MFE) and red/gray (actual) shows captured vs missed profit
          </div>
          {missed.slice(0, 15).map((m, i) => {
            const mfe = Math.abs(m.mfe_percent || 0);
            const actual = (m.net_pnl_percent || m.gross_pnl_percent || 0) * 100;
            const maxBar = Math.max(mfe, Math.abs(actual), 0.01);
            const mfePct = (mfe / maxBar) * 100;
            const actualPct = (Math.abs(actual) / maxBar) * 100;
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
                <div className="font-mono" style={{ width: '60px', fontSize: '0.7rem', color: 'var(--accent-cyan)' }}>
                  {(m.signal_id || '').slice(0, 8)}
                </div>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  <div style={{ height: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', overflow: 'hidden' }}>
                    <div style={{ width: `${mfePct}%`, height: '100%', background: 'var(--bull)', borderRadius: '3px' }} />
                  </div>
                  <div style={{ height: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', overflow: 'hidden' }}>
                    <div style={{
                      width: `${actualPct}%`, height: '100%', borderRadius: '3px',
                      background: actual >= 0 ? 'var(--bull)' : 'var(--bear)', opacity: 0.6,
                    }} />
                  </div>
                </div>
                <div style={{ width: '120px', textAlign: 'right', fontSize: '0.7rem' }}>
                  <span className="text-bull">{formatPct(mfe)}</span>
                  <span style={{ color: 'var(--text-secondary)', margin: '0 0.2rem' }}>vs</span>
                  <span className={actual >= 0 ? 'text-bull' : 'text-bear'}>{formatPct(actual)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
