import {
  eventTone,
  formatEventTime,
  readableEventType,
  scannerSummary,
} from './scannerState';

function formatScore(score) {
  if (score == null || Number.isNaN(Number(score))) return '--';
  return Number(score).toFixed(2);
}

function StageRail({ run }) {
  if (!run?.ordered?.length) {
    return (
      <div className="scanner-stage-rail">
        <div className="scanner-stage neutral">
          <span />
          <strong>Waiting</strong>
          <small>--</small>
        </div>
      </div>
    );
  }

  return (
    <div className="scanner-stage-rail">
      {run.ordered.slice(0, 5).map((ev) => (
        <div key={`${ev.event_time}-${ev.component}-${ev.event_type}`} className={`scanner-stage ${eventTone(ev)}`}>
          <span />
          <strong>{ev.component}</strong>
          <small>{readableEventType(ev.event_type)}</small>
        </div>
      ))}
    </div>
  );
}

export default function ScannerStatusPanel({ events, bias, loading = false, compact = false }) {
  const summary = scannerSummary(events, bias);
  const outcome = summary.latestOutcome;

  return (
    <section className={`scanner-panel ${compact ? 'scanner-panel-compact' : ''}`}>
      <div className="scanner-panel-head">
        <div>
          <div className="scanner-kicker">Scanner status</div>
          <h3>{loading && !summary.latestOutcome ? 'Reading live pipeline' : summary.statusLabel}</h3>
        </div>
        <span className={`scanner-state ${summary.tone}`}>
          {summary.tone === 'blocked' ? 'Blocked' : summary.tone === 'active' ? 'Passed' : summary.tone === 'running' ? 'Running' : 'Live'}
        </span>
      </div>

      <div className="scanner-summary-grid">
        <div className="scanner-stat">
          <span>Latest Run</span>
          <strong>{summary.latestRun ? summary.latestRun.runId.slice(0, 8) : '--'}</strong>
        </div>
        <div className="scanner-stat">
          <span>Candidate</span>
          <strong className={summary.side === 'LONG' ? 'text-bull' : summary.side === 'SHORT' ? 'text-bear' : ''}>
            {summary.side}
          </strong>
        </div>
        <div className="scanner-stat">
          <span>Alignment</span>
          <strong>{formatScore(summary.score)}</strong>
        </div>
        <div className="scanner-stat">
          <span>Updated</span>
          <strong>{outcome ? formatEventTime(outcome) : '--'}</strong>
        </div>
      </div>

      <div className="scanner-reason">
        <strong>{summary.statusTitle}</strong>
        {!compact && <span>{summary.statusMessage}</span>}
      </div>

      {!compact && <StageRail run={summary.latestRun} />}
    </section>
  );
}
