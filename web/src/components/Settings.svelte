<script>
  import { onMount } from 'svelte';

  let { open = $bindable(false) } = $props();

  let settings = $state(null);
  let memories = $state([]);
  let usage = $state(null);
  let systemInfo = $state(null);
  let activeTab = $state('providers');
  let loading = $state(true);
  let saving = $state(false);
  let testResult = $state(null);
  let rawYaml = $state('');
  let rawPath = $state('');
  let rawDirty = $state(false);

  // ChatGPT OAuth flow state
  let chatgptFlow = $state(null);  // {user_code, verification_uri, device_code}
  let chatgptPolling = $state(false);
  let chatgptPollTimer = null;

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
    { id: 'system', label: 'System' },
    { id: 'raw', label: 'Raw YAML' },
    { id: 'usage', label: 'Usage' },
  ];

  onMount(() => {
    if (open) loadAll();
  });

  $effect(() => {
    if (open) loadAll();
  });

  async function fetchJson(url, fallback = null) {
    try {
      const res = await fetch(url);
      if (!res.ok) return fallback;
      return await res.json();
    } catch {
      return fallback;
    }
  }

  async function loadAll() {
    loading = true;
    try {
      const [s, m, u, sys, raw] = await Promise.all([
        fetchJson('/api/settings', {}),
        fetchJson('/api/memories', []),
        fetchJson('/api/settings/usage', {}),
        fetchJson('/api/settings/system', null),
        fetchJson('/api/settings/raw', null),
      ]);
      settings = s;
      memories = m || [];
      usage = u;
      systemInfo = sys;
      if (raw && typeof raw.yaml === 'string') {
        rawYaml = raw.yaml;
        rawPath = raw.path || '';
        rawDirty = false;
      }

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

  let saveToastTimer = null;
  async function save(endpoint, data) {
    saving = true;
    try {
      const res = await fetch(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok && body.ok !== false) {
        testResult = { ok: true, message: 'Saved' };
      } else {
        testResult = { ok: false, message: body.error || `Save failed (${res.status})` };
      }
      clearTimeout(saveToastTimer);
      saveToastTimer = setTimeout(() => testResult = null, 2000);
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

  async function saveRawYaml() {
    if (!rawYaml.trim()) {
      testResult = { ok: false, message: 'Raw YAML cannot be empty' };
      return;
    }
    saving = true;
    try {
      const res = await fetch('/api/settings/raw', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml: rawYaml }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        rawDirty = false;
        testResult = { ok: true, message: 'Raw YAML saved and reloaded' };
        await loadAll();
      } else {
        testResult = { ok: false, message: data.detail || data.error || `Save failed (${res.status})` };
      }
    } catch (e) {
      testResult = { ok: false, message: 'Raw save failed: ' + e.message };
    }
    saving = false;
  }

  async function reloadRawYaml() {
    const raw = await fetchJson('/api/settings/raw', null);
    if (raw && typeof raw.yaml === 'string') {
      rawYaml = raw.yaml;
      rawPath = raw.path || rawPath;
      rawDirty = false;
      testResult = { ok: true, message: 'Reloaded raw YAML from disk' };
    } else {
      testResult = { ok: false, message: 'Could not reload raw YAML' };
    }
  }

  async function startChatGPTAuth() {
    try {
      const r = await fetch('/api/chatgpt/auth/start', { method: 'POST' });
      const data = await r.json();
      if (data.ok) {
        chatgptFlow = data;
        chatgptPolling = true;
        pollChatGPTAuth(data.device_code, data.interval || 5);
      } else {
        testResult = { ok: false, message: 'Auth start failed: ' + (data.error || 'unknown') };
      }
    } catch (e) {
      testResult = { ok: false, message: 'Auth start failed: ' + e.message };
    }
  }

  function pollChatGPTAuth(deviceCode, interval) {
    clearInterval(chatgptPollTimer);
    chatgptPollTimer = setInterval(async () => {
      try {
        const r = await fetch('/api/chatgpt/auth/poll', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ device_code: deviceCode }),
        });
        const data = await r.json();
        if (data.status === 'complete') {
          clearInterval(chatgptPollTimer);
          chatgptFlow = null;
          chatgptPolling = false;
          testResult = { ok: true, message: 'ChatGPT authenticated!' };
          loadAll();
        } else if (data.status === 'expired' || data.status === 'error') {
          clearInterval(chatgptPollTimer);
          chatgptFlow = null;
          chatgptPolling = false;
          testResult = { ok: false, message: data.error || 'Auth flow expired' };
        }
        // 'pending' and 'slow_down' — keep polling
      } catch {
        // Network error — keep polling
      }
    }, interval * 1000);
  }

  function formatTokens(n) {
    if (!n) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  }
