import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { useWebSocket } from './hooks/useWebSocket';
import AppShell from './ui/AppShell';
import Dashboard from './pages/Dashboard';
import LiveSignals from './pages/LiveSignals';
import SignalHistory from './pages/SignalHistory';
import Performance from './pages/Performance';
import AgentActivity from './pages/AgentActivity';
import SignalDeepDive from './pages/SignalDeepDive';
import './App.css';
import './ui/styles/modern.css';

function App() {
  const ws = useWebSocket();

  return (
    <BrowserRouter>
      <AppShell ws={ws}>
        <Routes>
          <Route path="/" element={<Dashboard ws={ws} />} />
          <Route path="/dashboard" element={<Dashboard ws={ws} />} />
          <Route path="/signals" element={<LiveSignals ws={ws} />} />
          <Route path="/history" element={<SignalHistory />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/agents" element={<AgentActivity />} />
          <Route path="/signals/:signalId" element={<SignalDeepDive />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}

export default App;
