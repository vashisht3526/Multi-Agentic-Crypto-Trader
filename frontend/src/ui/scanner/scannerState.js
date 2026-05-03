const HIDDEN_HEARTBEAT = 'ingestion_heartbeat';
const PASS_EVENTS = new Set([
  'alignment_passed',
  'trade_executed',
  'risk_advisory',
  'challenge_approved',
]);
const BLOCK_EVENTS = new Set([
  'no_alignment',
  'low_alignment',
  'low_adx',
  'pullback_not_complete',
  'low_nofi',
  'delta_divergence',
  'concurrency_blocked',
  'risk_budget_exceeded',
  'circuit_breaker',
  'candle_lock',
  'max_pyramid',
  'analysis_waiting',
  'analysis_skipped',
]);

export function normalizeEvents(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.events)) return data.events;
  return [];
}

export function eventTime(ev) {
  return ev?.event_time || ev?.created_at || ev?.timestamp || null;
}

export function eventMs(ev) {
  const time = eventTime(ev);
  const ms = time ? new Date(time).getTime() : 0;
  return Number.isFinite(ms) ? ms : 0;
}

export function formatEventTime(ev) {
  const time = eventTime(ev);
  if (!time) return '--';
  return new Date(time).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function isHeartbeatEvent(ev) {
  return ev?.component === 'Binance Ingestor' && ev?.event_type === HIDDEN_HEARTBEAT;
}

export function decisionEvents(events) {
  return normalizeEvents(events)
    .filter((ev) => !isHeartbeatEvent(ev))
    .sort((a, b) => eventMs(b) - eventMs(a));
}

export function heartbeatEvents(events) {
  return normalizeEvents(events)
    .filter(isHeartbeatEvent)
    .sort((a, b) => eventMs(b) - eventMs(a));
}

function isOutcomeEvent(ev) {
  return ev?.event_type && !['cycle_started', 'data_computed', 'pipeline_started'].includes(ev.event_type);
}

export function runGroups(events) {
  const groups = new Map();

  decisionEvents(events)
    .filter((ev) => ev.run_id)
    .forEach((ev) => {
      if (!groups.has(ev.run_id)) groups.set(ev.run_id, []);
      groups.get(ev.run_id).push(ev);
    });

  return Array.from(groups.entries()).map(([runId, runEvents]) => {
    const sorted = [...runEvents].sort((a, b) => eventMs(b) - eventMs(a));
    const ordered = [...runEvents].sort((a, b) => eventMs(a) - eventMs(b));
    const outcome = sorted.find(isOutcomeEvent) || sorted[0];

    return {
      runId,
      events: sorted,
      ordered,
      latest: sorted[0],
      outcome,
    };
  });
}

export function eventTone(ev) {
  const status = ev?.status?.toLowerCase();
  const type = ev?.event_type;

  if (status === 'failed' || status === 'error' || type === 'circuit_breaker') return 'danger';
  if (status === 'warn' || BLOCK_EVENTS.has(type)) return 'blocked';
  if (status === 'completed' || PASS_EVENTS.has(type)) return 'active';
  if (status === 'started') return 'running';
  return 'neutral';
}

export function readableEventType(type) {
  if (!type) return 'Waiting';
  return type
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function scannerSummary(events, bias) {
  const all = normalizeEvents(events);
  const decisions = decisionEvents(all);
  const runs = runGroups(decisions);
  const latestRun = runs[0] || null;
  const latestOutcome = latestRun?.outcome || decisions.find(isOutcomeEvent) || decisions[0] || null;
  const tone = latestOutcome ? eventTone(latestOutcome) : 'neutral';
  const side = bias?.candidate_side || latestOutcome?.payload?.candidate_side || 'NONE';
  const score = bias?.alignment_score ?? latestOutcome?.payload?.alignment_score;

  return {
    allCount: all.length,
    hiddenHeartbeatCount: heartbeatEvents(all).length,
    decisionCount: decisions.length,
    latestRun,
    latestOutcome,
    tone,
    side,
    score,
    statusLabel: latestOutcome ? readableEventType(latestOutcome.event_type) : 'Waiting',
    statusTitle: latestOutcome?.title || 'Waiting for scanner activity',
    statusMessage: latestOutcome?.message || 'No scanner decision events in the current window.',
  };
}
