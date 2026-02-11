<script>
  import { conversations, currentConversationId, switchConversation, newConversation, loadConversations } from '../lib/stores/chat.js';
  import { onMount } from 'svelte';

  let { open = $bindable(false), onOpenSettings } = $props();

  onMount(() => {
    loadConversations();
  });

  function handleNew() {
    newConversation();
    open = false;
  }

  function handleSelect(id) {
    switchConversation(id);
    open = false;
  }

  function handleSettings() {
    onOpenSettings?.();
  }

  function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
</script>

<aside class="sidebar" class:open>
  <div class="sidebar-header">
    <h2>Conversations</h2>
    <button class="new-btn" onclick={handleNew}>+ New</button>
  </div>

  <div class="conversation-list">
    {#each $conversations as conv (conv.id)}
      <button
        class="conv-item"
        class:active={$currentConversationId === conv.id}
        onclick={() => handleSelect(conv.id)}
      >
        <span class="conv-title">{conv.title}</span>
        <span class="conv-date">{formatDate(conv.updated_at)}</span>
      </button>
    {/each}

    {#if $conversations.length === 0}
      <div class="empty">No conversations yet</div>
    {/if}
  </div>

  <div class="sidebar-footer">
    <button class="settings-btn" onclick={handleSettings}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
      </svg>
      Settings
    </button>
  </div>
</aside>

{#if open}
  <button class="overlay" onclick={() => open = false} aria-label="Close sidebar"></button>
{/if}

<style>
  .sidebar {
    width: 280px;
    background: var(--bg-surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    height: 100%;
    transition: margin-left 0.2s ease;
    margin-left: -280px;
    z-index: 20;
    position: absolute;
  }

  .sidebar.open {
    margin-left: 0;
  }

  @media (min-width: 768px) {
    .sidebar {
      position: relative;
    }
    .sidebar.open {
      margin-left: 0;
    }
  }

  .sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    font-size: 15px;
    font-weight: 600;
  }

  .new-btn {
    padding: 4px 12px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    font-weight: 500;
  }
  .new-btn:hover { opacity: 0.85; }

  .conversation-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  .conv-item {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text);
    cursor: pointer;
    text-align: left;
    font-size: 14px;
    transition: background 0.15s;
  }
  .conv-item:hover { background: var(--bg-hover); }
  .conv-item.active { background: var(--bg-hover); }

  .conv-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .conv-date {
    color: var(--text-muted);
    font-size: 12px;
    flex-shrink: 0;
    margin-left: 8px;
  }

  .empty {
    text-align: center;
    padding: 24px;
    color: var(--text-muted);
    font-size: 14px;
  }

  .sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--border);
    flex-shrink: 0;
  }

  .settings-btn {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    cursor: pointer;
    font-size: 14px;
    transition: background 0.15s, color 0.15s;
  }
  .settings-btn:hover { background: var(--bg-hover); color: var(--text); }

  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.4);
    z-index: 15;
    border: none;
    cursor: default;
  }

  @media (min-width: 768px) {
    .overlay { display: none; }
  }
</style>
