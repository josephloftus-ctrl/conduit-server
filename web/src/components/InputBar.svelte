<script>
  import { sendMessage, isStreaming } from '../lib/stores/chat.js';

  let input = $state('');
  let textarea = $state(null);
  let recording = $state(false);
  let mediaRecorder = $state(null);
  let transcribing = $state(false);

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

  async function toggleRecording() {
    if (recording) {
      // Stop recording
      mediaRecorder?.stop();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : 'audio/webm'
      });
      const chunks = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };

      recorder.onstop = async () => {
        recording = false;
        mediaRecorder = null;
        stream.getTracks().forEach(t => t.stop());

        if (chunks.length === 0) return;

        const blob = new Blob(chunks, { type: 'audio/webm' });
        transcribing = true;

        try {
          const form = new FormData();
          form.append('file', blob, 'recording.webm');
          const res = await fetch('/api/voice/transcribe', { method: 'POST', body: form });
          const data = await res.json();
          if (data.ok && data.text) {
            input = data.text;
            autoResize();
            textarea?.focus();
          }
        } catch (err) {
          console.error('Transcription failed:', err);
        } finally {
          transcribing = false;
        }
      };

      recorder.start();
      mediaRecorder = recorder;
      recording = true;
    } catch (err) {
      console.error('Mic access denied:', err);
    }
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
      placeholder={transcribing ? 'Transcribing...' : 'Message Conduit...'}
      rows="1"
      disabled={$isStreaming || transcribing}
    ></textarea>

    <button
      class="mic-btn"
      class:recording
      class:transcribing
      onclick={toggleRecording}
      disabled={$isStreaming || transcribing}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
    >
      {#if transcribing}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M12 6v6l4 2"></path>
        </svg>
      {:else}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
          <line x1="12" y1="19" x2="12" y2="23"></line>
          <line x1="8" y1="23" x2="16" y2="23"></line>
        </svg>
      {/if}
    </button>

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

  .mic-btn {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-dim);
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.2s;
  }
  .mic-btn:hover { color: var(--text); border-color: var(--text-dim); }
  .mic-btn:disabled { opacity: 0.3; cursor: default; }
  .mic-btn.recording {
    background: var(--red, #ef4444);
    border-color: var(--red, #ef4444);
    color: #fff;
    animation: pulse-recording 1.5s ease-in-out infinite;
  }
  .mic-btn.transcribing {
    color: var(--accent);
    border-color: var(--accent);
    animation: spin 1s linear infinite;
  }

  @keyframes pulse-recording {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

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
