<script>
  import { sendMessage, isStreaming } from '../lib/stores/chat.js';

  let input = $state('');
  let textarea = $state(null);

  const COMMAND_HINTS = [
    { cmd: '/help', desc: 'Show commands' },
    { cmd: '/opus', desc: 'Use Opus (budget-capped)' },
    { cmd: '/think', desc: 'Deep thinking with Opus' },
    { cmd: '/research', desc: 'Use Gemini' },
    { cmd: '/remind', desc: 'Set a reminder' },
    { cmd: '/models', desc: 'List providers' },
    { cmd: '/usage', desc: 'Check Opus budget' },
    { cmd: '/clear', desc: 'New conversation' },
    { cmd: '/schedule', desc: 'View scheduled tasks' },
  ];

  let showHints = $derived(input.startsWith('/') && !input.includes(' '));
  let filteredHints = $derived(
    COMMAND_HINTS.filter(h => h.cmd.startsWith(input.toLowerCase()))
  );

  function handleSubmit() {
    if (!input.trim() || $isStreaming) return;
    sendMessage(input);
    input = '';
    autoResize();
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    if (e.key === 'Tab' && showHints && filteredHints.length > 0) {
      e.preventDefault();
      input = filteredHints[0].cmd + ' ';
    }
  }

  function autoResize() {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  }

  function selectHint(cmd) {
    input = cmd + ' ';
    textarea?.focus();
  }
</script>

<div class="input-bar">
  {#if showHints && filteredHints.length > 0}
    <div class="hints">
      {#each filteredHints as hint}
        <button class="hint" onclick={() => selectHint(hint.cmd)}>
          <span class="hint-cmd">{hint.cmd}</span>
          <span class="hint-desc">{hint.desc}</span>
        </button>
      {/each}
    </div>
  {/if}

  <div class="input-row">
    <textarea
      bind:this={textarea}
      bind:value={input}
      oninput={autoResize}
      onkeydown={handleKeydown}
      placeholder="Message Conduit..."
      rows="1"
      disabled={$isStreaming}
    ></textarea>

    <button
      class="send-btn"
      onclick={handleSubmit}
      disabled={!input.trim() || $isStreaming}
      aria-label="Send message"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>
    </button>
  </div>
</div>

<style>
  .input-bar {
    padding: 8px 16px 16px;
    max-width: 800px;
    margin: 0 auto;
    width: 100%;
  }

  .hints {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 6px 0;
  }

  .hint {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    cursor: pointer;
    font-size: 13px;
    transition: background 0.15s;
  }
  .hint:hover { background: var(--bg-hover); }
  .hint-cmd { color: var(--accent); font-weight: 500; }
  .hint-desc { color: var(--text-dim); }

  .input-row {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 8px 8px 8px 14px;
    transition: border-color 0.2s;
  }
  .input-row:focus-within {
    border-color: var(--accent);
  }

  textarea {
    flex: 1;
    background: transparent;
    border: none;
    outline: none;
    color: var(--text);
    font-size: 15px;
    font-family: inherit;
    line-height: 1.5;
    resize: none;
    max-height: 200px;
  }
  textarea::placeholder { color: var(--text-muted); }
  textarea:disabled { opacity: 0.5; }

  .send-btn {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--accent);
    border: none;
    border-radius: 8px;
    color: #fff;
    cursor: pointer;
    flex-shrink: 0;
    transition: opacity 0.15s;
  }
  .send-btn:hover { opacity: 0.85; }
  .send-btn:disabled { opacity: 0.3; cursor: default; }
</style>
