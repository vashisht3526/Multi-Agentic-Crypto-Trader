import { useState, useEffect, useRef } from 'react';

const WS_URL = `ws://${window.location.hostname}:8000/ws/live`;

export function useWebSocket() {
  const [data, setData] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);

  useEffect(() => {
    let shouldReconnect = true;

    const connect = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;

      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        if (reconnectTimer.current) {
          clearTimeout(reconnectTimer.current);
          reconnectTimer.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          if (parsed.type === 'update') {
            setData(parsed);
          }
        } catch {
          /* ignore malformed */
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (shouldReconnect) {
          reconnectTimer.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();
    // Ping keep-alive
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 20000);

    return () => {
      shouldReconnect = false;
      clearInterval(pingInterval);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, []);

  return { data, connected };
}
