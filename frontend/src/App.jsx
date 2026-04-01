import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { useWebSocket } from './hooks/useWebSocket';
import Dashboard from './pages/Dashboard';
import LiveSignals from './pages/LiveSignals';
import SignalHistory from './pages/SignalHistory';
import Performance from './pages/Performance';
import AgentActivity from './pages/AgentActivity';
import SignalDeepDive from './pages/SignalDeepDive';
import './App.css';

const NAV_ITEMS = [
  { path: '/', icon: '◈', label: 'Dashboard' },
  { path: '/signals', icon: '◉', label: 'Live Signals' },
  { path: '/history', icon: '☰', label: 'Signal History' },
  { path: '/performance', icon: '△', label: 'Performance' },
  { path: '/agents', icon: '⬡', label: 'Agent Activity' },
];

function App() {
  const ws = useWebSocket();

  return (
    <BrowserRouter>
      <div className="app-layout">
        <aside className="sidebar">
          <div className="sidebar-logo">
            <h1>CryptoSignals</h1>
            <div className="tag">AI Trading Platform</div>
          </div>
          <nav className="sidebar-nav">
            {NAV_ITEMS.map(item => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
              >
                <span className="nav-icon">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ws-status">
            <span className={`ws-dot ${ws.connected ? 'connected' : 'disconnected'}`}></span>
            {ws.connected ? 'Live Connected' : 'Reconnecting...'}
          </div>
        </aside>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard ws={ws} />} />
            <Route path="/signals" element={<LiveSignals ws={ws} />} />
            <Route path="/history" element={<SignalHistory />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/agents" element={<AgentActivity />} />
            <Route path="/signals/:signalId" element={<SignalDeepDive />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
