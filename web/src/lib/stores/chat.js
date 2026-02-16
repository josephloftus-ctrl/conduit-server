/**
 * Chat store — messages, conversations, streaming state, tool calls, permissions.
 */
import { writable, derived, get } from 'svelte/store';
import { send, connectionState, setMessageHandler } from './connection.js';

export const conversations = writable([]);
export const currentConversationId = writable(null);
export const messages = writable([]);
export const isStreaming = writable(false);
export const isTyping = writable(false);

// Expose streaming state and manual unlock globally
if (typeof window !== 'undefined') {
  isStreaming.subscribe(v => { window.__streaming = v; });
  // Allow manual unlock from browser console: __forceUnlock()
  window.__forceUnlock = () => forceUnlockStream();
}
export const pushNotifications = writable([]);
export const lastMeta = writable(null);
export const pendingPermission = writable(null);

// Voice mode — auto-speak assistant responses
const savedVoiceMode = typeof localStorage !== 'undefined'
  ? localStorage.getItem('voiceMode') === 'true'
  : false;
export const voiceMode = writable(savedVoiceMode);
voiceMode.subscribe(v => {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('voiceMode', v ? 'true' : 'false');
  }
});

// Current streaming content accumulator
let streamBuffer = '';

// Generation counter — guards against stale WS events after conversation switch.
let generation = 0;
let streamGeneration = 0;
let expectingStream = false;

// Timestamp when streaming started — used for time-based auto-unlock
let streamStartedAt = 0;
const MAX_STREAM_DURATION = 20_000; // 20s hard limit

// Unique ID counter (avoids Date.now() collisions)
let idCounter = 0;
function uniqueId() {
  return `${Date.now()}-${++idCounter}`;
}

// Batched streaming updates via requestAnimationFrame
let rafId = null;
let pendingContent = null;

function flushStreamUpdate() {
  rafId = null;
  if (pendingContent === null) return;
  const content = pendingContent;
  pendingContent = null;
  messages.update(msgs => {
    const last = msgs[msgs.length - 1];
    if (last?.id === '_streaming') {
      const updated = msgs.slice();
      updated[updated.length - 1] = { ...last, content };
      return updated;
    }
    return msgs;
  });
}

function scheduleStreamUpdate(content) {
  pendingContent = content;
  if (rafId === null) {
    rafId = requestAnimationFrame(flushStreamUpdate);
  }
}

// --- Stream watchdog ---
// If no progress (typing/chunk) arrives for WATCHDOG_MS while streaming,
// force-unlock the input to prevent permanent lock on dropped connections.
const WATCHDOG_MS = 12_000;
let watchdogId = null;

function resetWatchdog() {
  clearTimeout(watchdogId);
  watchdogId = setTimeout(watchdogFire, WATCHDOG_MS);
}

function clearWatchdog() {
  clearTimeout(watchdogId);
  watchdogId = null;
}

function watchdogFire() {
  watchdogId = null;
  if (!get(isStreaming)) return; // already resolved
  console.warn('[chat] watchdog: no progress for', WATCHDOG_MS, 'ms — force-unlocking');
  forceUnlockStream();
}

/** Hard reset of all stream state — used by watchdog, connection-loss, and InputBar. */
export function forceUnlockStream() {
  expectingStream = false;
  isStreaming.set(false);
  isTyping.set(false);
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
    pendingContent = null;
  }
  // Finalize any message still marked as streaming
  messages.update(msgs => {
    const updated = msgs.slice();
    let changed = false;
    for (let i = updated.length - 1; i >= 0; i--) {
      if (updated[i].streaming || updated[i].id === '_streaming') {
        if (updated[i].content?.trim()) {
          updated[i] = { ...updated[i], id: updated[i].id === '_streaming' ? uniqueId() : updated[i].id, streaming: false };
        } else {
          updated.splice(i, 1);
        }
        changed = true;
      }
    }
    return changed ? updated : msgs;
  });
  streamBuffer = '';
  clearWatchdog();
}

// --- Connection-loss cleanup ---
// When the socket drops while streaming, force-unlock immediately.
connectionState.subscribe(state => {
  if (state === 'reconnecting' || state === 'failed' || state === 'disconnected') {
    if (get(isStreaming) || expectingStream) {
      console.warn('[chat] connection lost while streaming — force-unlocking');
      forceUnlockStream();
    }
  }
});

// --- Periodic self-heal ---
// Every 3s, check for stuck streaming state using elapsed time (not flags).
if (typeof window !== 'undefined') {
  setInterval(() => {
    if (!get(isStreaming)) return;
    const elapsed = Date.now() - streamStartedAt;
    if (elapsed > MAX_STREAM_DURATION || !expectingStream) {
      console.warn('[chat] self-heal: streaming stuck (elapsed=%dms, expecting=%s) — force-unlocking', elapsed, expectingStream);
      forceUnlockStream();
    }
  }, 3000);
}

// TTS playback
let currentAudio = null;

