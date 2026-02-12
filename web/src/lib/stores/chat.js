/**
 * Chat store — messages, conversations, streaming state, tool calls, permissions.
 */
import { writable, derived, get } from 'svelte/store';
import { send, setMessageHandler } from './connection.js';

export const conversations = writable([]);
export const currentConversationId = writable(null);
export const messages = writable([]);
export const isStreaming = writable(false);
export const isTyping = writable(false);
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

// Initialize message handler
setMessageHandler((msg) => {
  switch (msg.type) {
    case 'hello':
      // Connection established
      break;

    case 'typing':
      isTyping.set(true);
      isStreaming.set(true);
      streamBuffer = '';
      // Add placeholder assistant message
      messages.update(msgs => [...msgs, {
        id: '_streaming',
        role: 'assistant',
        content: '',
        model: null,
        streaming: true,
        toolCalls: [],
      }]);
      break;

    case 'chunk':
      isTyping.set(false);
      streamBuffer += msg.content;
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.id === '_streaming') {
          return [...msgs.slice(0, -1), { ...last, content: streamBuffer }];
        }
        return msgs;
      });
      break;

    case 'done':
      isStreaming.set(false);
      isTyping.set(false);
      // Auto-speak if voice mode is on
      if (get(voiceMode) && streamBuffer) {
        speakResponse(streamBuffer);
      }
      // Finalize the streaming message
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.id === '_streaming') {
          return [...msgs.slice(0, -1), {
            ...last,
            id: Date.now().toString(),
            streaming: false,
          }];
        }
        return msgs;
      });
      streamBuffer = '';
      break;

    case 'meta':
      lastMeta.set({
        model: msg.model,
        inputTokens: msg.input_tokens,
        outputTokens: msg.output_tokens,
      });
      // Attach model to last assistant message
      messages.update(msgs => {
        const last = msgs[msgs.length - 1];
        if (last?.role === 'assistant') {
          return [...msgs.slice(0, -1), { ...last, model: msg.model }];
        }
        return msgs;
      });
      break;

    case 'tool_start':
      // Add a tool call entry to the current streaming message
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
          return [...msgs.slice(0, -1), { ...last, toolCalls }];
        }
        return msgs;
      });
      break;

    case 'tool_done':
      // Update tool call status
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
          return [...msgs.slice(0, -1), { ...last, toolCalls }];
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
      isStreaming.set(false);
      isTyping.set(false);
      messages.update(msgs => [...msgs, {
        id: Date.now().toString(),
        role: 'error',
        content: msg.message,
      }]);
      break;

    case 'push':
      pushNotifications.update(ns => [...ns, {
        id: Date.now(),
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

export function sendMessage(content) {
  if (!content.trim()) return;

  // Add user message locally
  messages.update(msgs => [...msgs, {
    id: Date.now().toString(),
    role: 'user',
    content: content.trim(),
  }]);

  // Send over WebSocket
  send({ type: 'message', content: content.trim() });
}

export function respondToPermission(id, granted) {
  send({ type: 'permission_response', id, granted });
  pendingPermission.set(null);
}

export function switchConversation(id) {
  currentConversationId.set(id);
  messages.set([]);
  send({ type: 'set_conversation', conversation_id: id });

  // Load messages from API
  fetch(`/api/conversations/${id}/messages`)
    .then(r => r.json())
    .then(msgs => {
      messages.set(msgs.map(m => ({
        id: m.id,
        role: m.role,
        content: m.content,
        model: m.model,
      })));
    })
    .catch(() => {});
}

export function newConversation() {
  messages.set([]);
  currentConversationId.set(null);
  // Server creates new conversation on next message
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
