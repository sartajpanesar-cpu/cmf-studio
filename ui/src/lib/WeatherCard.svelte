<script lang="ts">
  // Weather on the watch's weather screen; its place line doubles as a glance line.
  import { get, post, put } from "./api";

  type Place = { name: string; latitude: number; longitude: number };
  type Settings = { enabled: boolean; place: Place | null; glance: "place" | "text" | "shortcut"; glance_text: string; glance_shortcut: string };

  let settings = $state<Settings | null>(null);
  let last = $state<any>(null);
  let problem = $state<string | null>(null);
  let query = $state("");
  let results = $state<Place[]>([]);
  let busy = $state<string | null>(null);
  let message = $state<string | null>(null);

  const ICONS: Record<number, string> = {
    0: "–", 1: "☀️", 2: "☁️", 3: "☁️", 4: "🌧", 6: "🌫", 9: "⛈", 11: "🌨", 14: "🌨", 19: "🔥", 20: "🥶", 21: "💨",
    22: "🌫", 23: "🌙", 24: "☁️", 25: "🌫", 26: "⛅",
  };

  async function load() {
    const r = await get("/weather");
    settings = r.settings;
    last = r.last;
    problem = r.error;
  }
  load();

  async function search() {
    busy = "search";
    try {
      results = await get(`/weather/places?q=${encodeURIComponent(query)}`);
      message = results.length ? null : "No place found.";
    } catch (e: any) {
      message = e.message;
    } finally {
      busy = null;
    }
  }

  async function save(patch: Partial<Settings>) {
    settings = await put("/weather/settings", { ...settings, ...patch });
  }

  async function choose(p: Place) {
    results = [];
    query = "";
    await save({ place: p, enabled: true });
    await push();
  }

  async function push() {
    busy = "push";
    message = null;
    try {
      const r = await post("/weather/push");
      message = `Sent: ${r.now_c != null ? Math.round(r.now_c) + "°" : ""} · line “${r.glance}”${r.job.state === "queued" ? " (queued until the watch is back)" : ""}`;
      await load();
    } catch (e: any) {
      message = e.message;
    } finally {
      busy = null;
    }
  }
</script>

<section class="card">
  <h2>Weather & glance line</h2>
  {#if settings}
    <label class="check"><input type="checkbox" checked={settings.enabled} onchange={(e) => save({ enabled: (e.currentTarget as HTMLInputElement).checked })} /> Keep the watch's weather up to date (every 30 min)</label>
    <p class="small">{settings.place ? `Place: ${settings.place.name}` : "No place yet."}</p>
    <form class="row" onsubmit={(e) => { e.preventDefault(); search(); }}>
      <input placeholder="Find a city…" bind:value={query} minlength="2" />
      <button disabled={busy === "search" || query.length < 2}>Search</button>
    </form>
    {#if results.length}
      <ul class="results">{#each results as p}<li><button class="link" onclick={() => choose(p)}>{p.name}</button></li>{/each}</ul>
    {/if}

    <h3>Line under the weather</h3>
    <div class="seg">
      {#each [["place", "Place name"], ["text", "My text"], ["shortcut", "A Shortcut"]] as [m, label]}
        <button class:on={settings.glance === m} onclick={() => save({ glance: m as Settings["glance"] })}>{label}</button>
      {/each}
    </div>
    {#if settings.glance === "text"}
      <input value={settings.glance_text} maxlength="60" placeholder="e.g. Drink water" onchange={(e) => save({ glance_text: (e.currentTarget as HTMLInputElement).value })} />
    {:else if settings.glance === "shortcut"}
      <input value={settings.glance_shortcut} maxlength="100" placeholder="Shortcut name, e.g. Next event" onchange={(e) => save({ glance_shortcut: (e.currentTarget as HTMLInputElement).value })} />
      <p class="muted tiny">Its first line of output is shown (about 30 characters; the watch scrolls long text).</p>
    {/if}

    <div class="row">
      <button disabled={!!busy || !settings.place} onclick={push}>{busy === "push" ? "Sending…" : "Send now"}</button>
    </div>
    {#if last}
      <p class="muted small">Last: {ICONS[last.icon] ?? ""} {last.now_c != null ? Math.round(last.now_c) + "°" : ""} ({last.min_c != null ? Math.round(last.min_c) : "–"}–{last.max_c != null ? Math.round(last.max_c) : "–"}°) · “{last.glance}” · {new Date(last.fetched_at * 1000).toLocaleTimeString([], { timeStyle: "short" })}</p>
    {/if}
    {#if problem}<p class="error small">{problem}</p>{/if}
    {#if message}<p class="muted small">{message}</p>{/if}
    <p class="faint tiny">Forecast from Open-Meteo: only the place's coordinates (≈1 km) leave this Mac.</p>
  {/if}
</section>

<style>
  h3 { font-size: 13px; margin: 14px 0 6px; }
  .check { display: flex; gap: 8px; align-items: center; color: var(--text); margin: 8px 0; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  .row input { flex: 1; }
  input:not([type="checkbox"]) { width: 100%; }
  .results { list-style: none; padding: 0; margin: 6px 0; display: grid; gap: 4px; }
  .link { background: none; border: none; padding: 0; color: var(--text); text-decoration: underline; text-align: left; }
  .seg { display: flex; gap: 4px; margin-bottom: 6px; }
  .seg button { flex: 1; font-size: 12px; padding: 4px 6px; background: none; }
  .seg button.on { border-color: var(--accent); }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; margin: 4px 0 0; }
  .faint { color: var(--faint); }
  .error { color: var(--bad); }
</style>
