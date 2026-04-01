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

export default function LiveSignals({ ws }) {
  const { data: apiSignals, loading, refetch } = useApi('/api/signals/active', 8000);
  const navigate = useNavigate();

  const handleClose = async (type, id = null, e = null) => {
    if (e) e.stopPropagation();
    const url = `/api/signals/close/${type}` + (id ? `?signal_id=${id}` : '');
    try {
      const res = await fetch(`http://localhost:8000${url}`, { method: 'POST' });
      if (res.ok) {
        if (refetch) refetch();
      }
    } catch (err) {
      console.error('Failed to close trade:', err);
    }
  };

  // Prefer WS data if available
  const signals = ws?.data?.active_signals?.length ? ws.data.active_signals : (apiSignals || []);
  const price = ws?.data?.last_price;

  if (loading && !signals.length) {
    return (
      <div>
        <div className="page-header"><h2>Live Signals</h2><p>Loading active signals...</p></div>
        <div className="signal-grid">{[...Array(3)].map((_, i) => <div key={i} className="card"><div className="skeleton" style={{ height: 140 }}></div></div>)}</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h2>Live Signals</h2>
          <p>{signals.length} active signal{signals.length !== 1 ? 's' : ''} {price ? `— BTC $${formatPrice(price)}` : ''}</p>
        </div>
        {signals.length > 0 && (
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn" onClick={() => handleClose('win')} style={{ background: 'var(--bg-bull)', color: 'var(--text-bull)', border: '1px solid var(--accent-bull)' }}>Close Winners</button>
            <button className="btn" onClick={() => handleClose('loss')} style={{ background: 'var(--bg-bear)', color: 'var(--text-bear)', border: '1px solid var(--accent-bear)' }}>Close Losers</button>
            <button className="btn" onClick={() => handleClose('all')} style={{ background: 'var(--bg-header)', border: '1px solid var(--border)' }}>Close All</button>
          </div>
        )}
      </div>

      {signals.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>◌</div>
          <div style={{ color: 'var(--text-secondary)' }}>No active signals right now. The AI agents are analyzing the market.</div>
        </div>
      ) : (
        <div className="signal-grid">
          {signals.map(sig => {
            const side = sig.side || 'FLAT';
            const sideClass = side === 'LONG' ? 'bull' : side === 'SHORT' ? 'bear' : 'flat';
            const isValid = sig.is_valid;
            const liveState = sig.live_state || sig.status || 'OPEN';

            let statusLabel = friendlyStatus(liveState);
            let statusClass = badgeClass(liveState);
            if (isValid) { statusClass = 'live'; statusLabel = 'LIVE'; }

            return (
              <div
                key={sig.signal_id}
                className={`signal-card ${sideClass}`}
                onClick={() => navigate(`/signals/${sig.signal_id}`)}
              >
                <div className="signal-header">
                  <div className="signal-title-group">
                    <h3>{side} — {(sig.regime || '').replace(/_/g, ' ')}</h3>
                    <div className="signal-subtitle">ID: {sig.signal_id?.slice(0, 8)} • {sig.generated_at ? new Date(sig.generated_at).toLocaleTimeString() : ''}</div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
                    <span className={`badge ${statusClass}`}>
                      {isValid && <span className="badge-dot"></span>}
                      {statusLabel}
                    </span>
                    <span className={`badge ${sig.challenge_verdict?.toLowerCase() || 'pending'}`}>
                      {sig.challenge_verdict || 'PENDING'}
                    </span>
                    <button 
                      onClick={(e) => handleClose('single', sig.signal_id, e)}
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
                    <div className="signal-metric-label">Risk:Reward</div>
                    <div className="signal-metric-value">{sig.risk_reward || '—'}</div>
                  </div>
                </div>

                <div className="signal-metrics" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                  <div className="signal-metric">
                    <div className="signal-metric-label">Confidence</div>
                    <div className="signal-metric-value">{((sig.confidence || 0) * 100).toFixed(0)}%</div>
                  </div>
                  <div className="signal-metric">
                    <div className="signal-metric-label">Unrealized P&L</div>
                    <div className={`signal-metric-value ${(sig.unrealized_pnl_pct || 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {formatPct(sig.unrealized_pnl_pct)}
                    </div>
                  </div>
                  <div className="signal-metric">
                    <div className="signal-metric-label">Time Left</div>
                    <div className="signal-metric-value">{sig.time_remaining_seconds ? `${Math.floor(sig.time_remaining_seconds / 60)}m ${sig.time_remaining_seconds % 60}s` : '—'}</div>
                  </div>
                </div>

                {/* Entry Logic */}
                {sig.entry_logic && (
                  <div className="signal-rationale">
                    <strong style={{ color: 'var(--accent-cyan)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Entry Logic:</strong><br/>
                    {sig.entry_logic}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
