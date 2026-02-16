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
  let reconnectTimerId = null;
  // Prevents timeout + onclose from both calling handleDisconnect
  let disconnectHandled = false;

  function connect() {
    cleanup();
    intentionalClose = false;
    disconnectHandled = false;

    handlers.onStateChange?.('connecting');
    ws = new WebSocket(url);

    // Connection timeout
    timeoutId = setTimeout(() => {
      if (ws && ws.readyState !== WebSocket.OPEN) {
        intentionalClose = true;
        disconnectHandled = true;
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
        try {
          handlers.onMessage?.(msg);
        } catch (handlerErr) {
          console.error('WS message handler error:', handlerErr, 'for message type:', msg.type);
        }
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
      if (!intentionalClose && !disconnectHandled) {
        disconnectHandled = true;
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

    // Clear any existing reconnect timer to prevent duplicates
    clearTimeout(reconnectTimerId);
    reconnectTimerId = setTimeout(connect, delay);
  }

  function startPing() {
    clearInterval(pingId);
    pingId = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          // Don't call handleDisconnect here — onclose will fire
        }
      }
    }, PING_INTERVAL);
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
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
    clearTimeout(reconnectTimerId);
    clearInterval(pingId);
  }

  // Auto-connect
  connect();

  return { send, disconnect, reconnect: () => { retryCount = 0; connect(); } };
}
