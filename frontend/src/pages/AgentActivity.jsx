import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import ScannerStatusPanel from '../ui/scanner/ScannerStatusPanel';
import {
  decisionEvents,
  eventTone,
  formatEventTime,
  heartbeatEvents,
  normalizeEvents,
  readableEventType,
  runGroups,
} from '../ui/scanner/scannerState';

const MODES = [
  { id: 'decisions', label: 'Decisions' },
  { id: 'all', label: 'All Events' },
  { id: 'heartbeats', label: 'Ingestion' },
];

function EventRow({ ev }) {
  return (
    <div className={`agent-event-row ${eventTone(ev)}`}>
      <span className="status-dot" />
      <div>
        <div className="agent-event-topline">
          <strong>{ev.component || 'System'}</strong>
          <span>{formatEventTime(ev)}</span>
        </div>
        <div className="agent-event-title">{ev.title || readableEventType(ev.event_type)}</div>
        {ev.message && <div className="agent-event-message">{ev.message}</div>}
      </div>
    </div>
  );
}

function RunCard({ run }) {
  return (
    <article className={`agent-run-card ${eventTone(run.outcome)}`}>
      <div className="agent-run-head">
        <div>
          <span>Run</span>
          <strong>{run.runId.slice(0, 12)}</strong>
        </div>
        <div>
          <span>{formatEventTime(run.latest)}</span>
          <strong>{readableEventType(run.outcome?.event_type)}</strong>
        </div>
      </div>
      <div className="agent-event-list">
        {run.ordered.map((ev) => (
          <EventRow key={`${run.runId}-${ev.event_time}-${ev.component}-${ev.event_type}`} ev={ev} />
        ))}
      </div>
    </article>
  );
}

export default function AgentActivity() {
  const [mode, setMode] = useState('decisions');
  const { data, loading } = useApi('/api/agents/events?limit=1000', 6000);
  const { data: bias } = useApi('/api/timeframe/bias', 15000);

  const allEvents = normalizeEvents(data);
  const decisions = decisionEvents(allEvents);
  const heartbeats = heartbeatEvents(allEvents);
  const visibleEvents =
    mode === 'heartbeats' ? heartbeats :
      mode === 'all' ? allEvents :
        decisions;
  const visibleRuns = runGroups(visibleEvents).slice(0, 18);
  const standaloneEvents = visibleEvents
    .filter((ev) => !ev.run_id)
    .sort((a, b) => new Date(b.event_time || 0) - new Date(a.event_time || 0))
    .slice(0, mode === 'heartbeats' ? 60 : 24);

  return (
    <div className="agent-workspace">
      <ScannerStatusPanel events={allEvents} bias={bias} loading={loading} />

      <div className="agent-toolbar">
        <div className="agent-tabset" role="tablist" aria-label="Agent event filters">
          {MODES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`agent-tab ${mode === item.id ? 'active' : ''}`}
              onClick={() => setMode(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="agent-count-strip">
          <span>{decisions.length} decisions</span>
          <span>{heartbeats.length} ingestion</span>
        </div>
      </div>

      {loading && !allEvents.length ? (
        <div className="agent-run-stack">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="agent-run-card">
              <div className="skeleton" style={{ height: 82 }} />
            </div>
          ))}
        </div>
      ) : visibleRuns.length === 0 && standaloneEvents.length === 0 ? (
        <div className="agent-empty-state">
          <strong>No events in this view</strong>
          <span>Switch filters to inspect other pipeline records.</span>
        </div>
      ) : (
        <div className="agent-run-stack">
          {visibleRuns.map((run) => (
            <RunCard key={run.runId} run={run} />
          ))}

          {standaloneEvents.length > 0 && (
            <section className="agent-system-feed">
              <div className="agent-feed-title">
                <span>{mode === 'heartbeats' ? 'Ingestion Stream' : 'System Events'}</span>
                <strong>{standaloneEvents.length}</strong>
              </div>
              <div className="agent-event-list">
                {standaloneEvents.map((ev, i) => (
                  <EventRow key={`${ev.event_time}-${ev.component}-${ev.event_type}-${i}`} ev={ev} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
