import { useApi } from '../hooks/useApi';
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import ScannerStatusPanel from '../ui/scanner/ScannerStatusPanel';

function formatPrice(v) {
  if (v == null) return '--';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatPct(v) {
  if (v == null) return '--';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

function sessionColor(hour) {
  if (hour >= 0 && hour < 8) return { label: 'ASIAN', accent: '#ffb74d' };
  if (hour >= 8 && hour < 13) return { label: 'EUROPEAN', accent: '#64b5f6' };
  if (hour >= 13 && hour < 21) return { label: 'US', accent: '#81c784' };
  return { label: 'CLOSING', accent: '#ce93d8' };
}

export default function Dashboard({ ws }) {
  const { data, loading } = useApi('/api/dashboard', 10000);
  const { data: perfData } = useApi('/api/performance', 15000);
  const { data: sessionData } = useApi('/api/performance/by-session', 30000);
  const { data: missedData } = useApi('/api/signals/missed-profit?limit=5', 30000);
  const { data: tfBias } = useApi('/api/timeframe/bias', 15000);
  const { data: agentEvents } = useApi('/api/agents/events?limit=300', 10000);

  const price = ws?.data?.last_price ?? data?.last_price;
  const activeSignals = ws?.data?.active_signals ?? [];
  const l2 = ws?.data?.current_l2 || {};
  const d = data || {};
  const equity = perfData?.equity_curve || [];
  const sessions = sessionData || [];
  const missed = missedData || [];
  const bias = tfBias || {};

  if (loading && !data) {
    return (
      <div>
        <div className="page-header">
          <h2>Dashboard</h2>
          <p>Loading market data...</p>
        </div>
        <div className="kpi-grid">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="kpi-card">
              <div className="skeleton" style={{ height: 60 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const pnlColor = (d.total_pnl || 0) >= 0 ? 'var(--bull)' : 'var(--bear)';

  return (
    <div>
      <div className="page-header">
        <h2>Dashboard</h2>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card accent-cyan">
          <div className="kpi-label">BTC Price</div>
          <div className="kpi-value" style={{ color: 'var(--accent-cyan)' }}>${formatPrice(price)}</div>
        </div>
        <div className="kpi-card accent-bull">
          <div className="kpi-label">Active Signals</div>
          <div className="kpi-value">{activeSignals.length || d.active_signals || 0}</div>
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
        </div>
        <div className={`kpi-card ${(d.max_drawdown_pct || 0) < -5 ? 'accent-bear' : 'accent-warn'}`}>
          <div className="kpi-label">Max Drawdown</div>
          <div className="kpi-value" style={{ color: (d.max_drawdown_pct || 0) < -5 ? 'var(--bear)' : 'var(--warn)' }}>
            {formatPct(d.max_drawdown_pct)}
          </div>
          <div className="kpi-note">Peak-to-trough</div>
        </div>
      </div>

      <ScannerStatusPanel events={agentEvents} bias={bias} compact />

      {equity.length > 1 && (
        <div className="chart-card dashboard-equity-card">
          <div className="chart-title">Equity Curve - ${formatPrice(d.capital_base || 1000)} USDT Capital</div>
          <ResponsiveContainer width="100%" height={320}>
            <AreaChart data={equity}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#2ad9ec" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#27da8f" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="point_time" tick={false} stroke="var(--border-primary)" />
              <YAxis domain={['auto', 'auto']} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} stroke="var(--border-primary)" />
              <Tooltip contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 12, color: 'var(--text-primary)' }} />
              <Area type="monotone" dataKey="equity_value" stroke="#2ad9ec" strokeWidth={2.5} fill="url(#eqGrad)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="dashboard-insight-grid">
        <div className="card">
          <div className="card-title">L2 Order Book Monitor</div>
          <div className="dashboard-metric-row">
            <div>
              <div className="dashboard-mini-label">Spread</div>
              <div
                className="font-mono dashboard-mini-value"
                style={{ color: (l2.spread_ticks || 0) <= 3 ? 'var(--bull)' : 'var(--bear)' }}
              >
                {l2.spread_ticks != null ? `${l2.spread_ticks} ticks` : '--'}
              </div>
            </div>
            <div>
              <div className="dashboard-mini-label">OBI (0.1%)</div>
              <div className="font-mono dashboard-mini-value">
                {l2.obi != null ? (
                  <span style={{ color: l2.obi > 0.1 ? 'var(--bull)' : l2.obi < -0.1 ? 'var(--bear)' : 'var(--text-secondary)' }}>
                    {Number(l2.obi).toFixed(4)}
                  </span>
                ) : '--'}
              </div>
              {l2.obi != null && (
                <div className="dashboard-bar">
                  <div
                    style={{
                      width: `${Math.max(5, 50 + (l2.obi || 0) * 50)}%`,
                      height: '100%',
                      background: 'var(--bull)',
                      borderRadius: '999px',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
              )}
            </div>
            <div>
              <div className="dashboard-mini-label">Funding</div>
              <div
                className="font-mono dashboard-mini-value"
                style={{ color: Math.abs(l2.funding_rate || 0) > 0.0003 ? 'var(--warning)' : 'var(--text-primary)' }}
              >
                {l2.funding_rate != null ? `${(l2.funding_rate * 100).toFixed(4)}%` : '--'}
              </div>
            </div>
          </div>
          <div className="orderbook-quote-line">
            Bid: <span className="font-mono text-bull">${formatPrice(l2.best_bid)}</span>
            <span>|</span>
            Ask: <span className="font-mono text-bear">${formatPrice(l2.best_ask)}</span>
          </div>
        </div>

        <div className="card">
          <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Multi-Timeframe Alignment</span>
            <span className={(bias.alignment_score || 0) >= 0.7 ? 'badge live' : 'badge loss'}>
              Score: {(bias.alignment_score || 0).toFixed(2)}
            </span>
          </div>
          <div className="alignment-direction">
            <span>Direction:</span>
            <strong
              style={{
                color: bias.candidate_side === 'LONG' ? 'var(--bull)' : bias.candidate_side === 'SHORT' ? 'var(--bear)' : 'var(--text-secondary)',
              }}
            >
              {bias.candidate_side || 'NONE'}
            </strong>
            {bias.pullback_complete && <span className="badge live">PULLBACK</span>}
          </div>
          {[
            { label: 'Daily (20 EMA)', value: bias.daily_bias, side: bias.candidate_side },
            { label: '4H Structure', value: bias.four_h_direction, side: bias.candidate_side },
            { label: '1H RSI', value: bias.one_h_rsi ? `${bias.one_h_rsi.toFixed(1)}` : '--', isRsi: true, rsiVal: bias.one_h_rsi || 50 },
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
              <div key={i} className="timeframe-row">
                <div className="timeframe-label">{tf.label}</div>
                <div className="timeframe-track">
                  <div
                    style={{
                      width: tf.isRsi ? `${Math.min(100, tf.rsiVal)}%` : barColor !== 'var(--bg-tertiary)' ? '100%' : '0%',
                      height: '100%',
                      background: barColor,
                      borderRadius: '999px',
                      transition: 'width 0.5s ease, background 0.3s ease',
                    }}
                  />
                </div>
                <div className="timeframe-value" style={{ color: textColor }}>
                  {tf.isRsi ? tf.value : tf.value || '--'}
                </div>
              </div>
            );
          })}
          <div className="alignment-updated">
            Last updated: {bias.last_updated ? new Date(bias.last_updated).toLocaleTimeString() : '--'}
            {' | '} Buffers: 1d={bias.buffers_loaded?.daily || 0} 4h={bias.buffers_loaded?.['4h'] || 0} 1h={bias.buffers_loaded?.['1h'] || 0} 15m={bias.buffers_loaded?.['15m'] || 0}
          </div>
        </div>
      </div>

      <div className="card dashboard-session-card">
        <div className="card-title">Session Heatmap (24H Win Rate)</div>
        <div className="session-heatmap">
          {Array.from({ length: 24 }, (_, h) => {
            const sessInfo = sessionColor(h);
            const hourData = sessions.filter(s => s.hour_utc === h);
            const totalSignals = hourData.reduce((sum, s) => sum + (s.total_signals || 0), 0);
            const totalWins = hourData.reduce((sum, s) => sum + (s.wins || 0), 0);
            const wr = totalSignals > 0 ? totalWins / totalSignals : 0;
            const intensity = totalSignals > 0
              ? wr >= 0.6 ? 0.5 : wr >= 0.4 ? 0.25 : 0.1
              : 0.05;

            return (
              <div
                key={h}
                title={`${String(h).padStart(2, '0')}:00 UTC - ${sessInfo.label}\n${totalSignals} signals, ${(wr * 100).toFixed(0)}% win rate`}
                style={{
                  background: totalSignals > 0
                    ? `rgba(${wr >= 0.5 ? '39,218,143' : '255,94,100'}, ${intensity})`
                    : 'var(--bg-tertiary)',
                  border: `1px solid ${sessInfo.accent}22`,
                }}
              >
                <div style={{ fontWeight: 700 }}>{String(h).padStart(2, '0')}</div>
                {totalSignals > 0 && (
                  <div style={{ color: wr >= 0.5 ? 'var(--bull)' : 'var(--bear)', fontWeight: 700 }}>
                    {(wr * 100).toFixed(0)}%
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="heatmap-legend">
          <span style={{ color: '#ffb74d' }}>Asian</span>
          <span style={{ color: '#64b5f6' }}>European</span>
          <span style={{ color: '#81c784' }}>US</span>
          <span style={{ color: '#ce93d8' }}>Closing</span>
        </div>
      </div>

      {missed.length > 0 && (
        <div className="card dashboard-alert-card">
          <div className="card-title" style={{ color: 'var(--warning)' }}>Recent Missed Profits</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
            Expired signals where MFE exceeded 2x target - consider adjusting timeouts.
          </div>
          {missed.slice(0, 3).map(m => (
            <div key={m.signal_id} className="missed-profit-row">
              <span className="font-mono" style={{ color: 'var(--accent-cyan)' }}>{m.signal_id?.slice(0, 8)}</span>
              <span className={`badge ${m.side?.toLowerCase()}`}>{m.side}</span>
              <span className="text-bull">MFE: {formatPct(m.mfe_percent)}</span>
              <span className="text-muted">{m.session_tag}</span>
            </div>
          ))}
        </div>
      )}

      <div className="card dashboard-health-card">
        <div className="card-title">System Health</div>
        <div className="dashboard-health-grid">
          <span>Data: <strong className={d.health?.usable ? 'text-bull' : 'text-bear'}>{d.health?.usable ? 'Healthy' : d.health?.reason}</strong></span>
          <span>Ticks: <strong>{d.health?.tick_count || 0}</strong></span>
          <span>Updated: <strong>{d.generated_at ? new Date(d.generated_at).toLocaleTimeString() : '--'}</strong></span>
        </div>
      </div>
    </div>
  );
}
