<script>
  import { pendingPermission, respondToPermission } from '../lib/stores/chat.js';

  let permission = $derived($pendingPermission);

  function approve() {
    if (permission) respondToPermission(permission.id, true);
  }

  function deny() {
    if (permission) respondToPermission(permission.id, false);
  }

  let actionLabel = $derived.by(() => {
    if (!permission) return '';
    const [type, tool] = permission.action.split(':');
    if (type === 'write') return `Write: ${tool}`;
    if (type === 'execute') return `Execute: ${tool}`;
    return permission.action;
  });

  let detailText = $derived.by(() => {
    if (!permission) return '';
    const d = permission.detail;
    if (d.command) return d.command;
    if (d.path && d.content) return `${d.path}\n\n${d.content.slice(0, 200)}${d.content.length > 200 ? '...' : ''}`;
    if (d.path && d.old_text) return `${d.path}\n- ${d.old_text}\n+ ${d.new_text}`;
    if (d.path) return d.path;
    return JSON.stringify(d, null, 2);
  });
</script>

{#if permission}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="perm-overlay" role="presentation" onclick={deny}></div>
  <div class="perm-modal">
    <div class="perm-header">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--yellow, #e0c050)" stroke-width="2">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
      </svg>
      <span>Permission Required</span>
    </div>

    <div class="perm-action">{actionLabel}</div>

    <div class="perm-detail">
      <pre>{detailText}</pre>
    </div>

    <div class="perm-buttons">
      <button class="btn-deny" onclick={deny}>Deny</button>
      <button class="btn-approve" onclick={approve}>Approve</button>
    </div>
  </div>
{/if}

<style>
  .perm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    z-index: 200;
  }

  .perm-modal {
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    width: min(420px, 90vw);
    background: var(--bg-surface, #1a1a2e);
    border: 1px solid var(--border, #333);
    border-radius: var(--radius, 8px);
    z-index: 201;
    overflow: hidden;
  }

  .perm-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    background: var(--bg-hover, #1e1e2e);
    font-weight: 600;
    font-size: 14px;
    color: var(--text, #ddd);
  }

  .perm-action {
    padding: 8px 16px;
    font-size: 13px;
    color: var(--accent, #6b8afd);
    font-weight: 500;
  }

  .perm-detail {
    padding: 0 16px 12px;
    max-height: 200px;
    overflow-y: auto;
  }

  .perm-detail pre {
    margin: 0;
    font-size: 12px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: pre-wrap;
    word-wrap: break-word;
    color: var(--text-dim, #888);
    background: var(--bg, #111);
    padding: 8px 10px;
    border-radius: var(--radius-sm, 6px);
    line-height: 1.4;
  }

  .perm-buttons {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border, #333);
    justify-content: flex-end;
  }

  .btn-deny {
    padding: 8px 20px;
    background: var(--bg-hover, #1e1e2e);
    color: var(--text-dim, #888);
    border: 1px solid var(--border, #333);
    border-radius: var(--radius-sm, 6px);
    cursor: pointer;
    font-size: 13px;
  }

  .btn-deny:hover {
    color: var(--text, #ddd);
    border-color: var(--text-dim, #888);
  }

  .btn-approve {
    padding: 8px 20px;
    background: var(--accent, #6b8afd);
    color: #fff;
    border: none;
    border-radius: var(--radius-sm, 6px);
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
  }

  .btn-approve:hover {
    opacity: 0.85;
  }
</style>
