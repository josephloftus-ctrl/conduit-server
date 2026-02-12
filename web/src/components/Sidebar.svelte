<script>
  import { conversations, currentConversationId, switchConversation, newConversation, loadConversations, renameConversation, deleteConversation } from '../lib/stores/chat.js';
  import { onMount } from 'svelte';

  let { open = $bindable(false), onOpenSettings } = $props();

  let searchQuery = $state('');
  let editingId = $state(null);
  let editValue = $state('');
  let confirmDeleteId = $state(null);
  let confirmTimeout = null;

  onMount(() => {
    loadConversations();
  });

  // Filter conversations by search query
  let filtered = $derived(
    searchQuery.trim()
      ? $conversations.filter(c =>
          c.title.toLowerCase().includes(searchQuery.trim().toLowerCase())
        )
      : $conversations
  );

  // Group filtered conversations by date
  let grouped = $derived.by(() => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const weekAgo = new Date(today); weekAgo.setDate(today.getDate() - 7);

    const groups = { today: [], yesterday: [], week: [], older: [] };

    for (const conv of filtered) {
      const d = new Date(conv.updated_at * 1000);
      if (d >= today) groups.today.push(conv);
      else if (d >= yesterday) groups.yesterday.push(conv);
      else if (d >= weekAgo) groups.week.push(conv);
      else groups.older.push(conv);
    }

    const result = [];
    if (groups.today.length) result.push({ label: 'Today', convs: groups.today });
    if (groups.yesterday.length) result.push({ label: 'Yesterday', convs: groups.yesterday });
    if (groups.week.length) result.push({ label: 'Last 7 Days', convs: groups.week });
    if (groups.older.length) result.push({ label: 'Older', convs: groups.older });
    return result;
  });

  function handleNew() {
    newConversation();
    open = false;
  }

  function handleSelect(id) {
    if (editingId) return;
    switchConversation(id);
    open = false;
  }

  function handleSettings() {
    onOpenSettings?.();
  }

  function startEdit(conv) {
    editingId = conv.id;
    editValue = conv.title;
  }

  function saveEdit() {
    if (editingId && editValue.trim()) {
      renameConversation(editingId, editValue.trim());
    }
    editingId = null;
    editValue = '';
  }

  function cancelEdit() {
    editingId = null;
    editValue = '';
  }

  function handleEditKeydown(e) {
    if (e.key === 'Enter') { e.preventDefault(); saveEdit(); }
    else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
  }

  function handleDelete(id) {
    if (confirmDeleteId === id) {
      // Second click — delete
      clearTimeout(confirmTimeout);
      confirmDeleteId = null;
      deleteConversation(id);
    } else {
      // First click — enter confirm state
      confirmDeleteId = id;
      clearTimeout(confirmTimeout);
      confirmTimeout = setTimeout(() => { confirmDeleteId = null; }, 3000);
    }
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

  <div class="search-box">
    <input
      type="text"
      placeholder="Search..."
      bind:value={searchQuery}
    />
  </div>

  <div class="conversation-list">
    {#each grouped as group (group.label)}
      <div class="group-label">{group.label}</div>
      {#each group.convs as conv (conv.id)}
        <div
          class="conv-item"
          class:active={$currentConversationId === conv.id}
        >
          {#if editingId === conv.id}
            <input
              class="edit-input"
              type="text"
              bind:value={editValue}
              onkeydown={handleEditKeydown}
              onblur={saveEdit}
              autofocus
            />
          {:else}
            <button
              class="conv-btn"
              onclick={() => handleSelect(conv.id)}
              ondblclick={() => startEdit(conv)}
            >
              <span class="conv-title">{conv.title}</span>
              <span class="conv-date">{formatDate(conv.updated_at)}</span>
            </button>
            <button
              class="delete-btn"
              class:confirm={confirmDeleteId === conv.id}
              onclick={(e) => { e.stopPropagation(); handleDelete(conv.id); }}
              title={confirmDeleteId === conv.id ? 'Click again to delete' : 'Delete conversation'}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
                <path d="M10 11v6"></path>
                <path d="M14 11v6"></path>
              </svg>
            </button>
          {/if}
        </div>
      {/each}
    {/each}

    {#if $conversations.length === 0}
      <div class="empty">No conversations yet</div>
    {:else if filtered.length === 0}
      <div class="empty">No matches</div>
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

  .search-box {
    padding: 8px 12px 4px;
  }

  .search-box input {
    width: 100%;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
    outline: none;
    box-sizing: border-box;
  }
  .search-box input:focus {
    border-color: var(--accent);
  }
  .search-box input::placeholder {
    color: var(--text-muted);
  }

  .conversation-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px 8px;
  }

  .group-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 10px 12px 4px;
  }

  .conv-item {
    display: flex;
    align-items: center;
    border-radius: var(--radius-sm);
    transition: background 0.15s;
    position: relative;
  }
  .conv-item:hover { background: var(--bg-hover); }
  .conv-item.active { background: var(--bg-hover); }

  .conv-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    background: transparent;
    border: none;
    color: var(--text);
    cursor: pointer;
    text-align: left;
    font-size: 14px;
    min-width: 0;
  }

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

  .edit-input {
    flex: 1;
    margin: 4px;
    padding: 6px 8px;
    border: 1px solid var(--accent);
    border-radius: 4px;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    outline: none;
    box-sizing: border-box;
  }

  .delete-btn {
    display: none;
    padding: 6px;
    margin-right: 4px;
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    border-radius: 4px;
    flex-shrink: 0;
    transition: color 0.15s, background 0.15s;
  }
  .conv-item:hover .delete-btn {
    display: flex;
  }
  .delete-btn:hover {
    background: var(--bg-hover);
    color: var(--text);
  }
  .delete-btn.confirm {
    display: flex;
    color: #ef4444;
  }
  .delete-btn.confirm:hover {
    background: rgba(239, 68, 68, 0.1);
    color: #ef4444;
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
