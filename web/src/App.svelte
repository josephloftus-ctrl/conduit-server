<script>
  import { onMount } from 'svelte';
  import { connect } from './lib/stores/connection.js';
  import { loadConversations } from './lib/stores/chat.js';
  import ChatWindow from './components/ChatWindow.svelte';
  import InputBar from './components/InputBar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import StatusBar from './components/StatusBar.svelte';
  import PushToast from './components/PushToast.svelte';
  import Settings from './components/Settings.svelte';
  import PermissionModal from './components/PermissionModal.svelte';

  let sidebarOpen = $state(false);
  let settingsOpen = $state(false);

  onMount(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/ws`;
    connect(wsUrl);
    loadConversations();
  });
</script>

<div class="layout">
  <Sidebar bind:open={sidebarOpen} onOpenSettings={() => { sidebarOpen = false; settingsOpen = true; }} />

  <main class="main" class:sidebar-open={sidebarOpen}>
    <StatusBar onToggleSidebar={() => sidebarOpen = !sidebarOpen} onOpenSettings={() => settingsOpen = true} />
    <ChatWindow />
    <InputBar />
  </main>

  <PushToast />
  <Settings bind:open={settingsOpen} />
  <PermissionModal />
</div>

<style>
  .layout {
    display: flex;
    height: 100%;
    width: 100%;
    overflow: hidden;
  }

  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: 100%;
  }
</style>
