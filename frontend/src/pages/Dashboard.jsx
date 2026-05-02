import { useApi } from '../hooks/useApi';
import { useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';

function formatPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatPct(v) {
  if (v == null) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

function friendlyStatus(raw) {
  const map = {
    'AWAITING_REVIEW': 'EVALUATING', 'DATA_RISK': 'DATA GAP',
    'CHALLENGED': 'REJECTED', 'STOP_BREACHED': 'STOPPED OUT',
    'TARGET_TOUCHED': 'TARGET HIT', 'NO_PRICE': 'NO DATA',
    'LIVE': 'LIVE', 'OPEN': 'ACTIVE',
  };
  return map[(raw || '').toUpperCase()] || raw;
}

function badgeClass(raw) {
  const state = (raw || '').toUpperCase();
  if (['LIVE', 'OPEN'].includes(state)) return 'live';
  if (['STOP_BREACHED', 'CHALLENGED'].includes(state)) return 'loss';
  if (['TARGET_TOUCHED'].includes(state)) return 'win';
  if (['AWAITING_REVIEW', 'DATA_RISK', 'NO_PRICE'].includes(state)) return 'warn';
  return 'open';
}

/** Session color mapping for heatmap */
function sessionColor(hour) {
  if (hour >= 0 && hour < 8) return { bg: 'rgba(255, 183, 77, 0.15)', label: 'ASIAN', accent: '#ffb74d' };
  if (hour >= 8 && hour < 13) return { bg: 'rgba(100, 181, 246, 0.15)', label: 'EUROPEAN', accent: '#64b5f6' };
  if (hour >= 13 && hour < 21) return { bg: 'rgba(129, 199, 132, 0.15)', label: 'US', accent: '#81c784' };
  return { bg: 'rgba(206, 147, 216, 0.15)', label: 'CLOSING', accent: '#ce93d8' };
}


export default function Dashboard({ ws }) {
  const { data, loading, refetch } = useApi('/api/dashboard', 10000);
  const { data: perfData } = useApi('/api/performance', 15000);
  const { data: sessionData } = useApi('/api/performance/by-session', 30000);
  const { data: missedData } = useApi('/api/signals/missed-profit?limit=5', 30000);
  const { data: tfBias } = useApi('/api/timeframe/bias', 15000);
  const navigate = useNavigate();

  const handleClose = async (id, e) => {
    e.stopPropagation();
    try {
      const res = await fetch(`http://localhost:8000/api/signals/close/single?signal_id=${id}`, { method: 'POST' });
      if (res.ok && refetch) refetch();
    } catch (err) {
      console.error('Failed to close trade:', err);
    }
  };

  const price = ws?.data?.last_price ?? data?.last_price;
  const activeSignals = ws?.data?.active_signals ?? [];
  const l2 = ws?.data?.current_l2 || {};
  const sessionTag = ws?.data?.session_tag || '—';
  const wsConfig = ws?.data?.config || {};
  const d = data || {};
  const equity = perfData?.equity_curve || [];
  const sessions = sessionData || [];
  const missed = missedData || [];
  const bias = tfBias || {};

  if (loading && !data) {
    return (
      <div>
        <div className="page-header"><h2>Dashboard</h2><p>Loading market data...</p></div>
        <div className="kpi-grid">{[...Array(6)].map((_, i) => <div key={i} className="kpi-card"><div className="skeleton" style={{ height: 60 }}></div></div>)}</div>
      </div>
    );
  }

  const pnlColor = (d.total_pnl || 0) >= 0 ? 'var(--bull)' : 'var(--bear)';

  return (
    <div>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>Quick overview — {d.symbol || 'BTCUSDT'} — Session: <strong>{sessionTag}</strong> — Fees: {wsConfig.round_trip_fee_pct || 0.09}%</p>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card accent-cyan">
          <div className="kpi-label">BTC Price</div>
          <div className="kpi-value" style={{ color: 'var(--accent-cyan)' }}>${formatPrice(price)}</div>
          <div className="kpi-note">Real-time via Binance</div>
        </div>
        <div className="kpi-card accent-bull">
          <div className="kpi-label">Active Signals</div>
          <div className="kpi-value">{activeSignals.length || d.active_signals || 0}</div>
          <div className="kpi-note">{d.total_signals || 0} total generated</div>
        </div>
        <div className={`kpi-card ${(d.total_pnl || 0) >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Total P&L</div>
          <div className="kpi-value" style={{ color: pnlColor }}>${formatPrice(d.total_pnl)}</div>
          <div className="kpi-note">On ${formatPrice(d.capital_base)} USDT capital</div>
        </div>
        <div className="kpi-card accent-blue">
          <div className="kpi-label">Win Rate</div>
          <div className="kpi-value">{d.win_rate || 0}%</div>
          <div className="kpi-note">{d.win_count || 0}W / {d.loss_count || 0}L</div>
        </div>
        <div className="kpi-card accent-purple">
          <div className="kpi-label">Current Equity</div>
          <div className="kpi-value">${formatPrice(d.current_equity)}</div>
          <div className="kpi-note">Simulated USDT portfolio</div>
        </div>
        <div className={`kpi-card ${(d.max_drawdown_pct || 0) < -5 ? 'accent-bear' : 'accent-warn'}`}>
          <div className="kpi-label">Max Drawdown</div>
          <div className="kpi-value" style={{ color: (d.max_drawdown_pct || 0) < -5 ? 'var(--bear)' : 'var(--warn)' }}>{formatPct(d.max_drawdown_pct)}</div>
          <div className="kpi-note">Peak-to-trough</div>
        </div>
      </div>

      {/* L2 Monitor + Session Info */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1.5rem' }}>
        <div className="card">
          <div className="card-title">L2 Order Book Monitor</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
            <div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Spread</div>
              <div className="font-mono" style={{
                fontSize: '1.1rem', fontWeight: 600,
                color: (l2.spread_ticks || 0) <= 3 ? 'var(--bull)' : 'var(--bear)',
              }}>
                {l2.spread_ticks != null ? `${l2.spread_ticks} ticks` : '—'}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>OBI (0.1%)</div>
              <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                {l2.obi != null ? (
                  <span style={{ color: l2.obi > 0.1 ? 'var(--bull)' : l2.obi < -0.1 ? 'var(--bear)' : 'var(--text-secondary)' }}>
                    {Number(l2.obi).toFixed(4)}
                  </span>
                ) : '—'}
              </div>
              {/* OBI Bar */}
              {l2.obi != null && (
                <div style={{
                  width: '100%', height: '6px', background: 'var(--bg-tertiary)',
                  borderRadius: '3px', marginTop: '0.5rem', overflow: 'hidden',
                  display: 'flex',
                }}>
                  <div style={{
                    width: `${Math.max(5, 50 + (l2.obi || 0) * 50)}%`,
                    height: '100%',
                    background: 'var(--bull)',
                    transition: 'width 0.3s ease',
                  }} />
                </div>
              )}
            </div>
            <div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Funding</div>
              <div className="font-mono" style={{
                fontSize: '1.1rem', fontWeight: 600,
                color: Math.abs(l2.funding_rate || 0) > 0.0003 ? 'var(--warning)' : 'var(--text-primary)',
              }}>
                {l2.funding_rate != null ? `${(l2.funding_rate * 100).toFixed(4)}%` : '—'}
              </div>
            </div>
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: '0.75rem' }}>
            Bid: <span className="font-mono text-bull">${formatPrice(l2.best_bid)}</span> |
            Ask: <span className="font-mono text-bear">${formatPrice(l2.best_ask)}</span>
          </div>
        </div>

        {/* Session Heatmap (simplified 24-cell grid) */}
        <div className="card">
          <div className="card-title">Session Heatmap (24H Win Rate)</div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)',
            gap: '3px', fontSize: '0.65rem',
          }}>
            {Array.from({ length: 24 }, (_, h) => {
              const sessInfo = sessionColor(h);
              // Find data for this hour from sessions
              const hourData = sessions.filter(s => s.hour_utc === h);
              const totalSignals = hourData.reduce((sum, s) => sum + (s.total_signals || 0), 0);
              const totalWins = hourData.reduce((sum, s) => sum + (s.wins || 0), 0);
              const wr = totalSignals > 0 ? totalWins / totalSignals : 0;
              const intensity = totalSignals > 0
                ? wr >= 0.6 ? 0.5 : wr >= 0.4 ? 0.25 : 0.1
                : 0.05;

              return (
                <div key={h} title={`${String(h).padStart(2, '0')}:00 UTC — ${sessInfo.label}\n${totalSignals} signals, ${(wr * 100).toFixed(0)}% win rate`}
                  style={{
                    background: totalSignals > 0
                      ? `rgba(${wr >= 0.5 ? '0,212,170' : '255,85,85'}, ${intensity})`
                      : 'var(--bg-tertiary)',
                    padding: '0.25rem 0', textAlign: 'center',
                    borderRadius: '3px', cursor: 'default',
                    border: `1px solid ${sessInfo.accent}22`,
                  }}>
                  <div style={{ fontWeight: 600 }}>{String(h).padStart(2, '0')}</div>
                  {totalSignals > 0 && (
                    <div style={{ color: wr >= 0.5 ? 'var(--bull)' : 'var(--bear)', fontWeight: 600 }}>
                      {(wr * 100).toFixed(0)}%
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', fontSize: '0.65rem', color: 'var(--text-secondary)' }}>
            <span style={{ color: '#ffb74d' }}>■ Asian</span>
            <span style={{ color: '#64b5f6' }}>■ European</span>
            <span style={{ color: '#81c784' }}>■ US</span>
            <span style={{ color: '#ce93d8' }}>■ Closing</span>
          </div>
        </div>
      </div>

      {/* Trend Alignment Dashboard */}
      <div className="card" style={{ marginTop: '1.5rem' }}>
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Multi-Timeframe Alignment</span>
          <span style={{
            fontSize: '0.85rem', fontWeight: 700, padding: '0.2rem 0.6rem',
            borderRadius: '4px',
            background: (bias.alignment_score || 0) >= 0.7 ? 'rgba(0,212,170,0.15)' : 'rgba(255,85,85,0.15)',
            color: (bias.alignment_score || 0) >= 0.7 ? 'var(--bull)' : 'var(--bear)',
          }}>
            Score: {(bias.alignment_score || 0).toFixed(2)} {(bias.alignment_score || 0) >= 0.7 ? '✓' : '✗'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Direction:</span>
          <span style={{
            fontWeight: 700, fontSize: '0.9rem',
            color: bias.candidate_side === 'LONG' ? 'var(--bull)' : bias.candidate_side === 'SHORT' ? 'var(--bear)' : 'var(--text-secondary)',
          }}>
            {bias.candidate_side || 'NONE'}
          </span>
          {bias.pullback_complete && (
            <span style={{ fontSize: '0.7rem', background: 'rgba(0,212,170,0.15)', color: 'var(--bull)', padding: '0.1rem 0.4rem', borderRadius: '3px' }}>PULLBACK ✓</span>
          )}
        </div>
        {[
          { label: 'Daily (20 EMA)', value: bias.daily_bias, side: bias.candidate_side },
          { label: '4H Structure', value: bias.four_h_direction, side: bias.candidate_side },
          { label: '1H RSI', value: bias.one_h_rsi ? `${bias.one_h_rsi.toFixed(1)}` : '—', isRsi: true, rsiVal: bias.one_h_rsi || 50 },
          { label: '15m Momentum', value: bias.momentum_ok ? 'ALIGNED' : 'FLAT', isMomentum: true, ok: bias.momentum_ok },
        ].map((tf, i) => {
          let barColor = 'var(--bg-tertiary)';
          let textColor = 'var(--text-secondary)';
          if (tf.isRsi) {
            const rsi = tf.rsiVal;
            const inZone = bias.candidate_side === 'LONG' ? (rsi >= 45 && rsi <= 65) : (rsi >= 35 && rsi <= 55);
            barColor = inZone ? 'var(--bull)' : rsi > 75 || rsi < 25 ? 'var(--bear)' : 'var(--bg-tertiary)';
            textColor = inZone ? 'var(--bull)' : 'var(--text-secondary)';
          } else if (tf.isMomentum) {
            barColor = tf.ok ? 'var(--bull)' : 'var(--bg-tertiary)';
            textColor = tf.ok ? 'var(--bull)' : 'var(--text-secondary)';
          } else {
            const aligned = (bias.candidate_side === 'LONG' && tf.value === 'BULLISH') || (bias.candidate_side === 'SHORT' && tf.value === 'BEARISH');
            const opposed = (bias.candidate_side === 'LONG' && tf.value === 'BEARISH') || (bias.candidate_side === 'SHORT' && tf.value === 'BULLISH');
            barColor = aligned ? 'var(--bull)' : opposed ? 'var(--bear)' : 'var(--bg-tertiary)';
            textColor = aligned ? 'var(--bull)' : opposed ? 'var(--bear)' : 'var(--text-secondary)';
          }
          return (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
              <div style={{ width: '100px', fontSize: '0.75rem', color: 'var(--text-secondary)', flexShrink: 0 }}>{tf.label}</div>
              <div style={{ flex: 1, height: '8px', background: 'var(--bg-tertiary)', borderRadius: '4px', overflow: 'hidden' }}>
                <div style={{
                  width: tf.isRsi ? `${Math.min(100, tf.rsiVal)}%` : barColor !== 'var(--bg-tertiary)' ? '100%' : '0%',
                  height: '100%', background: barColor, borderRadius: '4px',
                  transition: 'width 0.5s ease, background 0.3s ease',
                }} />
              </div>
              <div style={{ width: '80px', fontSize: '0.75rem', fontWeight: 600, color: textColor, textAlign: 'right' }}>
                {tf.isRsi ? tf.value : tf.value || '—'}
              </div>
            </div>
          );
        })}
        <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
          Last updated: {bias.last_updated ? new Date(bias.last_updated).toLocaleTimeString() : '—'}
          {' | '} Buffers: 1d={bias.buffers_loaded?.daily || 0} 4h={bias.buffers_loaded?.['4h'] || 0} 1h={bias.buffers_loaded?.['1h'] || 0} 15m={bias.buffers_loaded?.['15m'] || 0}
        </div>
      </div>

      {/* Equity Curve */}
      {equity.length > 1 && (
        <div className="chart-card" style={{ marginTop: '1.5rem', marginBottom: '1.5rem' }}>
          <div className="chart-title">Equity Curve — ${formatPrice(d.capital_base || 1000)} USDT Capital</div>
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={equity}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00d4aa" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="#00d4aa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="point_time" tick={false} stroke="var(--border-primary)" />
              <YAxis domain={['auto', 'auto']} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} stroke="var(--border-primary)" />
              <Tooltip contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8, color: 'var(--text-primary)' }} />
              <Area type="monotone" dataKey="equity_value" stroke="#00d4aa" strokeWidth={2} fill="url(#eqGrad)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Active Signals Preview */}
      {activeSignals.length > 0 && (
        <div className="card">
          <div className="card-title">Active Signals</div>
          <div className="signal-grid">
            {activeSignals.slice(0, 3).map(sig => (
              <div
                key={sig.signal_id}
                className={`signal-card ${sig.side === 'LONG' ? 'bull' : sig.side === 'SHORT' ? 'bear' : 'flat'}`}
                onClick={() => navigate(`/signals/${sig.signal_id}`)}
              >
                <div className="signal-header">
                  <div className="signal-title-group">
                    <h3>{sig.side} — {sig.regime?.replace(/_/g, ' ')}</h3>
                    <div className="signal-subtitle">{sig.signal_id?.slice(0, 8)}</div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                    <span className={`badge ${sig.is_valid ? 'live' : badgeClass(sig.live_state || sig.status)}`}>
                      {sig.is_valid && <span className="badge-dot"></span>}
                      {sig.is_valid ? 'LIVE' : friendlyStatus(sig.live_state || sig.status)}
                    </span>
                    <button
                      onClick={(e) => handleClose(sig.signal_id, e)}
                      style={{
                        padding: '0.1rem 0.6rem', fontSize: '0.65rem',
                        background: 'transparent', border: '1px solid var(--text-secondary)',
                        color: 'var(--text-primary)', borderRadius: '4px',
                        cursor: 'pointer', textTransform: 'uppercase', fontWeight: 'bold'
                      }}
                    >
                      Close ✕
                    </button>
                  </div>
                </div>
                <div className="signal-metrics">
                  <div className="signal-metric">
                    <div className="signal-metric-label">Entry</div>
                    <div className="signal-metric-value">${formatPrice(sig.entry_price)}</div>
                  </div>
                  <div className="signal-metric">
                    <div className="signal-metric-label">Stop Loss</div>
                    <div className="signal-metric-value text-bear">${formatPrice(sig.stop_price)}</div>
                  </div>
                  <div className="signal-metric">
                    <div className="signal-metric-label">Target</div>
                    <div className="signal-metric-value text-bull">${formatPrice(sig.take_profit)}</div>
                  </div>
                  <div className="signal-metric">
                    <div className="signal-metric-label">R:R</div>
                    <div className="signal-metric-value">{sig.risk_reward || '—'}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Missed Profit Alerts */}
      {missed.length > 0 && (
        <div className="card" style={{ marginTop: '1rem', borderLeft: '3px solid var(--warning)' }}>
          <div className="card-title" style={{ color: 'var(--warning)' }}>⚠ Recent Missed Profits</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
            Expired signals where MFE exceeded 2× target — consider adjusting timeouts
          </div>
          {missed.slice(0, 3).map(m => (
            <div key={m.signal_id} style={{
              display: 'flex', gap: '1rem', padding: '0.4rem 0',
              borderBottom: '1px solid var(--border)', fontSize: '0.8rem',
            }}>
              <span className="font-mono" style={{ color: 'var(--accent-cyan)' }}>{m.signal_id?.slice(0, 8)}</span>
              <span className={`badge ${m.side?.toLowerCase()}`}>{m.side}</span>
              <span className="text-bull">MFE: {formatPct(m.mfe_percent)}</span>
              <span className="text-muted">{m.session_tag}</span>
            </div>
          ))}
        </div>
      )}

      {/* Health Status */}
      <div className="card" style={{ marginTop: '1rem' }}>
        <div className="card-title">System Health</div>
        <div style={{ display: 'flex', gap: '2rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          <span>Data: <strong className={d.health?.usable ? 'text-bull' : 'text-bear'}>{d.health?.usable ? 'Healthy' : d.health?.reason}</strong></span>
          <span>Ticks: <strong>{d.health?.tick_count || 0}</strong></span>
          <span>Updated: <strong>{d.generated_at ? new Date(d.generated_at).toLocaleTimeString() : '—'}</strong></span>
        </div>
      </div>
    </div>
  );
}