</script>

{#if open}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="settings-overlay" role="presentation" onclick={() => open = false}></div>
<div class="settings-panel">
  <div class="settings-header">
    <h2>Settings</h2>
    <button class="close-btn" onclick={() => open = false} aria-label="Close settings">
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
                  {#if prov.type === 'chatgpt'}
                    {prov.has_key ? 'authenticated' : 'not connected'}
                  {:else if prov.auth_method === 'vertex'}
                    {prov.has_key ? 'vertex' : 'no project'}
                  {:else if prov.auth_method === 'cli'}
                    {prov.has_key ? 'installed' : 'not found'}
                  {:else}
                    {prov.has_key ? 'configured' : 'no key'}
                  {/if}
                </span>
              </div>
              <div class="card-body">
                <div class="field">
                  <span class="field-label">Model</span>
                  <span class="value">{prov.model || prov.default_model || '-'}</span>
                </div>
                {#if prov.type === 'chatgpt'}
                  {#if prov.auth?.authenticated}
                    {#if prov.auth.email}
                      <div class="field">
                        <span class="field-label">Account</span>
                        <span class="value">{prov.auth.email}</span>
                      </div>
                    {/if}
                    {#if prov.auth.plan}
                      <div class="field">
                        <span class="field-label">Plan</span>
                        <span class="value" style="text-transform:capitalize">{prov.auth.plan}</span>
                      </div>
                    {/if}
                    <div class="btn-row">
                      <button class="btn-sm" onclick={() => testProvider(name)}>Test</button>
                      <button class="btn-sm" onclick={startChatGPTAuth}>Re-authenticate</button>
                    </div>
                  {:else}
                    {#if chatgptFlow}
                      <div class="oauth-flow">
                        <div class="field">
                          <span class="field-label">Code</span>
                          <span class="value mono" style="font-size:18px;font-weight:600;letter-spacing:2px">{chatgptFlow.user_code}</span>
                        </div>
                        <div class="hint" style="margin-top:4px">
                          Go to <a href={chatgptFlow.verification_uri} target="_blank" rel="noopener">{chatgptFlow.verification_uri}</a> and enter the code above
                        </div>
                        {#if chatgptPolling}
                          <div class="hint" style="color:var(--accent)">Waiting for authorization...</div>
                        {/if}
                      </div>
                    {:else}
                      <button class="btn" onclick={startChatGPTAuth}>Authenticate with ChatGPT</button>
                    {/if}
                  {/if}
                {:else if prov.auth_method === 'vertex'}
                  <div class="field">
                    <span class="field-label">Auth</span>
                    <span class="value">GCP Vertex AI</span>
                  </div>
                  {#if prov.gcp_project}
                    <div class="field">
                      <span class="field-label">Project</span>
                      <span class="value mono">{prov.gcp_project}</span>
                    </div>
                  {/if}
                  <button class="btn-sm" onclick={() => testProvider(name)}>Test</button>
                {:else if prov.auth_method === 'cli'}
                  <div class="field">
                    <span class="field-label">Auth</span>
                    <span class="value">Claude CLI</span>
                  </div>
                  <button class="btn-sm" onclick={() => testProvider(name)}>Test</button>
                {:else}
                  {#if prov.base_url}
                    <div class="field">
                      <span class="field-label">URL</span>
                      <span class="value mono">{prov.base_url}</span>
                    </div>
                  {/if}
                  <div class="field">
                    <span class="field-label">API Key</span>
                    <span class="value mono">{prov.api_key_masked || 'not set'}</span>
                  </div>
                  <button class="btn-sm" onclick={() => testProvider(name)}>Test</button>
                {/if}
              </div>
            </div>
          {/each}
        {/if}
      </div>

    {:else if activeTab === 'routing'}
      <div class="section">
        <div class="form-group">
          <label for="routing-default">Default Provider</label>
          <select id="routing-default" bind:value={routingDefault}>
            {#if settings?.providers}
              {#each Object.keys(settings.providers) as name}
                <option value={name}>{name}</option>
              {/each}
            {/if}
          </select>
        </div>
        <div class="form-group">
          <label for="routing-budget">Opus Daily Budget (tokens)</label>
          <input id="routing-budget" type="number" bind:value={routingBudget} min="0" step="10000" />
        </div>
        <div class="form-group">
          <label for="routing-complexity">Complexity Threshold (0-100)</label>
          <input id="routing-complexity" type="range" bind:value={complexityThreshold} min="0" max="100" />
          <span class="range-value">{complexityThreshold}</span>
        </div>
        <div class="form-group">
          <label for="routing-longctx">Long Context Threshold (chars)</label>
          <input id="routing-longctx" type="number" bind:value={longContextChars} min="500" step="500" />
        </div>
        <button class="btn" disabled={saving}
          onclick={() => save('/api/settings/routing', {
            default: routingDefault,
            opus_daily_budget_tokens: routingBudget,
            complexity_threshold: complexityThreshold,
            long_context_chars: longContextChars,
          })}
        >Save Routing</button>
      </div>

    {:else if activeTab === 'personality'}
      <div class="section">
        <div class="form-group">
          <label for="personality-name">Name</label>
          <input id="personality-name" type="text" bind:value={personalityName} />
        </div>
        <div class="form-group">
          <label for="personality-prompt">System Prompt Template</label>
          <textarea id="personality-prompt" bind:value={personalityPrompt} rows="10"></textarea>
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
          <label for="tools-turns">Max Agent Turns</label>
          <input id="tools-turns" type="number" bind:value={maxAgentTurns} min="1" max="20" />
        </div>
        <div class="form-group">
          <label for="tools-timeout">Command Timeout (seconds)</label>
          <input id="tools-timeout" type="number" bind:value={commandTimeout} min="5" max="120" />
        </div>
        <div class="form-group">
          <label for="tools-dirs">Allowed Directories (one per line)</label>
          <textarea id="tools-dirs" bind:value={allowedDirs} rows="5" placeholder="~/Documents/Work/&#10;~/Projects/"></textarea>
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
          <label for="sched-hours-start">Active Hours</label>
          <div class="inline">
            <input id="sched-hours-start" type="number" bind:value={activeHoursStart} min="0" max="23" style="width:60px" />
            <span>to</span>
            <input id="sched-hours-end" type="number" bind:value={activeHoursEnd} min="0" max="23" style="width:60px" />
          </div>
        </div>
        <div class="form-group">
          <label for="sched-heartbeat">Heartbeat Interval (minutes)</label>
          <input id="sched-heartbeat" type="number" bind:value={heartbeatInterval} min="5" max="120" />
        </div>
        <div class="form-group">
          <label for="sched-idle">Idle Check-in After (minutes)</label>
          <input id="sched-idle" type="number" bind:value={idleCheckin} min="5" max="480" />
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
          <label for="mem-max">Max Memories</label>
          <input id="mem-max" type="number" bind:value={maxMemories} min="10" max="1000" />
        </div>
        <div class="form-group">
          <label for="mem-summary">Summary Threshold (messages)</label>
          <input id="mem-summary" type="number" bind:value={summaryThreshold} min="10" max="100" />
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
          <label for="ntfy-server">ntfy Server</label>
          <input id="ntfy-server" type="text" bind:value={ntfyServer} placeholder="https://ntfy.example.com" />
        </div>
        <div class="form-group">
          <label for="ntfy-topic">Topic</label>
          <input id="ntfy-topic" type="text" bind:value={ntfyTopic} placeholder="conduit" />
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

    {:else if activeTab === 'system'}
      <div class="section">
        {#if systemInfo}
          <div class="card">
            <div class="card-header"><strong>Runtime Health</strong></div>
            <div class="card-body">
              <div class="field"><span class="field-label">Status</span><span class="value">{systemInfo.health?.status || 'unknown'}</span></div>
              <div class="field"><span class="field-label">Uptime</span><span class="value">{systemInfo.health?.uptime_seconds || 0}s</span></div>
              <div class="field"><span class="field-label">Providers</span><span class="value">{(systemInfo.health?.providers || []).join(', ') || '-'}</span></div>
              <div class="field"><span class="field-label">Worker</span><span class="value">{systemInfo.health?.worker_phase || 'unknown'}</span></div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><strong>Feature Flags</strong></div>
            <div class="card-body">
              {#each Object.entries(systemInfo.features || {}) as [k, v]}
                {#if k !== 'outlook'}
                  <div class="field">
                    <span class="field-label mono">{k}</span>
                    <span class="value">{typeof v === 'boolean' ? (v ? 'enabled' : 'disabled') : String(v)}</span>
                  </div>
                {/if}
              {/each}
              <div class="field"><span class="field-label">outlook</span><span class="value">{systemInfo.features?.outlook?.enabled ? 'enabled' : 'disabled'} | configured: {systemInfo.features?.outlook?.configured ? 'yes' : 'no'} | auth: {systemInfo.features?.outlook?.authenticated ? 'yes' : 'no'}</span></div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><strong>Plugin Registry</strong></div>
            <div class="card-body">
              {#if systemInfo.plugins?.length}
                {#each systemInfo.plugins as plugin}
                  <div class="memory-item">
                    <span class="badge">{plugin.id}</span>
                    <span class="memory-text">{plugin.version || '0.0.0'} • tools: {(plugin.tools || []).length} • hooks: {(plugin.hooks || []).length}</span>
                  </div>
                {/each}
              {:else}
                <div class="empty">No plugins loaded</div>
              {/if}
            </div>
          </div>

          <div class="card">
            <div class="card-header"><strong>Scheduled Tasks</strong></div>
            <div class="card-body">
              {#if systemInfo.scheduled_tasks?.length}
                {#each systemInfo.scheduled_tasks as task}
                  <div class="memory-item">
                    <span class="badge">{task.enabled ? 'on' : 'off'}</span>
                    <span class="memory-text">{task.name} • {task.cron} • tier {task.model_tier}</span>
                  </div>
                {/each}
              {:else}
                <div class="empty">No custom scheduled tasks</div>
              {/if}
            </div>
          </div>

          <div class="card">
            <div class="card-header"><strong>Paths</strong></div>
            <div class="card-body">
              <div class="field"><span class="field-label">config</span><span class="value mono">{systemInfo.paths?.config || '-'}</span></div>
              <div class="field"><span class="field-label">env</span><span class="value mono">{systemInfo.paths?.env || '-'}</span></div>
              <div class="field"><span class="field-label">plugins</span><span class="value mono">{systemInfo.paths?.plugins_dir || '-'}</span></div>
            </div>
          </div>
        {:else}
          <div class="empty">System diagnostics unavailable</div>
        {/if}
      </div>

    {:else if activeTab === 'raw'}
      <div class="section">
        <div class="form-group">
          <label for="raw-config-editor">Raw Config YAML</label>
          <div class="hint">Editing path: {rawPath || 'server/config.yaml'}</div>
          <textarea
            id="raw-config-editor"
            class="raw-editor"
            bind:value={rawYaml}
            rows="26"
            oninput={() => rawDirty = true}
          ></textarea>
        </div>
        <div class="btn-row">
          <button class="btn" disabled={saving || !rawDirty} onclick={saveRawYaml}>Save Raw YAML</button>
          <button class="btn btn-secondary" disabled={saving} onclick={reloadRawYaml}>Reload From Disk</button>
        </div>
        <div class="hint">This writes directly to config.yaml and reloads server config immediately.</div>
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
  .field-label {
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
  .raw-editor {
    min-height: 420px;
    line-height: 1.35;
    tab-size: 2;
    white-space: pre;
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
