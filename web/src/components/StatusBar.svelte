<script>
  import { connectionState } from '../lib/stores/connection.js';
  import { lastMeta, voiceMode, stopSpeaking } from '../lib/stores/chat.js';

  function toggleVoice() {
    voiceMode.update(v => {
      if (v) stopSpeaking();
      return !v;
    });
  }

  let { onToggleSidebar, onOpenSettings } = $props();

  let statusColor = $derived({
    ready: 'var(--green)',
    connecting: 'var(--orange)',
    reconnecting: 'var(--orange)',
    failed: 'var(--red)',
    disconnected: 'var(--text-muted)',
  }[$connectionState] || 'var(--text-muted)');

  let statusText = $derived({
    ready: 'Connected',
    connecting: 'Connecting...',
    reconnecting: 'Reconnecting...',
    failed: 'Disconnected',
    disconnected: 'Offline',
  }[$connectionState] || $connectionState);
</script>

<div class="status-bar">
  <button class="menu-btn" onclick={onToggleSidebar} aria-label="Toggle sidebar">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <line x1="3" y1="6" x2="21" y2="6"></line>
      <line x1="3" y1="12" x2="21" y2="12"></line>
      <line x1="3" y1="18" x2="21" y2="18"></line>
    </svg>
  </button>

  <div class="title">Conduit</div>

  <div class="right">
    {#if $lastMeta}
      <span class="model-indicator">{$lastMeta.model?.split('/').pop() || ''}</span>
    {/if}

    <div class="status" style="color: {statusColor}">
      <span class="dot" style="background: {statusColor}"></span>
      {statusText}
    </div>

    <button
      class="voice-btn"
      class:active={$voiceMode}
      onclick={toggleVoice}
      aria-label={$voiceMode ? 'Disable voice mode' : 'Enable voice mode'}
      title={$voiceMode ? 'Voice mode ON' : 'Voice mode OFF'}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        {#if $voiceMode}
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
          <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
        {:else}
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
          <line x1="23" y1="9" x2="17" y2="15"></line>
          <line x1="17" y1="9" x2="23" y2="15"></line>
        {/if}
      </svg>
    </button>

    <button class="gear-btn" onclick={onOpenSettings} aria-label="Settings">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
      </svg>
    </button>
  </div>
</div>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-surface);
    gap: 12px;
    flex-shrink: 0;
  }

  .menu-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
    display: flex;
    align-items: center;
  }
  .menu-btn:hover { color: var(--text); }

  .title {
    font-weight: 600;
    font-size: 16px;
  }

  .right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .model-indicator {
    font-size: 12px;
    padding: 2px 8px;
    background: var(--bg-hover);
    border-radius: 4px;
    color: var(--text-dim);
  }

  .status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .voice-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
    display: flex;
    align-items: center;
    border-radius: 4px;
    transition: all 0.15s;
  }
  .voice-btn:hover { color: var(--text); }
  .voice-btn.active {
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 15%, transparent);
  }

  .gear-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
    display: flex;
    align-items: center;
  }
  .gear-btn:hover { color: var(--text); }
</style>
