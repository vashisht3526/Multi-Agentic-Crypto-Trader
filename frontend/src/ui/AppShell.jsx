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

function KairosLogo() {
  return (
    <svg className="kairos-logo" viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <path className="kairos-logo-orbit" d="M11 35C15 16 36 7 52 19" />
      <path className="kairos-logo-hourglass" d="M22 13H42M22 51H42M24 16C27 25 37 25 40 16M24 48C27 39 37 39 40 48" />
      <path className="kairos-logo-window" d="M25 22L32 32L39 22M25 42L32 32L39 42" />
      <path className="kairos-logo-signal" d="M15 40L24 35L30 38L37 27L44 29L51 20" />
      <path className="kairos-logo-arrow" d="M45 20H51V26" />
      <circle className="kairos-logo-core" cx="32" cy="32" r="4.5" />
      <circle className="kairos-logo-dot" cx="51" cy="20" r="3.2" />
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
          <div className="brand-mark" aria-label="Kairos">
            <KairosLogo />
          </div>
          <div>
            <h1>Kairos</h1>
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
          <span className="brand-mark" aria-label="Kairos"><KairosLogo /></span>
          <span>Kairos</span>
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
