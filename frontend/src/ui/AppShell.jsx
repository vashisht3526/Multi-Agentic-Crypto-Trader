import { NavLink, useLocation } from 'react-router-dom';

function Icon({ name }) {
  const common = {
    width: '18',
    height: '18',
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: '1.8',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': 'true',
  };

  if (name === 'dashboard') {
    return (
      <svg {...common}>
        <path d="M4 13.5V20h6v-6.5H4Z" />
        <path d="M14 4v16h6V4h-6Z" />
        <path d="M4 4v5.5h6V4H4Z" />
      </svg>
    );
  }

  if (name === 'signals') {
    return (
      <svg {...common}>
        <path d="M4 17h3.5l2.2-10 4.6 12 2.2-7H20" />
        <path d="M4 6h3" />
        <path d="M17 6h3" />
      </svg>
    );
  }

  if (name === 'history') {
    return (
      <svg {...common}>
        <path d="M5 5v14" />
        <path d="M9 7h10" />
        <path d="M9 12h10" />
        <path d="M9 17h7" />
      </svg>
    );
  }

  if (name === 'performance') {
    return (
      <svg {...common}>
        <path d="M4 18 9 12l4 3 7-9" />
        <path d="M4 20h16" />
      </svg>
    );
  }

  return (
    <svg {...common}>
      <path d="M12 4v3" />
      <path d="M12 17v3" />
      <path d="M4 12h3" />
      <path d="M17 12h3" />
      <path d="M8.5 8.5h7v7h-7z" />
    </svg>
  );
}

function ChronosLogo() {
  return (
    <svg className="chronos-logo" viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle className="chronos-logo-core" cx="32" cy="32" r="21" />
      <path className="chronos-logo-orbit" d="M11 36c4.8-15.6 20.7-27.2 35.5-21.8 8.9 3.2 9.3 12.3 1.2 18.1-9.7 6.9-27 7.6-37.6 1.5" />
      <path className="chronos-logo-hourglass" d="M24 19h16M24 45h16M27 21c0 7.8 10 8.8 10 11s-10 3.2-10 11M37 21c0 7.8-10 8.8-10 11s10 3.2 10 11" />
      <path className="chronos-logo-hand" d="M32 32l9-6" />
      <circle className="chronos-logo-dot" cx="47" cy="22" r="3" />
    </svg>
  );
}

const NAV_ITEMS = [
  { path: '/', aliases: ['/dashboard'], icon: 'dashboard', label: 'Dashboard', shortLabel: 'Dash', eyebrow: '' },
  { path: '/signals', icon: 'signals', label: 'Live Signals', shortLabel: 'Live', eyebrow: 'Execution' },
  { path: '/history', icon: 'history', label: 'Signal History', shortLabel: 'History', eyebrow: 'Ledger' },
  { path: '/performance', icon: 'performance', label: 'Performance', shortLabel: 'Perf', eyebrow: 'Analytics' },
  { path: '/agents', icon: 'agents', label: 'Agent Activity', shortLabel: 'Agents', eyebrow: 'Pipeline' },
];

function formatPrice(v) {
  if (v == null) return '--';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 });
}

function ConnectionStatus({ connected, compact = false }) {
  return (
    <div className={compact ? 'ws-status ws-status-compact' : 'ws-status'}>
      <span className={`ws-dot ${connected ? 'connected' : 'disconnected'}`} />
      <span>{connected ? 'Live' : 'Syncing'}</span>
    </div>
  );
}

function NavItems({ compact = false }) {
  const location = useLocation();
  return NAV_ITEMS.map((item) => (
    <NavLink
      key={item.path}
      to={item.path}
      end={item.path === '/'}
      title={item.label}
      className={({ isActive }) => {
        const aliasActive = item.aliases?.includes(location.pathname);
        return `nav-link ${compact ? 'nav-link-compact' : ''} ${isActive || aliasActive ? 'active' : ''}`;
      }}
    >
      <span className="nav-icon"><Icon name={item.icon} /></span>
      <span className="nav-label">{compact ? item.shortLabel : item.label}</span>
    </NavLink>
  ));
}

function getPageMeta(pathname) {
  if (pathname.startsWith('/signals/') && pathname !== '/signals') {
    return { label: 'Signal Deep Dive', eyebrow: 'Trade Detail' };
  }
  const exact = NAV_ITEMS.find((item) => item.path === pathname);
  if (exact) return exact;
  const aliased = NAV_ITEMS.find((item) => item.aliases?.includes(pathname));
  if (aliased) return aliased;
  return NAV_ITEMS[0];
}

export default function AppShell({ ws, children }) {
  const location = useLocation();
  const page = getPageMeta(location.pathname);
  const price = ws?.data?.last_price;
  const session = ws?.data?.session_tag || '--';
  const activeSignals = ws?.data?.active_signals?.length ?? 0;
  const l2 = ws?.data?.current_l2 || {};

  return (
    <div className="app-layout">
      <div className="ambient-grid" aria-hidden="true" />
      <div className="ambient-scanline" aria-hidden="true" />

      <aside className="sidebar" aria-label="Primary navigation">
        <div className="sidebar-logo">
          <div className="brand-mark" aria-label="Chronos">
            <ChronosLogo />
          </div>
          <div>
            <h1>Chronos</h1>
            <div className="tag">Temporal Signal OS</div>
          </div>
        </div>
        <nav className="sidebar-nav">
          <NavItems />
        </nav>
        <div className="sidebar-footer">
          <ConnectionStatus connected={ws.connected} />
        </div>
      </aside>

      <header className="desktop-command-bar">
        <div className={page.eyebrow ? 'command-title' : 'command-title command-title-plain'}>
          {page.eyebrow && <span>{page.eyebrow}</span>}
          <strong>{page.label}</strong>
        </div>
        <div className="market-strip">
          <div className="market-pill market-pill-primary">
            <span>BTCUSDT</span>
            <strong>${formatPrice(price)}</strong>
          </div>
          <div className="market-pill">
            <span>Session</span>
            <strong>{session}</strong>
          </div>
          <div className="market-pill">
            <span>Active</span>
            <strong>{activeSignals}</strong>
          </div>
          <div className="market-pill">
            <span>Spread</span>
            <strong>{l2.spread_ticks != null ? `${l2.spread_ticks}t` : '--'}</strong>
          </div>
          <ConnectionStatus connected={ws.connected} compact />
        </div>
      </header>

      <header className="mobile-topbar">
        <div className="mobile-brand">
          <span className="brand-mark" aria-label="Chronos"><ChronosLogo /></span>
          <span>Chronos</span>
        </div>
        <ConnectionStatus connected={ws.connected} compact />
      </header>

      <main className="main-content">
        <div className="content-frame">
          {children}
        </div>
      </main>

      <nav className="mobile-tabbar" aria-label="Primary navigation">
        <NavItems compact />
      </nav>
    </div>
  );
}