export async function speakResponse(text) {
  // Stop any currently playing audio
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if (!text?.trim()) return;
  try {
    const res = await fetch('/api/voice/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.slice(0, 4096) }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.onended = () => {
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
    currentAudio.onerror = () => {
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
    await currentAudio.play();
  } catch (err) {
    console.error('TTS playback failed:', err);
  }
}

export function stopSpeaking() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

// Ensure a _streaming placeholder exists (for chunks arriving without typing event)
function ensureStreamingPlaceholder() {
  const msgs = get(messages);
  const last = msgs[msgs.length - 1];
  if (last?.id === '_streaming') return; // already exists
  streamStartedAt = Date.now();
  isStreaming.set(true);
  messages.update(msgs => [...msgs, {
    id: '_streaming',
    role: 'assistant',
    content: '',
    model: null,
    streaming: true,
    toolCalls: [],
  }]);
}

// Remove orphaned _streaming message (on error or cleanup)
function removeStreamingPlaceholder() {
  messages.update(msgs => {
    const last = msgs[msgs.length - 1];
    if (last?.id === '_streaming') {
      // If it has content, finalize it; if empty, remove it
      if (last.content?.trim()) {
        const updated = msgs.slice();
        updated[updated.length - 1] = { ...last, id: uniqueId(), streaming: false };
        return updated;
      }
      return msgs.slice(0, -1);
    }
    return msgs;
  });
}

// Initialize message handler
setMessageHandler((msg) => {
  switch (msg.type) {
    case 'hello':
      break;

    case 'typing':
      if (!expectingStream) return;
      streamGeneration = generation;
      streamStartedAt = Date.now();
      isTyping.set(true);
      isStreaming.set(true);
      streamBuffer = '';
      ensureStreamingPlaceholder();
      resetWatchdog();
      break;

    case 'chunk': {
      if (streamGeneration !== generation) {
        if (!expectingStream) return;
        streamGeneration = generation;
      }
      isTyping.set(false);
      if (!get(isStreaming)) {
        ensureStreamingPlaceholder();
      }
      streamBuffer += msg.content;
      scheduleStreamUpdate(streamBuffer);
      resetWatchdog();
      break;
    }

    case 'done': {
      expectingStream = false;
      isStreaming.set(false);
      isTyping.set(false);
      clearWatchdog();
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
        pendingContent = null;
      }
      if (get(voiceMode) && streamBuffer) {
        speakResponse(streamBuffer);
      }
      // Finalize any message still marked as streaming
      messages.update(msgs => {
        const updated = msgs.slice();
        let changed = false;
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].streaming || updated[i].id === '_streaming') {
            updated[i] = {
              ...updated[i],
              id: updated[i].id === '_streaming' ? uniqueId() : updated[i].id,
              content: (i === updated.length - 1 && streamBuffer) ? streamBuffer : updated[i].content,
              streaming: false,
            };
            changed = true;
          }
        }
        return changed ? updated : msgs;
      });
      streamBuffer = '';
      streamGeneration = -1;
      break;
    }

    case 'meta':
      // Safety net: meta arrives right after done — clear any leftover streaming state
      if (get(isStreaming)) {
        console.warn('[chat] meta: isStreaming still true — force-clearing');
        forceUnlockStream();
      }
      lastMeta.set({
        model: msg.model,
        inputTokens: msg.input_tokens,
        outputTokens: msg.output_tokens,
      });
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.role === 'assistant') {
          const updated = msgs.slice();
          updated[updated.length - 1] = { ...last, model: msg.model };
          return updated;
        }
        return msgs;
      });
      break;

    case 'tool_start':
      if (streamGeneration !== generation) return; // stale stream
      ensureStreamingPlaceholder();
      resetWatchdog();
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.id === '_streaming') {
          const toolCalls = [...(last.toolCalls || []), {
            id: msg.tool_call_id,
            name: msg.name,
            arguments: msg.arguments,
            status: 'running',
            result: null,
            error: null,
          }];
          const updated = msgs.slice();
          updated[updated.length - 1] = { ...last, toolCalls };
          return updated;
        }
        return msgs;
      });
      break;

    case 'tool_done':
      if (streamGeneration !== generation) return; // stale stream
      resetWatchdog();
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.id === '_streaming' && last.toolCalls) {
          const toolCalls = last.toolCalls.map(tc => {
            if (tc.id === msg.tool_call_id) {
              return {
                ...tc,
                status: msg.error ? 'error' : 'done',
                result: msg.result || null,
                error: msg.error || null,
              };
            }
            return tc;
          });
          const updated = msgs.slice();
          updated[updated.length - 1] = { ...last, toolCalls };
          return updated;
        }
        return msgs;
      });
      break;

    case 'permission':
      pendingPermission.set({
        id: msg.id,
        action: msg.action,
        detail: msg.detail,
      });
      break;

    case 'error':
      expectingStream = false;
      isStreaming.set(false);
      isTyping.set(false);
      clearWatchdog();
      // Cancel pending rAF
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
        pendingContent = null;
      }
      // Finalize or remove orphaned _streaming message before appending error
      removeStreamingPlaceholder();
      messages.update(msgs => [...msgs, {
        id: uniqueId(),
        role: 'error',
        content: msg.message,
      }]);
      streamBuffer = '';
      break;

    case 'push':
      pushNotifications.update(ns => [...ns, {
        id: uniqueId(),
        title: msg.title || 'Notification',
        content: msg.content,
      }]);
      break;

    case 'conversation_updated':
      // Update the title in the local list without a full refetch
      conversations.update(convs =>
        convs.map(c => c.id === msg.id ? { ...c, title: msg.title } : c)
      );
      break;

    case 'conversation_changed':
      // Server switched conversation (e.g. /clear command)
      generation++;
      expectingStream = false;
      streamBuffer = '';
      clearWatchdog();
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
        pendingContent = null;
      }
      isStreaming.set(false);
      isTyping.set(false);
      currentConversationId.set(msg.conversation_id);
      messages.set([]);
      loadConversations();
      break;

    case 'conversation_deleted':
      conversations.update(convs => convs.filter(c => c.id !== msg.id));
      // If the deleted conversation was active, clear to fresh state
      if (get(currentConversationId) === msg.id) {
        currentConversationId.set(null);
        messages.set([]);
      }
      break;
  }
});

