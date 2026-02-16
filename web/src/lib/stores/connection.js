/**
 * Connection state store — wraps WebSocket client.
 */
import { writable } from 'svelte/store';
import { createWebSocket } from '../ws.js';

export const connectionState = writable('disconnected'); // disconnected | connecting | ready | reconnecting | failed
export const errorMessage = writable('');

let client = null;
let messageHandler = null;

export function setMessageHandler(fn) {
  messageHandler = fn;
}

export function connect(url) {
  if (client) client.disconnect();

  client = createWebSocket(url, {
    onStateChange(state) {
      connectionState.set(state);
      if (state === 'ready') errorMessage.set('');
    },
    onMessage(msg) {
      messageHandler?.(msg);
    },
    onError(err) {
      errorMessage.set(err);
    },
  });
}

export function send(msg) {
  return client?.send(msg) ?? false;
}

export function disconnect() {
  client?.disconnect();
  client = null;
}

export function reconnect() {
  client?.reconnect();
}
