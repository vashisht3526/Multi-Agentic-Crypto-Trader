import { useState, useEffect } from 'react';
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

function formatPct4(v) {
  if (v == null) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(4)}%`;
}

function formatDollar(v) {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  const sign = v >= 0 ? '+' : '-';
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
    hour: '2-digit', minute: '2-digit',
    hour12: true,
  });
}

function getLifecycleTag(sig) {
  const status = (sig.status || '').toUpperCase();
  const verdict = (sig.challenge_verdict || '').toUpperCase();
  if (status === 'WIN') return { label: 'TARGET HIT', cls: 'win', icon: '✓' };
  if (status === 'LOSS') return { label: 'STOPPED OUT', cls: 'loss', icon: '✗' };
  if (status === 'EXPIRED') return { label: 'EXPIRED', cls: 'warn', icon: '⏱' };
  if (status === 'TREND_REVERSAL') return { label: 'TREND EXIT', cls: 'warn', icon: '↩' };
  if (status === 'REJECTED') return { label: 'REJECTED', cls: 'loss', icon: '✗' };
  if (verdict === 'REJECT') return { label: 'REJECTED', cls: 'loss', icon: '✗' };
  if (verdict === 'APPROVE') return { label: 'LIVE TRADE', cls: 'live', icon: '●' };
  return { label: status || 'UNKNOWN', cls: 'open', icon: '?' };
}

function calcLivePnl(sig, currentPrice) {
  if (sig.capital_pnl && sig.status !== 'OPEN') return sig.capital_pnl;
  if (sig.status === 'OPEN' && currentPrice && sig.entry_price) {
    const entry = Number(sig.entry_price);
    const posSize = Number(sig.position_size_usd || sig.capital_base || 10000);
    if (entry <= 0) return 0;
    const pnlPct = sig.side === 'LONG'
      ? (currentPrice - entry) / entry
      : (entry - currentPrice) / entry;
    return posSize * pnlPct;
  }
  return sig.capital_pnl || 0;
}

const STATUS_FILTERS = ['ALL', 'LIVE', 'WIN', 'LOSS', 'TREND_REVERSAL', 'EXPIRED'];
const SIDE_FILTERS = ['ALL', 'LONG', 'SHORT'];

/* ── Sticky P&L column styles ── */
const stickyThStyle = {
  position: 'sticky',
  right: 0,
  zIndex: 3,
  background: 'var(--bg-card, #141e2e)',
  borderLeft: '2px solid var(--accent-cyan)',
  fontWeight: 700,
  fontSize: '0.95rem',
  padding: '0.65rem 1rem',
  whiteSpace: 'nowrap',
  boxShadow: '-4px 0 12px rgba(0,0,0,0.3)',
};

const stickyTdStyle = {
  position: 'sticky',
  right: 0,
  zIndex: 2,
  background: 'var(--bg-card, #141e2e)',
  borderLeft: '2px solid rgba(0, 212, 170, 0.25)',
  fontFamily: 'var(--font-mono, monospace)',
  fontWeight: 700,
  fontSize: '1.05rem',
  textAlign: 'right',
  whiteSpace: 'nowrap',
  padding: '0.6rem 1rem',
  boxShadow: '-4px 0 12px rgba(0,0,0,0.25)',
};

export default function SignalHistory() {
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [sideFilter, setSideFilter] = useState('ALL');
  const [page, setPage] = useState(0);
  const pageSize = 100;

  const queryParams = new URLSearchParams();
  queryParams.set('limit', String(pageSize));
  queryParams.set('offset', String(page * pageSize));
  if (statusFilter !== 'ALL' && statusFilter !== 'LIVE') queryParams.set('status', statusFilter);
  if (sideFilter !== 'ALL') queryParams.set('side', sideFilter);

  const { data, loading } = useApi(`/api/signals/history-v2?${queryParams.toString()}`, 5000);
  const { data: account } = useApi('/api/account', 5000);
  const navigate = useNavigate();

  const result = data || {};
  const signals = result.signals || [];
  const totalCount = result.total_count || 0;
  const totalPages = Math.ceil(totalCount / pageSize);
  const currentPrice = account?.current_price || 0;

  useEffect(() => { setPage(0); }, [statusFilter, sideFilter]);

  const filtered = statusFilter === 'LIVE'
    ? signals.filter(s => (s.status || '').toUpperCase() === 'OPEN')
    : signals;

  const acc = account || {};
  const totalPnl = acc.total_pnl || 0;
  const realPnl = acc.realized_pnl || 0;
  const unrealPnl = acc.unrealized_pnl || 0;

  return (
    <div>
      <div className="page-header">
        <h2>Signal History</h2>
        <p>{totalCount} total signal{totalCount !== 1 ? 's' : ''} — real-time dollar tracking</p>
      </div>

      {/* KPI Summary */}
      <div className="kpi-grid" style={{ marginBottom: '1.5rem' }}>
        <div className={`kpi-card ${totalPnl >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Total P&L</div>
          <div className="kpi-value" style={{ color: totalPnl >= 0 ? 'var(--bull)' : 'var(--bear)' }}>
            ${formatPrice(totalPnl)}
          </div>
          <div className="kpi-note">On ${formatPrice(acc.capital_base)} USDT</div>
        </div>
        <div className={`kpi-card ${realPnl >= 0 ? 'accent-bull' : 'accent-bear'}`}>
          <div className="kpi-label">Realized P&L</div>
          <div className="kpi-value" style={{ color: realPnl >= 0 ? 'var(--bull)' : 'var(--bear)', fontSize: '1.3rem' }}>
            ${formatPrice(realPnl)}
          </div>
          <div className="kpi-note">{acc.win_count || 0}W / {acc.loss_count || 0}L</div>
        </div>
        <div className={`kpi-card ${unrealPnl >= 0 ? 'accent-cyan' : 'accent-bear'}`}>
          <div className="kpi-label">Unrealized P&L</div>
          <div className="kpi-value" style={{ color: unrealPnl >= 0 ? 'var(--accent-cyan)' : 'var(--bear)', fontSize: '1.3rem' }}>
            ${formatPrice(unrealPnl)}
          </div>
          <div className="kpi-note">{acc.active_trades || 0} open</div>
        </div>
        <div className="kpi-card accent-purple">
          <div className="kpi-label">Equity</div>
          <div className="kpi-value" style={{ fontSize: '1.3rem' }}>${formatPrice(acc.current_equity)}</div>
          <div className="kpi-note">Simulated portfolio</div>
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <div className="filter-bar">
          <span style={{ fontSize: '0.8rem', opacity: 0.6, marginRight: '0.5rem' }}>Status:</span>
          {STATUS_FILTERS.map(f => (
            <button key={f} className={`filter-btn ${statusFilter === f ? 'active' : ''}`}
              onClick={() => setStatusFilter(f)}>{f}</button>
          ))}
        </div>
        <div className="filter-bar">
          <span style={{ fontSize: '0.8rem', opacity: 0.6, marginRight: '0.5rem' }}>Side:</span>
          {SIDE_FILTERS.map(f => (
            <button key={f} className={`filter-btn ${sideFilter === f ? 'active' : ''}`}
              onClick={() => setSideFilter(f)}>{f}</button>
          ))}
        </div>
      </div>

      {loading && !signals.length ? (
        <div className="card"><div className="skeleton" style={{ height: 200 }}></div></div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)', fontSize: '1rem' }}>
          No signals found for this filter.
        </div>
      ) : (
        <>
          <div className="table-container" style={{ overflowX: 'auto', position: 'relative' }}>
            <table style={{ fontSize: '0.92rem', minWidth: '900px' }}>
              <thead>
                <tr>
                  <th style={{ padding: '0.65rem 0.7rem', whiteSpace: 'nowrap' }}>Time</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Side</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Status</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Entry</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>SL</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Target</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Exit</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Reason</th>
                  <th style={{ padding: '0.65rem 0.7rem' }}>Net %</th>
                  {/* ═══ STICKY P&L COLUMN ═══ */}
                  <th style={stickyThStyle}>P&L $</th>
                  {/* ─── Scroll-right columns ─── */}
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>MFE</th>
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>MAE</th>
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>Align</th>
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>Session</th>
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>Duration</th>
                  <th style={{ padding: '0.65rem 0.7rem', opacity: 0.6 }}>Signal</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(sig => {
                  const netPnl = sig.net_pnl_percent || 0;
                  const mfe = sig.mfe_percent || 0;
                  const mae = sig.mae_percent || 0;
                  const isActive = (sig.status || '').toUpperCase() === 'OPEN';
                  const netClass = netPnl > 0 ? 'text-bull' : netPnl < 0 ? 'text-bear' : 'text-muted';
                  const lifecycle = getLifecycleTag(sig);
                  const dollarPnl = calcLivePnl(sig, currentPrice);
                  const pnlColor = dollarPnl > 0 ? 'var(--bull)' : dollarPnl < 0 ? 'var(--bear)' : 'var(--text-secondary)';

                  const cell = { padding: '0.55rem 0.7rem' };
                  const mono = { ...cell, fontFamily: 'var(--font-mono, monospace)' };

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
                      {/* Time */}
                      <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                        {formatTime(sig.generated_at)}
                      </td>

                      {/* Side */}
                      <td style={cell}>
                        <span className={`badge ${sig.side?.toLowerCase()}`} style={{ fontSize: '0.88rem' }}>{sig.side}</span>
                      </td>

                      {/* Status */}
                      <td style={cell}>
                        <span className={`badge ${lifecycle.cls}`} style={{ fontSize: '0.82rem' }}>
                          {lifecycle.icon} {lifecycle.label}
                        </span>
                      </td>

                      {/* Entry */}
                      <td style={mono}>${formatPrice(sig.entry_price)}</td>

                      {/* SL */}
                      <td style={{ ...mono, color: 'var(--bear)' }}>${formatPrice(sig.stop_price)}</td>

                      {/* Target */}
                      <td style={{ ...mono, color: 'var(--bull)' }}>${formatPrice(sig.take_profit)}</td>

                      {/* Exit */}
                      <td style={mono}>
                        {sig.close_price ? `$${formatPrice(sig.close_price)}` :
                          isActive ? <span style={{ color: 'var(--accent-cyan)' }}>${formatPrice(currentPrice)}</span> : '—'}
                      </td>

                      {/* Reason */}
                      <td style={cell}>
                        {sig.exit_reason ? (
                          <span className={`badge ${sig.exit_reason === 'TP_HIT' ? 'win' : sig.exit_reason === 'TREND_REVERSAL' ? 'warn' : 'loss'}`}
                            style={{ fontSize: '0.78rem' }}>
                            {sig.exit_reason}
                          </span>
                        ) : isActive ? (
                          <span style={{ color: 'var(--accent-cyan)', fontWeight: 600, fontSize: '0.88rem' }}>ACTIVE</span>
                        ) : '—'}
                      </td>

                      {/* Net % */}
                      <td style={{ ...mono, fontWeight: 600 }} className={netClass}>
                        {isActive && <span style={{ fontSize: '0.65rem', opacity: 0.5, marginRight: '2px' }}>⟳</span>}
                        {formatPct4(netPnl * 100)}
                      </td>

                      {/* ═══ STICKY P&L $ ═══ */}
                      <td style={{ ...stickyTdStyle, color: pnlColor, background: isActive ? 'rgba(0, 30, 46, 0.98)' : 'var(--bg-card, #141e2e)' }}>
                        {isActive && <span style={{ fontSize: '0.58rem', opacity: 0.8, marginRight: '4px', verticalAlign: 'super', color: 'var(--accent-cyan)' }}>LIVE</span>}
                        {formatDollar(dollarPnl)}
                      </td>

                      {/* ─── Scroll-right columns ─── */}
                      <td style={{ ...mono, color: 'var(--bull)', opacity: 0.8 }}>{mfe !== 0 ? formatPct(mfe) : '—'}</td>
                      <td style={{ ...mono, color: 'var(--bear)', opacity: 0.8 }}>{mae !== 0 ? formatPct(mae) : '—'}</td>
                      <td style={{ ...mono, opacity: 0.7 }}>
                        {sig.alignment_score_at_entry ? (
                          <span style={{ color: sig.alignment_score_at_entry >= 0.7 ? 'var(--bull)' : 'var(--accent-cyan)' }}>
                            {Number(sig.alignment_score_at_entry).toFixed(2)}
                          </span>
                        ) : '—'}
                      </td>
                      <td style={{ ...cell, opacity: 0.6 }}>{sig.session_tag || '—'}</td>
                      <td style={{ ...cell, opacity: 0.6, whiteSpace: 'nowrap' }}>{formatDuration(sig.generated_at, sig.closed_at)}</td>
                      <td style={{ ...mono, color: 'var(--accent-cyan)', opacity: 0.5, fontSize: '0.82rem' }}>{sig.signal_id?.slice(0, 8)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '1rem', padding: '1rem', marginTop: '0.5rem' }}>
            <button className="filter-btn" disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>← Prev</button>
            <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
              Page {page + 1} of {Math.max(totalPages, 1)} ({totalCount} signals)
            </span>
            <button className="filter-btn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
          </div>
        </>
      )}
    </div>
  );
}
