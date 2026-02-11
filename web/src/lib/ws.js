/**
 * WebSocket client with exponential backoff reconnection.
 * Mirrors iOS WebSocketService.swift logic.
 */

const MAX_RETRIES = 8;
const BASE_DELAY = 1000;
const MAX_DELAY = 30000;
const CONNECTION_TIMEOUT = 10000;
const PING_INTERVAL = 30000;

export function createWebSocket(url, handlers) {
  let ws = null;
  let retryCount = 0;
  let timeoutId = null;
  let pingId = null;
  let intentionalClose = false;

  function connect() {
    cleanup();
    intentionalClose = false;

    handlers.onStateChange?.('connecting');
    ws = new WebSocket(url);

    // Connection timeout
    timeoutId = setTimeout(() => {
      if (ws && ws.readyState !== WebSocket.OPEN) {
        ws.close();
        handleDisconnect();
      }
    }, CONNECTION_TIMEOUT);

    ws.onopen = () => {
      // Wait for hello message to confirm ready
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'hello') {
          clearTimeout(timeoutId);
          retryCount = 0;
          handlers.onStateChange?.('ready');
          startPing();
        }
        handlers.onMessage?.(msg);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    ws.onerror = () => {
      // onclose will fire after this
    };

    ws.onclose = () => {
      clearTimeout(timeoutId);
      clearInterval(pingId);
      if (!intentionalClose) {
        handleDisconnect();
      }
    };
  }

  function handleDisconnect() {
    retryCount++;
    if (retryCount > MAX_RETRIES) {
      handlers.onStateChange?.('failed');
      handlers.onError?.(`Connection failed after ${MAX_RETRIES} attempts`);
      return;
    }

    handlers.onStateChange?.('reconnecting');

    const exponential = BASE_DELAY * Math.pow(2, retryCount - 1);
    const capped = Math.min(exponential, MAX_DELAY);
    const jitter = Math.random() * capped * 0.3;
    const delay = capped + jitter;

    setTimeout(connect, delay);
  }

  function startPing() {
    clearInterval(pingId);
    pingId = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        // WebSocket API doesn't have native ping, send a keep-alive
        // Server will ignore unknown types gracefully
        try {
          ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          handleDisconnect();
        }
      }
    }, PING_INTERVAL);
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function disconnect() {
    intentionalClose = true;
    cleanup();
    if (ws) {
      ws.close();
      ws = null;
    }
    handlers.onStateChange?.('disconnected');
  }

  function cleanup() {
    clearTimeout(timeoutId);
    clearInterval(pingId);
  }

  // Auto-connect
  connect();

  return { send, disconnect, reconnect: () => { retryCount = 0; connect(); } };
}
