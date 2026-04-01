import { useApi } from '../hooks/useApi';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, PieChart, Pie
} from 'recharts';

function formatPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatPct(v) {
  if (v == null) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

export default function Performance() {
  const { data, loading } = useApi('/api/performance', 15000);

  if (loading && !data) {
    return (
      <div>
        <div className="page-header"><h2>Performance Analytics</h2><p>Loading performance data...</p></div>
        <div className="kpi-grid">{[...Array(4)].map((_, i) => <div key={i} className="kpi-card"><div className="skeleton" style={{ height: 60 }}></div></div>)}</div>
      </div>
    );
  }

  const d = data || {};
  const pnlColor = (d.total_pnl || 0) >= 0 ? 'var(--bull)' : 'var(--bear)';
  const equity = d.equity_curve || [];
  const tradeBars = d.trade_pnl_bars || [];
  const pieData = [
    { name: 'Wins', value: d.win_count || 0, color: '#10b981' },
    { name: 'Losses', value: d.loss_count || 0, color: '#ef4444' },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>Performance Analytics</h2>
        <p>Simulated trading on ${formatPrice(d.capital_base)} USDT capital &bull; {d.position_size_pct || 10}% position size (${formatPrice(d.position_size || 100)}/trade)</p>
      </div>

      <div className="kpi-grid">
        <div className={`kpi-card ${(d.total_pnl || 0) >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Total P&L</div>
          <div className="kpi-value" style={{ color: pnlColor }}>${formatPrice(d.total_pnl)}</div>
          <div className="kpi-note">{formatPct(d.total_pnl_pct)}</div>
        </div>
        <div className="kpi-card accent-blue">
          <div className="kpi-label">Win Rate</div>
          <div className="kpi-value">{d.win_rate || 0}%</div>
          <div className="kpi-note">{d.win_count || 0}W / {d.loss_count || 0}L ({d.total_trades || 0} total)</div>
        </div>
        <div className="kpi-card accent-purple">
          <div className="kpi-label">Current Equity</div>
          <div className="kpi-value">${formatPrice(d.current_equity)}</div>
          <div className="kpi-note">From ${formatPrice(d.capital_base)} USDT</div>
        </div>
        <div className={`kpi-card ${(d.max_drawdown_pct || 0) < -5 ? 'accent-bear' : 'accent-warn'}`}>
          <div className="kpi-label">Max Drawdown</div>
          <div className="kpi-value" style={{ color: 'var(--bear)' }}>{formatPct(d.max_drawdown_pct)}</div>
          <div className="kpi-note">Peak-to-trough</div>
        </div>
      </div>

      {/* Secondary metrics */}
      <div className="kpi-grid" style={{ marginBottom: '1.5rem' }}>
        <div className="kpi-card accent-bull">
          <div className="kpi-label">Best Trade</div>
          <div className="kpi-value text-bull">{formatPct(d.best_trade_pnl_pct)}</div>
        </div>
        <div className="kpi-card accent-bear">
          <div className="kpi-label">Worst Trade</div>
          <div className="kpi-value text-bear">{formatPct(d.worst_trade_pnl_pct)}</div>
        </div>
        <div className="kpi-card accent-bull">
          <div className="kpi-label">Avg Win</div>
          <div className="kpi-value text-bull">{formatPct(d.avg_win_pnl_pct)}</div>
        </div>
        <div className="kpi-card accent-bear">
          <div className="kpi-label">Avg Loss</div>
          <div className="kpi-value text-bear">{formatPct(d.avg_loss_pnl_pct)}</div>
        </div>
      </div>

      <div className="charts-grid">
        {/* Equity Curve */}
        <div className="chart-card">
          <div className="chart-title">Equity Curve</div>
          {equity.length > 1 ? (
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={equity}>
                <defs>
                  <linearGradient id="eqGradPerf" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4aa" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#00d4aa" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="point_time" tick={false} stroke="var(--border-primary)" />
                <YAxis domain={['auto', 'auto']} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} stroke="var(--border-primary)" />
                <Tooltip
                  contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8, color: 'var(--text-primary)' }}
                  formatter={(v, name) => [`$${Number(v).toLocaleString('en-US')}`, name === 'equity_value' ? 'Equity' : name]}
                />
                <Area type="monotone" dataKey="equity_value" stroke="#00d4aa" strokeWidth={2} fill="url(#eqGradPerf)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>No equity data yet</div>
          )}
        </div>

        {/* Win/Loss Distribution */}
        <div className="chart-card">
          <div className="chart-title">Win / Loss Distribution</div>
          {(d.win_count || 0) + (d.loss_count || 0) > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" innerRadius={70} outerRadius={100} paddingAngle={3} dataKey="value" label={({ name, value }) => `${name}: ${value}`}>
                  {pieData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                </Pie>
                <Tooltip contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8, color: 'var(--text-primary)' }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>No trades yet</div>
          )}
        </div>
      </div>

      {/* Trade-by-Trade PnL */}
      <div className="chart-card">
        <div className="chart-title">Trade-by-Trade P&L</div>
        {tradeBars.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={tradeBars}>
              <XAxis dataKey="signal_id" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} stroke="var(--border-primary)" />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} stroke="var(--border-primary)" />
              <Tooltip
                contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border-primary)', borderRadius: 8, color: 'var(--text-primary)' }}
                formatter={(v) => [`$${Number(v).toLocaleString('en-US')}`, 'Capital P&L']}
              />
              <Bar dataKey="capital_pnl" radius={[4, 4, 0, 0]}>
                {tradeBars.map((entry, i) => (
                  <Cell key={i} fill={(entry.capital_pnl || 0) >= 0 ? '#10b981' : '#ef4444'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>No trade data yet</div>
        )}
      </div>
    </div>
  );
}
