<script lang="ts">
  import { onDestroy } from "svelte";
  import { get, hasToken } from "./lib/api";
  import Health from "./views/Health.svelte";
  import Settings from "./views/Settings.svelte";
  import Studio from "./views/Studio.svelte";
  import Watch from "./views/Watch.svelte";

  const tabs = ["Health", "Studio", "Settings", "Watch"] as const;
  type Tab = (typeof tabs)[number];
  let tab = $state<Tab>(readTab());
  let status = $state<any>(null);
  let statusError = $state<string | null>(null);

  function readTab(): Tab {
    try {
      const t = localStorage.getItem("cmf-tab");
      return (tabs as readonly string[]).includes(t ?? "") ? (t as Tab) : "Health";
    } catch {
      return "Health";
    }
  }
  function choose(t: Tab) {
    tab = t;
    try { localStorage.setItem("cmf-tab", t); } catch { /* per-viewer convenience only */ }
  }

  async function refresh() {
    try {
      status = await get("/status");
      statusError = null;
    } catch (e: any) {
      statusError = e.status === 401 ? "not signed in" : "watchd unreachable";
    }
  }
  refresh();
  const timer = setInterval(refresh, 10_000);
  onDestroy(() => clearInterval(timer));

  const stateLabel: Record<string, string> = {
    connected: "Connected", connecting: "Connecting…", disconnected: "Away", needs_pair: "Needs pairing", stopped: "Stopped",
  };
</script>

{#if !hasToken()}
  <main class="signin card">
    <h1>CMF Studio</h1>
    <p>Open this page with <code>uv run cmf ui</code> — it signs you in with the token from your Keychain.</p>
  </main>
{:else}
  <header>
    <div class="brand">CMF Studio</div>
    <nav>
      {#each tabs as t}
        <button class:active={tab === t} onclick={() => choose(t)}>{t}</button>
      {/each}
    </nav>
    <div class="watch">
      {#if statusError}
        <span class="dot bad"></span><span class="muted">{statusError}</span>
      {:else if status}
        <span class="dot" class:good={status.link.state === "connected"} class:warn={status.link.state !== "connected"}></span>
        <span>{stateLabel[status.link.state] ?? status.link.state}</span>
        {#if status.link.battery != null}<span class="muted num">· {status.link.battery}%</span>{/if}
      {/if}
    </div>
  </header>
  <main>
    {#if tab === "Health"}<Health />{:else if tab === "Studio"}<Studio {status} />{:else if tab === "Settings"}<Settings />{:else}<Watch {status} onchange={refresh} />{/if}
  </main>
{/if}

<style>
  header {
    display: flex; align-items: center; gap: 16px; padding: 10px 20px; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: var(--bg); z-index: 5;
  }
  .brand { font-weight: 700; letter-spacing: 0.02em; }
  nav { display: flex; gap: 4px; }
  nav button { background: none; border-color: transparent; color: var(--muted); }
  nav button.active { color: var(--text); background: var(--surface-2); border-color: var(--border); }
  .watch { margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 13px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
  .dot.good { background: var(--good); } .dot.warn { background: var(--warn); } .dot.bad { background: var(--bad); }
  main { padding: 20px; max-width: 1180px; margin: 0 auto; }
  .signin { max-width: 460px; margin: 15vh auto; }
  @media (max-width: 640px) {
    header { flex-wrap: wrap; padding: 10px 16px; }
    main { padding: 16px; }
  }
</style>
