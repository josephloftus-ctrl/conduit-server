<script>
  import { onMount } from 'svelte';

  let { open = $bindable(false) } = $props();

  let settings = $state(null);
  let memories = $state([]);
  let usage = $state(null);
  let activeTab = $state('providers');
  let loading = $state(true);
  let saving = $state(false);
  let testResult = $state(null);

  // Editable copies
  let personalityName = $state('');
  let personalityPrompt = $state('');
  let routingDefault = $state('');
  let routingBudget = $state(50000);
  let complexityThreshold = $state(60);
  let longContextChars = $state(3000);
  let activeHoursStart = $state(7);
  let activeHoursEnd = $state(22);
  let heartbeatInterval = $state(15);
  let idleCheckin = $state(120);
  let maxMemories = $state(200);
  let summaryThreshold = $state(30);
  let extractionEnabled = $state(true);
  let toolsEnabled = $state(true);
  let maxAgentTurns = $state(10);
  let commandTimeout = $state(30);
  let allowedDirs = $state('');
  let ntfyEnabled = $state(true);
  let ntfyServer = $state('');
  let ntfyTopic = $state('');

  const tabs = [
    { id: 'providers', label: 'Providers' },
    { id: 'routing', label: 'Routing' },
    { id: 'personality', label: 'Personality' },
    { id: 'tools', label: 'Tools' },
    { id: 'schedule', label: 'Schedule' },
    { id: 'memory', label: 'Memory' },
    { id: 'notifications', label: 'Notifications' },
    { id: 'usage', label: 'Usage' },
  ];

  onMount(() => {
    if (open) loadAll();
  });

  $effect(() => {
    if (open && !settings) loadAll();
  });

  async function loadAll() {
    loading = true;
    try {
      const [s, m, u] = await Promise.all([
        fetch('/api/settings').then(r => r.json()),
        fetch('/api/memories').then(r => r.json()),
        fetch('/api/settings/usage').then(r => r.json()),
      ]);
      settings = s;
      memories = m || [];
      usage = u;

      // Populate editable fields
      personalityName = s.personality?.name || '';
      personalityPrompt = s.personality?.system_prompt || '';
      routingDefault = s.routing?.default || 'nim';
      routingBudget = s.routing?.opus_daily_budget_tokens || 50000;
      complexityThreshold = s.classifier?.complexity_threshold || 60;
      longContextChars = s.classifier?.long_context_chars || 3000;
      activeHoursStart = s.scheduler?.active_hours?.[0] || 7;
      activeHoursEnd = s.scheduler?.active_hours?.[1] || 22;
      heartbeatInterval = s.scheduler?.heartbeat_interval_minutes || 15;
      idleCheckin = s.scheduler?.idle_checkin_minutes || 120;
      maxMemories = s.memory?.max_memories || 200;
      summaryThreshold = s.memory?.summary_threshold || 30;
      extractionEnabled = s.memory?.extraction_enabled !== false;
      toolsEnabled = s.tools?.enabled !== false;
      maxAgentTurns = s.tools?.max_agent_turns || 10;
      commandTimeout = s.tools?.command_timeout_seconds || 30;
      allowedDirs = (s.tools?.allowed_directories || []).join('\n');
      ntfyEnabled = s.ntfy?.enabled !== false;
      ntfyServer = s.ntfy?.server || '';
      ntfyTopic = s.ntfy?.topic || '';
    } catch (e) {
      console.error('Failed to load settings:', e);
    }
    loading = false;
  }

  async function save(endpoint, data) {
    saving = true;
    try {
      await fetch(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      testResult = { ok: true, message: 'Saved' };
      setTimeout(() => testResult = null, 2000);
    } catch (e) {
      testResult = { ok: false, message: 'Save failed: ' + e.message };
    }
    saving = false;
  }

  async function testProvider(name) {
    testResult = { ok: true, message: 'Testing...' };
    try {
      const r = await fetch(`/api/settings/test-provider/${name}`, { method: 'POST' });
      const data = await r.json();
      if (data.ok) {
        testResult = { ok: true, message: `${name}: "${data.response?.slice(0, 100)}"` };
      } else {
        testResult = { ok: false, message: `${name} failed: ${data.error}` };
      }
    } catch (e) {
      testResult = { ok: false, message: 'Test failed: ' + e.message };
    }
  }

  async function testNtfy() {
    testResult = { ok: true, message: 'Sending test...' };
    try {
      await fetch('/api/settings/test-ntfy', { method: 'POST' });
      testResult = { ok: true, message: 'Test notification sent!' };
    } catch (e) {
      testResult = { ok: false, message: 'Test failed: ' + e.message };
    }
  }

  async function deleteMemory(id) {
    await fetch(`/api/memories/${id}`, { method: 'DELETE' });
    memories = memories.filter(m => m.id !== id);
  }

  function formatTokens(n) {
    if (!n) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  }
</script>

{#if open}
<div class="settings-overlay" onclick={() => open = false}></div>
<div class="settings-panel">
  <div class="settings-header">
    <h2>Settings</h2>
    <button class="close-btn" onclick={() => open = false}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  </div>

  {#if testResult}
    <div class="toast" class:error={!testResult.ok}>{testResult.message}</div>
  {/if}

  <div class="tabs">
    {#each tabs as tab}
      <button
        class="tab" class:active={activeTab === tab.id}
        onclick={() => activeTab = tab.id}
      >{tab.label}</button>
    {/each}
  </div>

  <div class="tab-content">
    {#if loading}
      <div class="loading">Loading settings...</div>

    {:else if activeTab === 'providers'}
      <div class="section">
        {#if settings?.providers}
          {#each Object.entries(settings.providers) as [name, prov]}
            <div class="card">
              <div class="card-header">
                <strong>{name}</strong>
                <span class="badge">{prov.role || 'unknown'}</span>
                <span class="badge" class:ok={prov.has_key} class:warn={!prov.has_key}>
                  {prov.has_key ? 'configured' : 'no key'}
                </span>
              </div>
              <div class="card-body">
                <div class="field">
                  <label>Model</label>
                  <span class="value">{prov.model || prov.default_model || '-'}</span>
                </div>
                {#if prov.base_url}
                  <div class="field">
                    <label>URL</label>
                    <span class="value mono">{prov.base_url}</span>
                  </div>
                {/if}
                <div class="field">
                  <label>API Key</label>
                  <span class="value mono">{prov.api_key_masked || 'not set'}</span>
                </div>
                <button class="btn-sm" onclick={() => testProvider(name)}>Test</button>
              </div>
            </div>
          {/each}
        {/if}
      </div>

    {:else if activeTab === 'routing'}
      <div class="section">
        <div class="form-group">
          <label>Default Provider</label>
          <select bind:value={routingDefault}>
            {#if settings?.providers}
              {#each Object.keys(settings.providers) as name}
                <option value={name}>{name}</option>
              {/each}
            {/if}
          </select>
        </div>
        <div class="form-group">
          <label>Opus Daily Budget (tokens)</label>
          <input type="number" bind:value={routingBudget} min="0" step="10000" />
        </div>
        <div class="form-group">
          <label>Complexity Threshold (0-100)</label>
          <input type="range" bind:value={complexityThreshold} min="0" max="100" />
          <span class="range-value">{complexityThreshold}</span>
        </div>
        <div class="form-group">
          <label>Long Context Threshold (chars)</label>
          <input type="number" bind:value={longContextChars} min="500" step="500" />
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/routing', {
            default: routingDefault,
            opus_daily_budget_tokens: routingBudget,
          })}
        >Save Routing</button>
      </div>

    {:else if activeTab === 'personality'}
      <div class="section">
        <div class="form-group">
          <label>Name</label>
          <input type="text" bind:value={personalityName} />
        </div>
        <div class="form-group">
          <label>System Prompt Template</label>
          <textarea bind:value={personalityPrompt} rows="10"></textarea>
          <div class="hint">Variables: {'{name}'}, {'{time}'}, {'{date}'}, {'{day}'}, {'{memories}'}, {'{pending_tasks}'}, {'{tools_context}'}</div>
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/personality', {
            name: personalityName,
            system_prompt: personalityPrompt,
          })}
        >Save Personality</button>
      </div>

    {:else if activeTab === 'tools'}
      <div class="section">
        <div class="form-group">
          <label>
            <input type="checkbox" bind:checked={toolsEnabled} />
            Enable tool use (file access, commands)
          </label>
        </div>
        <div class="form-group">
          <label>Max Agent Turns</label>
          <input type="number" bind:value={maxAgentTurns} min="1" max="20" />
        </div>
        <div class="form-group">
          <label>Command Timeout (seconds)</label>
          <input type="number" bind:value={commandTimeout} min="5" max="120" />
        </div>
        <div class="form-group">
          <label>Allowed Directories (one per line)</label>
          <textarea bind:value={allowedDirs} rows="5" placeholder="~/Documents/Work/&#10;~/Projects/"></textarea>
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/tools', {
            enabled: toolsEnabled,
            max_agent_turns: maxAgentTurns,
            command_timeout_seconds: commandTimeout,
            allowed_directories: allowedDirs.split('\n').map(s => s.trim()).filter(Boolean),
          })}
        >Save Tools Settings</button>
      </div>

    {:else if activeTab === 'schedule'}
      <div class="section">
        <div class="form-group">
          <label>Active Hours</label>
          <div class="inline">
            <input type="number" bind:value={activeHoursStart} min="0" max="23" style="width:60px" />
            <span>to</span>
            <input type="number" bind:value={activeHoursEnd} min="0" max="23" style="width:60px" />
          </div>
        </div>
        <div class="form-group">
          <label>Heartbeat Interval (minutes)</label>
          <input type="number" bind:value={heartbeatInterval} min="5" max="120" />
        </div>
        <div class="form-group">
          <label>Idle Check-in After (minutes)</label>
          <input type="number" bind:value={idleCheckin} min="5" max="480" />
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/scheduler', {
            active_hours: [activeHoursStart, activeHoursEnd],
            heartbeat_interval_minutes: heartbeatInterval,
            idle_checkin_minutes: idleCheckin,
          })}
        >Save Schedule</button>
      </div>

    {:else if activeTab === 'memory'}
      <div class="section">
        <div class="form-group">
          <label>
            <input type="checkbox" bind:checked={extractionEnabled} />
            Auto-extract memories
          </label>
        </div>
        <div class="form-group">
          <label>Max Memories</label>
          <input type="number" bind:value={maxMemories} min="10" max="1000" />
        </div>
        <div class="form-group">
          <label>Summary Threshold (messages)</label>
          <input type="number" bind:value={summaryThreshold} min="10" max="100" />
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/memory', {
            max_memories: maxMemories,
            summary_threshold: summaryThreshold,
            extraction_enabled: extractionEnabled,
          })}
        >Save Memory Settings</button>

        <h3>Stored Memories ({memories.length})</h3>
        <div class="memory-list">
          {#each memories as mem (mem.id)}
            <div class="memory-item">
              <span class="badge">{mem.category}</span>
              <span class="memory-text">{mem.content}</span>
              <button class="btn-xs" onclick={() => deleteMemory(mem.id)}>x</button>
            </div>
          {:else}
            <div class="empty">No memories stored yet</div>
          {/each}
        </div>
      </div>

    {:else if activeTab === 'notifications'}
      <div class="section">
        <div class="form-group">
          <label>
            <input type="checkbox" bind:checked={ntfyEnabled} />
            Enable ntfy push notifications
          </label>
        </div>
        <div class="form-group">
          <label>ntfy Server</label>
          <input type="text" bind:value={ntfyServer} placeholder="https://ntfy.example.com" />
        </div>
        <div class="form-group">
          <label>Topic</label>
          <input type="text" bind:value={ntfyTopic} placeholder="conduit" />
        </div>
        <div class="btn-row">
          <button class="btn" disabled={saving}
            onclick={() => save('/api/settings/ntfy', {
              enabled: ntfyEnabled,
              server: ntfyServer,
              topic: ntfyTopic,
            })}
          >Save</button>
          <button class="btn btn-secondary" onclick={testNtfy}>Test Notification</button>
        </div>
      </div>

    {:else if activeTab === 'usage'}
      <div class="section">
        {#if usage}
          <div class="card">
            <div class="card-header"><strong>Opus Budget</strong></div>
            <div class="card-body">
              <div class="budget-bar">
                <div class="budget-fill" style="width: {Math.min(100, (usage.opus_today / usage.opus_budget) * 100)}%"></div>
              </div>
              <div class="budget-text">{formatTokens(usage.opus_today)} / {formatTokens(usage.opus_budget)} tokens</div>
            </div>
          </div>

          <h3>Today</h3>
          {#if usage.daily?.length}
            <table class="usage-table">
              <thead><tr><th>Provider</th><th>Model</th><th>Requests</th><th>Input</th><th>Output</th></tr></thead>
              <tbody>
                {#each usage.daily as row}
                  <tr>
                    <td>{row.provider}</td>
                    <td class="mono">{row.model?.split('/').pop() || '-'}</td>
                    <td>{row.request_count}</td>
                    <td>{formatTokens(row.total_input)}</td>
                    <td>{formatTokens(row.total_output)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <div class="empty">No usage today</div>
          {/if}

          <h3>Last 7 Days</h3>
          {#if usage.weekly?.length}
            <table class="usage-table">
              <thead><tr><th>Provider</th><th>Model</th><th>Requests</th><th>Input</th><th>Output</th></tr></thead>
              <tbody>
                {#each usage.weekly as row}
                  <tr>
                    <td>{row.provider}</td>
                    <td class="mono">{row.model?.split('/').pop() || '-'}</td>
                    <td>{row.request_count}</td>
                    <td>{formatTokens(row.total_input)}</td>
                    <td>{formatTokens(row.total_output)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <div class="empty">No usage in the last 7 days</div>
          {/if}
        {:else}
          <div class="loading">Loading usage data...</div>
        {/if}
      </div>
    {/if}
  </div>
</div>
{/if}

<style>
  .settings-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 100;
  }

  .settings-panel {
    position: fixed;
    top: 0;
    right: 0;
    width: min(520px, 95vw);
    height: 100%;
    background: var(--bg-surface);
    border-left: 1px solid var(--border);
    z-index: 101;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .settings-header h2 {
    font-size: 18px;
    font-weight: 600;
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
  }
  .close-btn:hover { color: var(--text); }

  .toast {
    padding: 8px 16px;
    background: var(--green);
    color: #fff;
    font-size: 13px;
    text-align: center;
    flex-shrink: 0;
  }
  .toast.error { background: var(--red); }

  .tabs {
    display: flex;
    gap: 2px;
    padding: 8px 12px 0;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    flex-shrink: 0;
  }

  .tab {
    padding: 8px 12px;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-dim);
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .tab-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }

  .section { display: flex; flex-direction: column; gap: 14px; }

  .card {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .card-header {
    padding: 10px 14px;
    background: var(--bg-hover);
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
  }
  .card-body {
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--bg-input);
    color: var(--text-dim);
  }
  .badge.ok { background: rgba(76,175,128,0.15); color: var(--green); }
  .badge.warn { background: rgba(224,80,80,0.15); color: var(--red); }

  .field {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
  }
  .field label {
    color: var(--text-dim);
    min-width: 60px;
  }
  .value { color: var(--text); }
  .mono { font-family: monospace; font-size: 12px; }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .form-group label {
    font-size: 13px;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .form-group input[type="text"],
  .form-group input[type="number"],
  .form-group select,
  .form-group textarea {
    padding: 8px 10px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: 14px;
    font-family: inherit;
  }
  .form-group textarea {
    resize: vertical;
    font-family: monospace;
    font-size: 12px;
  }
  .form-group input[type="range"] {
    width: 100%;
  }
  .form-group input[type="checkbox"] {
    width: 16px;
    height: 16px;
  }

  .range-value {
    font-size: 13px;
    color: var(--text);
    text-align: center;
  }

  .inline {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .hint {
    font-size: 11px;
    color: var(--text-muted);
  }

  .btn {
    padding: 8px 16px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 13px;
    cursor: pointer;
    align-self: flex-start;
  }
  .btn:hover { opacity: 0.85; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: var(--bg-hover); color: var(--text); border: 1px solid var(--border); }
  .btn-row { display: flex; gap: 8px; }

  .btn-sm {
    padding: 4px 10px;
    background: var(--bg-hover);
    color: var(--text-dim);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 12px;
    cursor: pointer;
    align-self: flex-start;
  }
  .btn-sm:hover { color: var(--text); border-color: var(--text-dim); }

  .btn-xs {
    padding: 2px 6px;
    background: none;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-size: 11px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .btn-xs:hover { color: var(--red); border-color: var(--red); }

  h3 {
    font-size: 14px;
    font-weight: 600;
    margin-top: 12px;
    color: var(--text-dim);
  }

  .memory-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 400px;
    overflow-y: auto;
  }

  .memory-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--bg-input);
    border-radius: var(--radius-sm);
    font-size: 13px;
  }
  .memory-text { flex: 1; }

  .budget-bar {
    height: 8px;
    background: var(--bg-input);
    border-radius: 4px;
    overflow: hidden;
  }
  .budget-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 4px;
    transition: width 0.3s;
  }
  .budget-text {
    font-size: 12px;
    color: var(--text-dim);
    text-align: center;
  }

  .usage-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .usage-table th {
    text-align: left;
    padding: 6px 8px;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    font-weight: 500;
  }
  .usage-table td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
  }

  .loading, .empty {
    text-align: center;
    padding: 24px;
    color: var(--text-muted);
    font-size: 14px;
  }
</style>
