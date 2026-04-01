import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import { useNavigate } from 'react-router-dom';

function formatPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatPct(v) {
  if (v == null) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

function formatDuration(start, end) {
  if (!start) return '—';
  const s = new Date(start + 'Z');
  const e = end ? new Date(end + 'Z') : new Date();
  const diff = Math.max(0, Math.floor((e - s) / 1000));
  if (diff < 60) return `${diff}s`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ${diff % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatTime(dt) {
  if (!dt) return '—';
  return new Date(dt + 'Z').toLocaleString('en-US', {
    day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: true,
  });
}

/** Map internal DB states to user-friendly lifecycle tags */
function getLifecycleTag(sig) {
  const status = (sig.status || '').toUpperCase();
  const verdict = (sig.challenge_verdict || '').toUpperCase();

  if (status === 'WIN') return { label: 'TARGET HIT', cls: 'win', icon: '✓' };
  if (status === 'LOSS') return { label: 'STOPPED OUT', cls: 'loss', icon: '✗' };
  if (status === 'EXPIRED') return { label: 'EXPIRED', cls: 'warn', icon: '⏱' };
  if (status === 'REJECTED') return { label: 'REJECTED', cls: 'loss', icon: '✗' };
  if (status === 'PENDING_VALIDATION') return { label: 'AWAITING VALIDATION', cls: 'pending', icon: '◌' };

  // OPEN = validated and live
  if (verdict === 'REJECT') return { label: 'REJECTED', cls: 'loss', icon: '✗' };
  if (verdict === 'APPROVE') return { label: 'LIVE TRADE', cls: 'live', icon: '●' };
  if (verdict === 'PENDING' || !verdict) return { label: 'VALIDATING', cls: 'pending', icon: '◌' };

  return { label: status || 'UNKNOWN', cls: 'open', icon: '?' };
}

function getVerdictTag(verdict) {
  if (!verdict) return { label: 'PENDING', cls: 'pending' };
  if (verdict === 'APPROVE') return { label: '✓ VALIDATED', cls: 'approve' };
  if (verdict === 'REJECT') return { label: '✗ REJECTED', cls: 'loss' };
  return { label: verdict, cls: 'open' };
}

const FILTERS = ['ALL', 'LIVE', 'WIN', 'LOSS', 'EXPIRED', 'REJECTED'];

export default function SignalHistory() {
  const [filter, setFilter] = useState('ALL');
  const { data, loading } = useApi('/api/signals/history?limit=500', 10000);
  const { data: account } = useApi('/api/account', 10000);
  const navigate = useNavigate();

  const signals = data || [];

  const filtered = signals.filter(s => {
    if (filter === 'ALL') return true;
    const status = (s.status || '').toUpperCase();
    const verdict = (s.challenge_verdict || '').toUpperCase();
    if (filter === 'LIVE') return status === 'OPEN' && verdict !== 'REJECT';
    if (filter === 'PENDING') return status === 'PENDING_VALIDATION';
    if (filter === 'REJECTED') return verdict === 'REJECT' || status === 'REJECTED';
    return status === filter;
  });

  // Count by category for filter badges
  const counts = {
    ALL: signals.length,
    LIVE: signals.filter(s => s.status === 'OPEN' && (s.challenge_verdict || '').toUpperCase() !== 'REJECT').length,
    WIN: signals.filter(s => s.status === 'WIN').length,
    LOSS: signals.filter(s => s.status === 'LOSS').length,
    EXPIRED: signals.filter(s => s.status === 'EXPIRED').length,
    REJECTED: signals.filter(s => (s.challenge_verdict || '').toUpperCase() === 'REJECT' || s.status === 'REJECTED').length,
  };

  const acc = account || {};
  const totalPnl = acc.total_pnl || 0;
  const realPnl = acc.realized_pnl || 0;
  const unrealPnl = acc.unrealized_pnl || 0;

  return (
    <div>
      <div className="page-header">
        <h2>Signal History</h2>
        <p>{filtered.length} signal{filtered.length !== 1 ? 's' : ''} — full trade log with lifecycle tags</p>
      </div>

      {/* Account Summary Bar */}
      <div className="kpi-grid" style={{ marginBottom: '1.5rem' }}>
        <div className={`kpi-card ${totalPnl >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Total P&L (Realized + Active)</div>
          <div className="kpi-value" style={{ color: totalPnl >= 0 ? 'var(--bull)' : 'var(--bear)' }}>
            ${formatPrice(totalPnl)}
          </div>
          <div className="kpi-note">On ${formatPrice(acc.capital_base)} USDT capital</div>
        </div>
        <div className={`kpi-card ${realPnl >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Realized P&L</div>
          <div className="kpi-value" style={{ color: realPnl >= 0 ? 'var(--bull)' : 'var(--bear)', fontSize: '1.3rem' }}>
            ${formatPrice(realPnl)}
          </div>
          <div className="kpi-note">Closed trades: {acc.win_count || 0}W / {acc.loss_count || 0}L</div>
        </div>
        <div className={`kpi-card ${unrealPnl >= 0 ? 'accent-cyan' : 'accent-bear'}`}>
          <div className="kpi-label">Active / Unrealized P&L</div>
          <div className="kpi-value" style={{ color: unrealPnl >= 0 ? 'var(--accent-cyan)' : 'var(--bear)', fontSize: '1.3rem' }}>
            ${formatPrice(unrealPnl)}
          </div>
          <div className="kpi-note">{acc.active_trades || 0} open trade{(acc.active_trades || 0) !== 1 ? 's' : ''}</div>
        </div>
        <div className="kpi-card accent-purple">
          <div className="kpi-label">Current Equity</div>
          <div className="kpi-value" style={{ fontSize: '1.3rem' }}>${formatPrice(acc.current_equity)}</div>
          <div className="kpi-note">Simulated USDT portfolio</div>
        </div>
      </div>

      <div className="filter-bar">
        {FILTERS.map(f => (
          <button
            key={f}
            className={`filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f === 'ALL' ? 'All' : f} <span style={{ opacity: 0.6, fontSize: '0.75rem', marginLeft: '0.3rem' }}>({counts[f] || 0})</span>
          </button>
        ))}
      </div>

      {loading && !signals.length ? (
        <div className="card"><div className="skeleton" style={{ height: 200 }}></div></div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
          No signals found for this filter.
        </div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Generated</th>
                <th>Side</th>
                <th>Status</th>
                <th>Verdict</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>SL</th>
                <th>Target</th>
                <th>P&L %</th>
                <th>P&L $</th>
                <th>Duration</th>
                <th>Closed At</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(sig => {
                const pnl = sig.pnl_pct || 0;
                const capPnl = sig.capital_pnl || 0;
                const isActive = (sig.status || '').toUpperCase() === 'OPEN';
                const isPending = (sig.status || '').toUpperCase() === 'PENDING_VALIDATION';
                const pnlClass = pnl > 0 ? 'text-bull' : pnl < 0 ? 'text-bear' : 'text-muted';
                const lifecycle = getLifecycleTag(sig);
                const verdictTag = getVerdictTag(sig.challenge_verdict);

                return (
                  <tr
                    key={sig.signal_id}
                    style={{
                      cursor: 'pointer',
                      borderLeft: isActive ? '3px solid var(--accent-cyan)' : undefined,
                      background: isActive ? 'rgba(0, 212, 170, 0.04)' : undefined,
                    }}
                    onClick={() => navigate(`/signals/${sig.signal_id}`)}
                  >
                    <td className="font-mono" style={{ color: 'var(--accent-cyan)' }}>{sig.signal_id?.slice(0, 8)}</td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {formatTime(sig.generated_at)}
                    </td>
                    <td><span className={`badge ${sig.side?.toLowerCase()}`}>{sig.side}</span></td>
                    <td>
                      <span className={`badge ${lifecycle.cls}`}>
                        <span style={{ marginRight: '0.25rem' }}>{lifecycle.icon}</span>
                        {lifecycle.label}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${verdictTag.cls}`}>{verdictTag.label}</span>
                    </td>
                    <td className="font-mono">${formatPrice(sig.entry_price)}</td>
                    <td className="font-mono">{sig.close_price ? `$${formatPrice(sig.close_price)}` : isActive ? <span style={{ color: 'var(--accent-cyan)', fontStyle: 'italic', fontSize: '0.78rem' }}>ongoing</span> : '—'}</td>
                    <td className="font-mono text-bear">${formatPrice(sig.stop_price)}</td>
                    <td className="font-mono text-bull">${formatPrice(sig.take_profit)}</td>
                    <td className={`font-mono font-bold ${pnlClass}`}>
                      {isActive && <span title="Unrealized" style={{ fontSize: '0.65rem', opacity: 0.7, marginRight: '0.2rem' }}>⟳</span>}
                      {formatPct(pnl)}
                    </td>
                    <td className={`font-mono font-bold ${pnlClass}`}>
                      {isActive && <span title="Unrealized" style={{ fontSize: '0.65rem', opacity: 0.7, marginRight: '0.2rem' }}>⟳</span>}
                      ${formatPrice(capPnl)}
                    </td>
                    <td className="text-muted" style={{ whiteSpace: 'nowrap' }}>{formatDuration(sig.generated_at, sig.closed_at)}</td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {sig.closed_at ? formatTime(sig.closed_at) : isActive ? <span style={{ color: 'var(--accent-cyan)' }}>active</span> : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
