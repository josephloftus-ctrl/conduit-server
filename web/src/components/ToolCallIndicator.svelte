<script>
  let { toolCall } = $props();
  let expanded = $state(false);

  const TOOL_ICONS = {
    // Conduit tools
    read_file: 'file',
    list_directory: 'folder',
    glob_files: 'search',
    grep: 'search',
    write_file: 'edit',
    edit_file: 'edit',
    run_command: 'terminal',
    // Claude Code tools
    Read: 'file',
    Write: 'edit',
    Edit: 'edit',
    Bash: 'terminal',
    Glob: 'search',
    Grep: 'search',
    WebFetch: 'search',
    WebSearch: 'search',
    Task: 'tool',
    NotebookEdit: 'edit',
  };

  let icon = $derived(TOOL_ICONS[toolCall.name] || 'tool');

  let summary = $derived.by(() => {
    const args = toolCall.arguments || {};
    if (args.path) return args.path;
    if (args.file_path) return args.file_path;
    if (args.pattern) return args.pattern;
    if (args.command) return args.command.slice(0, 60);
    if (args.query) return args.query.slice(0, 60);
    if (args.url) return args.url.slice(0, 60);
    return '';
  });

  let displayResult = $derived.by(() => {
    if (toolCall.error) return toolCall.error;
    if (toolCall.result) {
      return toolCall.result.length > 500
        ? toolCall.result.slice(0, 500) + '...'
        : toolCall.result;
    }
    return '';
  });
</script>

<div class="tool-call" class:running={toolCall.status === 'running'} class:error={toolCall.status === 'error'}>
  <button class="tool-header" onclick={() => expanded = !expanded}>
    <span class="tool-icon">
      {#if icon === 'file'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
        </svg>
      {:else if icon === 'folder'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
        </svg>
      {:else if icon === 'search'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
      {:else if icon === 'edit'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
        </svg>
      {:else if icon === 'terminal'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line>
        </svg>
      {:else}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
        </svg>
      {/if}
    </span>

    <span class="tool-name">{toolCall.name}</span>
    {#if summary}
      <span class="tool-args">{summary}</span>
    {/if}

    <span class="tool-status">
      {#if toolCall.status === 'running'}
        <span class="spinner"></span>
      {:else if toolCall.status === 'done'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green, #4caf80)" stroke-width="2.5">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
      {:else if toolCall.status === 'error'}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--red, #e05050)" stroke-width="2.5">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      {/if}
    </span>

    <span class="expand-icon" class:open={expanded}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </span>
  </button>

  {#if expanded && displayResult}
    <div class="tool-result">
      <pre>{displayResult}</pre>
    </div>
  {/if}
</div>

<style>
  .tool-call {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm, 6px);
    margin: 6px 0;
    overflow: hidden;
    font-size: 13px;
  }

  .tool-call.running {
    border-color: var(--accent, #6b8afd);
    opacity: 0.85;
  }

  .tool-call.error {
    border-color: var(--red, #e05050);
  }

  .tool-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    background: var(--bg-hover, #1e1e2e);
    border: none;
    color: var(--text-dim, #888);
    cursor: pointer;
    width: 100%;
    text-align: left;
    font-family: inherit;
    font-size: 13px;
  }

  .tool-header:hover {
    background: var(--bg-input, #252535);
  }

  .tool-icon {
    flex-shrink: 0;
    display: flex;
    align-items: center;
  }

  .tool-name {
    font-weight: 500;
    color: var(--text, #ddd);
    flex-shrink: 0;
  }

  .tool-args {
    color: var(--text-muted, #666);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .tool-status {
    flex-shrink: 0;
    display: flex;
    align-items: center;
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid var(--border);
    border-top-color: var(--accent, #6b8afd);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .expand-icon {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    transition: transform 0.15s;
  }

  .expand-icon.open {
    transform: rotate(180deg);
  }

  .tool-result {
    padding: 8px 10px;
    border-top: 1px solid var(--border);
    overflow-x: auto;
  }

  .tool-result pre {
    margin: 0;
    font-size: 12px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: pre-wrap;
    word-wrap: break-word;
    color: var(--text-dim, #888);
    line-height: 1.4;
  }
</style>
