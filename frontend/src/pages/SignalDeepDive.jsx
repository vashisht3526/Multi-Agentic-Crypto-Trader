import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../hooks/useApi';

function formatPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatPct(v) {
  if (v == null) return '—';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

const STATUS_COLORS = {
  COMPLETED: 'text-bull',
  STARTED: 'text-cyan',
  FAILED: 'text-bear',
  SKIPPED: 'text-warn',
  WARN: 'text-warn',
  INFO: 'text-muted',
};

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

function verdictLabel(v) {
  if (!v) return 'PENDING';
  if (v === 'APPROVE') return '✓ VALIDATED';
  if (v === 'REJECT') return '✗ REJECTED';
  return v;
}

export default function SignalDeepDive() {
  const { signalId } = useParams();
  const navigate = useNavigate();
  const { data, loading, error } = useApi(`/api/signals/${signalId}`);

  if (loading) {
    return (
      <div>
        <div className="page-header"><h2>Signal Deep Dive</h2><p>Loading signal details...</p></div>
        <div className="card"><div className="skeleton" style={{ height: 300 }}></div></div>
      </div>
    );
  }

  if (error || !data || data.error) {
    return (
      <div>
        <div className="page-header"><h2>Signal Not Found</h2></div>
        <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>
          {data?.error || error || 'Signal could not be loaded.'}
          <br /><br />
          <button className="filter-btn" onClick={() => navigate('/history')}>← Back to History</button>
        </div>
      </div>
    );
  }

  const sig = data.signal || {};
  const challenge = data.challenge || sig.challenge || {};
  const events = data.agent_events || [];
  const trace = data.decision_trace || sig.decision_trace || {};
  const side = sig.side || 'FLAT';
  const sideClass = side === 'LONG' ? 'bull' : side === 'SHORT' ? 'bear' : 'flat';

  return (
    <div>
      <div className="page-header">
        <h2>Signal Deep Dive</h2>
        <p>
          <button className="filter-btn" onClick={() => navigate(-1)} style={{ marginRight: '0.5rem' }}>← Back</button>
          {sig.signal_id?.slice(0, 12)} — {side} {(sig.regime || '').replace(/_/g, ' ')}
        </p>
      </div>

      {/* Signal Overview Card */}
      <div className={`signal-card ${sideClass}`} style={{ marginBottom: '1.5rem', cursor: 'default' }}>
        <div className="signal-header">
          <div className="signal-title-group">
            <h3>{side} — {(sig.regime || '').replace(/_/g, ' ')}</h3>
            <div className="signal-subtitle">
              ID: {sig.signal_id} • {sig.generated_at ? new Date(sig.generated_at + 'Z').toLocaleString() : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
            <span className={`badge ${sig.status === 'OPEN' ? 'live' : 'warn'}`}>{friendlyStatus(sig.status)}</span>
            <span className={`badge ${sig.challenge_verdict === 'APPROVE' ? 'approve' : sig.challenge_verdict === 'REJECT' ? 'loss' : 'pending'}`}>{verdictLabel(sig.challenge_verdict)}</span>
          </div>
        </div>

        <div className="signal-metrics">
          <div className="signal-metric">
            <div className="signal-metric-label">Entry Price</div>
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
            <div className="signal-metric-label">P&L</div>
            <div className={`signal-metric-value ${(sig.pnl_pct || 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
              {formatPct(sig.pnl_pct)}
            </div>
          </div>
          <div className="signal-metric">
            <div className="signal-metric-label">Capital P&L</div>
            <div className={`signal-metric-value ${(sig.capital_pnl || 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
              ${formatPrice(sig.capital_pnl)}
            </div>
          </div>
        </div>
      </div>

      {/* Trade Logic Transparency */}
      <div className="deep-dive-section">
        <h3>Trade Logic Transparency</h3>

        <div className="logic-block">
          <div className="logic-block-title">Entry Logic — Why This Trade Was Taken</div>
          <div className="logic-block-text">
            {sig.entry_logic || sig.rationale || 'No entry logic available.'}
          </div>
        </div>

        <div className="logic-block">
          <div className="logic-block-title">Exit Logic — Why Trade Closed</div>
          <div className="logic-block-text">
            {sig.exit_logic || 'Trade has not closed yet.'}
          </div>
        </div>

        {sig.success_failure_explanation && (
          <div className="logic-block">
            <div className="logic-block-title">
              {sig.status === 'WIN' ? '✓ Success Explanation' : sig.status === 'LOSS' ? '✗ Failure Explanation' : 'Outcome Analysis'}
            </div>
            <div className="logic-block-text">{sig.success_failure_explanation}</div>
          </div>
        )}
      </div>

      {/* Challenge / Validation */}
      {challenge && (challenge.verdict || challenge.summary) && (
        <div className="deep-dive-section">
          <h3>Signal Validation (AI Gatekeeper)</h3>
          <div className="logic-block">
            <div className="logic-block-title">
              Verdict: <span className={challenge.verdict === 'APPROVE' ? 'text-bull' : challenge.verdict === 'REJECT' ? 'text-bear' : 'text-warn'}>{challenge.verdict}</span>
              {challenge.score != null && <span style={{ marginLeft: '1rem', color: 'var(--text-muted)' }}>Score: {(Number(challenge.score) * 100).toFixed(0)}%</span>}
            </div>
            <div className="logic-block-text">{challenge.summary || sig.challenge_summary || '—'}</div>
          </div>

          {/* Checks breakdown */}
          {challenge.details?.checks && (
            <div className="logic-block" style={{ marginTop: '0.5rem' }}>
              <div className="logic-block-title">Validation Checks</div>
              {challenge.details.checks.map((check, i) => (
                <div key={i} style={{ display: 'flex', gap: '0.75rem', padding: '0.4rem 0', borderBottom: '1px solid var(--border-primary)', fontSize: '0.82rem' }}>
                  <span style={{ color: check.passed ? 'var(--bull)' : 'var(--bear)', fontWeight: 700, width: '1rem' }}>
                    {check.passed ? '✓' : '✗'}
                  </span>
                  <span style={{ color: 'var(--text-primary)', fontWeight: 600, minWidth: '180px' }}>{(check.name || '').replace(/_/g, ' ')}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{check.details}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Agent Conversation Log */}
      {events.length > 0 && (
        <div className="deep-dive-section">
          <h3>Agent Conversation Log</h3>
          <div className="event-timeline">
            {events.map((ev, i) => (
              <div key={i} className="event-item">
                <div className="event-header">
                  <span className="event-component">{ev.component}</span>
                  <span className="event-time">{ev.event_time ? new Date(ev.event_time).toLocaleTimeString() : ''}</span>
                </div>
                <div className="event-title">
                  <span className={STATUS_COLORS[ev.status] || 'text-muted'}>●</span>{' '}
                  {ev.title}
                </div>
                <div className="event-message">{ev.message}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Decision Trace */}
      {trace && Object.keys(trace).length > 0 && (
        <div className="deep-dive-section">
          <h3>Decision Trace</h3>
          {trace.analysis && (
            <div className="logic-block">
              <div className="logic-block-title">Orderflow Analysis</div>
              <div className="logic-block-text">
                Regime: <strong>{trace.analysis.regime}</strong> • 
                Confidence: <strong>{((trace.analysis.confidence || 0) * 100).toFixed(0)}%</strong> •
                Imbalance: <strong>{trace.analysis.imbalance?.toFixed(4)}</strong> •
                Volume Delta: <strong>{trace.analysis.volume_delta?.toFixed(4)} BTC</strong>
              </div>
            </div>
          )}
          {trace.learning_recommendation && (
            <div className="logic-block">
              <div className="logic-block-title">Learning Memory</div>
              <div className="logic-block-text">
                Recommendation: <strong>{trace.learning_recommendation.recommendation}</strong> •
                Samples: <strong>{trace.learning_recommendation.sample_size}</strong> •
                Win Rate: <strong>{((trace.learning_recommendation.win_rate || 0) * 100).toFixed(0)}%</strong>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
