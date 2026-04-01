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

/** Map internal DB states to user-friendly labels */
function friendlyStatus(raw) {
  const map = {
    'AWAITING_REVIEW': 'EVALUATING',
    'DATA_RISK': 'DATA GAP',
    'CHALLENGED': 'REJECTED',
    'STOP_BREACHED': 'STOPPED OUT',
    'TARGET_TOUCHED': 'TARGET HIT',
    'NO_PRICE': 'NO DATA',
    'LIVE': 'LIVE',
    'OPEN': 'ACTIVE',
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


export default function Dashboard({ ws }) {
  const { data, loading, refetch } = useApi('/api/dashboard', 10000);
  const { data: perfData } = useApi('/api/performance', 15000);
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
  const d = data || {};
  const equity = perfData?.equity_curve || [];

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
        <p>Quick overview — {d.symbol || 'BTCUSDT'}</p>
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

      {/* Equity Curve */}
      {equity.length > 1 && (
        <div className="chart-card" style={{ marginBottom: '1.5rem' }}>
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
                        padding: '0.1rem 0.6rem',
                        fontSize: '0.65rem',
                        background: 'transparent',
                        border: '1px solid var(--text-secondary)',
                        color: 'var(--text-primary)',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        textTransform: 'uppercase',
                        fontWeight: 'bold'
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
