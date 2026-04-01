import { useApi } from '../hooks/useApi';

const STATUS_COLORS = {
  COMPLETED: 'text-bull',
  STARTED: 'text-cyan',
  FAILED: 'text-bear',
  SKIPPED: 'text-warn',
  WARN: 'text-warn',
  INFO: 'text-muted',
};

export default function AgentActivity() {
  const { data, loading } = useApi('/api/agents/events?limit=200', 6000);
  const events = data || [];

  // Group by run_id
  const grouped = {};
  const noRun = [];
  events.forEach(ev => {
    if (ev.run_id) {
      if (!grouped[ev.run_id]) grouped[ev.run_id] = [];
      grouped[ev.run_id].push(ev);
    } else {
      noRun.push(ev);
    }
  });

  const runIds = Object.keys(grouped);

  return (
    <div>
      <div className="page-header">
        <h2>Agent Activity</h2>
        <p>Live view of what each agent is doing — {events.length} recent events</p>
      </div>

      {loading && !events.length ? (
        <div className="event-timeline">{[...Array(5)].map((_, i) => <div key={i} className="event-item"><div className="skeleton" style={{ height: 50 }}></div></div>)}</div>
      ) : events.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
          No agent events recorded yet. Start the pipeline to see activity.
        </div>
      ) : (
        <>
          {/* Grouped by crew run */}
          {runIds.slice(0, 20).map(runId => {
            const runEvents = grouped[runId];
            const firstEvent = runEvents[runEvents.length - 1];
            const lastEvent = runEvents[0];
            return (
              <div key={runId} className="card" style={{ marginBottom: '1rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                  <div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Crew Run</div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--accent-cyan)', fontFamily: 'monospace' }}>{runId.slice(0, 12)}</div>
                  </div>
                  <span className={`badge ${lastEvent.status?.toLowerCase() || 'info'}`}>
                    {lastEvent.status}
                  </span>
                </div>
                <div className="event-timeline">
                  {runEvents.map((ev, i) => (
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
            );
          })}

          {/* System events without run_id */}
          {noRun.length > 0 && (
            <div className="card">
              <div className="card-title">System Events</div>
              <div className="event-timeline">
                {noRun.slice(0, 30).map((ev, i) => (
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
        </>
      )}
    </div>
  );
}
