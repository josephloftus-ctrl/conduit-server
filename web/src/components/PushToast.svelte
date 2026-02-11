<script>
  import { pushNotifications, dismissPush } from '../lib/stores/chat.js';
  import { marked } from 'marked';

  // Auto-dismiss after 15 seconds
  $effect(() => {
    const ns = $pushNotifications;
    if (ns.length > 0) {
      const latest = ns[ns.length - 1];
      const timer = setTimeout(() => dismissPush(latest.id), 15000);
      return () => clearTimeout(timer);
    }
  });
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
        <div class="toast-body">{@html marked.parse(notif.content || '')}</div>
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
  }

  .toast-body :global(p) { margin: 0.3em 0; }
  .toast-body :global(strong) { color: var(--text); }

  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }
</style>