/** Send a message. Returns true if sent, false if connection is down. */
export function sendMessage(content) {
  if (!content.trim()) return false;

  // Check connection state before sending
  const state = get(connectionState);
  if (state !== 'ready') {
    messages.update(msgs => [...msgs, {
      id: uniqueId(),
      role: 'error',
      content: 'Not connected to server. Reconnecting...',
    }]);
    return false;
  }

  // If still streaming from previous message, quietly clear flags
  // (don't call forceUnlockStream — it modifies messages and causes
  // auto-scroll which steals focus on mobile)
  if (get(isStreaming)) {
    expectingStream = false;
    isStreaming.set(false);
    isTyping.set(false);
    clearWatchdog();
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
      pendingContent = null;
    }
    streamBuffer = '';
  }

  // Try WS send FIRST — only add user message if it actually went through
  const sent = send({ type: 'message', content: content.trim() });
  if (!sent) {
    messages.update(msgs => [...msgs, {
      id: uniqueId(),
      role: 'error',
      content: 'Message failed to send. Check your connection.',
    }]);
    return false;
  }

  // Success — add user message and expect a stream back
  messages.update(msgs => [...msgs, {
    id: uniqueId(),
    role: 'user',
    content: content.trim(),
  }]);
  expectingStream = true;
  streamStartedAt = Date.now();
  return true;
}

export function respondToPermission(id, granted) {
  send({ type: 'permission_response', id, granted });
  pendingPermission.set(null);
}

export function switchConversation(id) {
  // Bump generation to ignore stale WS events from previous conversation
  generation++;
  expectingStream = false;
  const myGen = generation;

  streamBuffer = '';
  clearWatchdog();
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
    pendingContent = null;
  }
  isStreaming.set(false);
  isTyping.set(false);

  currentConversationId.set(id);
  messages.set([]);
  send({ type: 'set_conversation', conversation_id: id });

  // Load messages from API
  fetch(`/api/conversations/${id}/messages`)
    .then(r => r.json())
    .then(msgs => {
      // Guard: ignore if user switched away again
      if (generation !== myGen) return;
      messages.set(msgs.map(m => ({
        id: m.id,
        role: m.role,
        content: m.content,
        model: m.model,
      })));
    })
    .catch(() => {});
}

export async function newConversation() {
  // Bump generation
  generation++;
  expectingStream = false;

  streamBuffer = '';
  clearWatchdog();
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
    pendingContent = null;
  }
  isStreaming.set(false);
  isTyping.set(false);

  messages.set([]);

  // Create a new server-side conversation and tell the WS to use it
  try {
    const res = await fetch('/api/conversations', { method: 'POST' });
    const data = await res.json();
    if (data.id) {
      currentConversationId.set(data.id);
      send({ type: 'set_conversation', conversation_id: data.id });
      loadConversations();
    }
  } catch {
    // If create fails, the server still holds its current ws.conversation_id.
    // Don't reset — messages will continue in the existing conversation.
  }
}

export function dismissPush(id) {
  pushNotifications.update(ns => ns.filter(n => n.id !== id));
}

export async function renameConversation(id, title) {
  try {
    await fetch(`/api/conversations/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
  } catch {
    // WS broadcast will update the list
  }
}

export async function deleteConversation(id) {
  try {
    await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
  } catch {
    // WS broadcast will update the list
  }
}

export async function loadConversations() {
  try {
    const res = await fetch('/api/conversations');
    const data = await res.json();
    conversations.set(data);
  } catch {
    // Server might not be available yet
  }
}
