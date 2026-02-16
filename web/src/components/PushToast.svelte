<script>
  import { pushNotifications, dismissPush } from '../lib/stores/chat.js';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  // Auto-dismiss each notification after 15 seconds
  // Track timers per-notification to handle bursts correctly
  const dismissTimers = new Map();

  $effect(() => {
    const ns = $pushNotifications;
    // Set timers for any new notifications
    for (const notif of ns) {
      if (!dismissTimers.has(notif.id)) {
        const timer = setTimeout(() => {
          dismissTimers.delete(notif.id);
          dismissPush(notif.id);
        }, 15000);
        dismissTimers.set(notif.id, timer);
      }
    }
    // Clean up timers for dismissed notifications
    for (const [id, timer] of dismissTimers) {
      if (!ns.find(n => n.id === id)) {
        clearTimeout(timer);
        dismissTimers.delete(id);
      }
    }
  });

  function sanitize(content) {
    return DOMPurify.sanitize(marked.parse(content || ''));
  }
</script>

{#if $pushNotifications.length > 0}
  <div class="toast-container">
    {#each $pushNotifications as notif (notif.id)}
      <div class="toast" role="alert">
        <div class="toast-header">
          <span class="toast-icon">⚡</span>
          <span class="toast-title">{notif.title}</span>
          <button class="toast-close" onclick={() => dismissPush(notif.id)} aria-label="Dismiss">&times;</button>
        </div>
        <div class="toast-body">{@html sanitize(notif.content)}</div>
      </div>
    {/each}
  </div>
{/if}

<style>
  .toast-container {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 100;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 400px;
  }

  .toast {
    background: var(--bg-surface);
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    padding: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    animation: slideIn 0.3s ease;
  }

  .toast-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }

  .toast-icon { font-size: 16px; }

  .toast-title {
    font-weight: 600;
    font-size: 14px;
    flex: 1;
  }

  .toast-close {
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }
  .toast-close:hover { color: var(--text); }

  .toast-body {
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.5;
    max-height: 200px;
    overflow-y: auto;
    overflow-wrap: break-word;
  }

  .toast-body :global(p) { margin: 0.3em 0; }
  .toast-body :global(strong) { color: var(--text); }

  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }
</style>
