<script lang="ts">
  // The Mac's music on the watch: now playing on the music card, and the watch's buttons as a remote.
  import { get, put } from "./api";

  let state = $state<{ settings: { show_now_playing: boolean; buttons_control_music: boolean }; now: any } | null>(null);
  let error = $state<string | null>(null);

  async function load() {
    try {
      state = await get("/media");
    } catch (e: any) {
      error = e.message;
    }
  }
  load();
  const timer = setInterval(load, 5000);
  $effect(() => () => clearInterval(timer));

  async function set(patch: Record<string, boolean>) {
    state = await put("/media/settings", { ...state!.settings, ...patch });
  }
</script>

<section class="card">
  <h2>Music</h2>
  {#if state}
    <label class="check"><input type="checkbox" checked={state.settings.show_now_playing}
      onchange={(e) => set({ show_now_playing: (e.currentTarget as HTMLInputElement).checked })} /> Show what the Mac is playing on the watch</label>
    <label class="check"><input type="checkbox" checked={state.settings.buttons_control_music}
      onchange={(e) => set({ buttons_control_music: (e.currentTarget as HTMLInputElement).checked })} /> Watch music buttons control the Mac</label>
    <p class="small">
      {#if state.now}
        {state.now.state === "playing" ? "▶" : "❚❚"} {state.now.track} — {state.now.artist} <span class="muted">({state.now.app})</span>
      {:else}
        <span class="muted">{state.settings.show_now_playing ? "Nothing playing in Spotify or Music." : "Off."}</span>
      {/if}
    </p>
    <p class="faint tiny">Works with Spotify and Apple Music when they're open (nothing is launched). macOS asks once whether watchd may control them. Volume buttons change the Mac's volume. Buttons only register while the watch's music screen is open.</p>
  {:else if error}<p class="error small">{error}</p>{/if}
</section>

<style>
  .check { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; margin: 4px 0 0; }
  .faint { color: var(--faint); }
  .error { color: var(--bad); }
</style>
