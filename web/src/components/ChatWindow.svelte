<script>
  import { messages, isTyping } from '../lib/stores/chat.js';
  import MessageBubble from './MessageBubble.svelte';
  import { tick } from 'svelte';

  let container = $state(null);
  let shouldAutoScroll = $state(true);

  // Auto-scroll when new messages arrive
  $effect(() => {
    // Subscribe to messages
    const _ = $messages;
    if (shouldAutoScroll && container) {
      tick().then(() => {
        container.scrollTop = container.scrollHeight;
      });
    }
  });

  function onScroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 100;
  }
</script>

<div class="chat-window" bind:this={container} onscroll={onScroll}>
  {#if $messages.length === 0}
    <div class="empty">
      <div class="empty-icon">⚡</div>
      <div class="empty-title">Conduit</div>
      <div class="empty-hint">Send a message or try a command like <code>/help</code></div>
    </div>
  {:else}
    <div class="messages">
      {#each $messages as msg (msg.id)}
        <MessageBubble {msg} />
      {/each}
      {#if $isTyping}
        <div class="typing">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .chat-window {
    flex: 1;
    overflow-y: auto;
    padding: 16px 16px 8px;
  }

  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 8px;
    opacity: 0.5;
  }

  .empty-icon { font-size: 48px; }
  .empty-title { font-size: 24px; font-weight: 600; }
  .empty-hint { color: var(--text-dim); }
  .empty-hint code {
    background: var(--bg-hover);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
  }

  .messages {
    max-width: 768px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding-bottom: 16px;
  }

  .typing {
    display: flex;
    gap: 4px;
    padding: 12px 16px;
  }

  .dot {
    width: 8px;
    height: 8px;
    background: var(--text-muted);
    border-radius: 50%;
    animation: bounce 1.4s infinite ease-in-out both;
  }
  .dot:nth-child(1) { animation-delay: -0.32s; }
  .dot:nth-child(2) { animation-delay: -0.16s; }

  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0); }
    40% { transform: scale(1); }
  }
</style>
