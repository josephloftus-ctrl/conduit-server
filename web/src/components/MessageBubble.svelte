<script>
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import ToolCallIndicator from './ToolCallIndicator.svelte';
  import { isStreaming } from '../lib/stores/chat.js';

  let { msg } = $props();

  // Configure marked once at module level (not per-instance)
  marked.setOptions({ breaks: true, gfm: true });

  // Throttled markdown rendering during streaming to avoid
  // re-parsing the entire message on every token
  let lastParsed = $state('');
  let lastInput = '';
  let parseTimer = null;

  let rendered = $derived.by(() => {
    if (msg.role === 'user') return escapeHtml(msg.content);
    const content = msg.content || '';

    if (msg.streaming) {
      // During streaming, throttle markdown parsing to ~60ms intervals
      if (content !== lastInput) {
        lastInput = content;
        if (!parseTimer) {
          parseTimer = setTimeout(() => {
            parseTimer = null;
            lastParsed = DOMPurify.sanitize(marked.parse(lastInput));
          }, 60);
        }
      }
      return lastParsed || DOMPurify.sanitize(marked.parse(content));
    }

    // Final render — no throttle
    lastInput = '';
    lastParsed = '';
    if (parseTimer) { clearTimeout(parseTimer); parseTimer = null; }
    return DOMPurify.sanitize(marked.parse(content));
  });

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/\n/g, '<br>');
  }

  // Model badge display name
  let modelBadge = $derived.by(() => {
    if (!msg.model) return null;
    const m = msg.model;
    if (m.includes('opus') || m.includes('claude')) return 'Opus';
    if (m.includes('gemini')) return 'Gemini';
    if (m.includes('llama')) return 'Llama';
    if (m.includes('meta/')) return 'NIM';
    return m.split('/').pop()?.split('-')[0] || m;
  });

  let hasToolCalls = $derived(msg.toolCalls && msg.toolCalls.length > 0);
</script>

<div class="bubble {msg.role}" class:streaming={$isStreaming && msg.streaming} class:error={msg.role === 'error'}>
  {#if msg.role === 'error'}
    <div class="error-content">{msg.content}</div>
  {:else}
    {#if hasToolCalls}
      <div class="tool-calls">
        {#each msg.toolCalls as tc (tc.id)}
          <ToolCallIndicator toolCall={tc} />
        {/each}
      </div>
    {/if}

    {#if msg.content}
      <div class="content">{@html rendered}</div>
    {/if}
  {/if}

  {#if msg.role === 'assistant' && modelBadge}
    <div class="meta">
      <span class="model-badge">{modelBadge}</span>
    </div>
  {/if}

  {#if $isStreaming && msg.streaming}
    <span class="cursor">|</span>
  {/if}
</div>

<style>
  .bubble {
    padding: 10px 14px;
    border-radius: var(--radius);
    max-width: 100%;
    word-wrap: break-word;
    overflow-wrap: break-word;
    position: relative;
  }

  .bubble.user {
    background: var(--accent-dim);
    color: #fff;
    align-self: flex-end;
    border-radius: var(--radius) var(--radius) 4px var(--radius);
    max-width: 80%;
    margin-left: auto;
  }

  .bubble.assistant {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius) var(--radius) var(--radius) 4px;
  }

  .bubble.error {
    background: transparent;
    border: 1px solid var(--red);
    color: var(--red);
  }

  .error-content {
    font-size: 13px;
  }

  .content {
    line-height: 1.6;
  }

  .tool-calls {
    margin-bottom: 6px;
  }

  /* Markdown styles */
  .content :global(p) { margin: 0.4em 0; }
  .content :global(p:first-child) { margin-top: 0; }
  .content :global(p:last-child) { margin-bottom: 0; }

  .content :global(code) {
    background: var(--bg-hover);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 13px;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }

  .content :global(pre) {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 12px;
    overflow-x: auto;
    margin: 8px 0;
  }

  .content :global(pre code) {
    background: transparent;
    padding: 0;
    font-size: 13px;
  }

  .content :global(ul), .content :global(ol) {
    padding-left: 1.5em;
    margin: 0.4em 0;
  }

  .content :global(strong) { color: #fff; }

  .content :global(blockquote) {
    border-left: 3px solid var(--accent);
    padding-left: 12px;
    color: var(--text-dim);
    margin: 8px 0;
  }

  .meta {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }

  .model-badge {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--bg-hover);
    color: var(--text-dim);
    font-weight: 500;
  }

  .cursor {
    animation: blink 0.8s infinite;
    color: var(--accent);
    font-weight: bold;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }
</style>
