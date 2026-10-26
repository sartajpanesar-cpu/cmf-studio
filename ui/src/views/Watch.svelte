<script lang="ts">
  import { get, post } from "../lib/api";

  let { status, onchange }: { status: any; onchange: () => void } = $props();
  let jobs = $state<any[]>([]);
  let events = $state<any[]>([]);
  let busy = $state<string | null>(null);
  let message = $state<string | null>(null);
  let title = $state("CMF Studio");
  let body = $state("hello from the Mac");

  const time = (t: number | null) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "–");

  async function load() {
    [jobs, events] = await Promise.all([get("/jobs?limit=15"), get("/events?limit=40")]);
    events = events.filter((e) => e.kind !== "frame");
  }
  load();

  async function run(label: string, fn: () => Promise<any>) {
    busy = label;
    message = null;
    try {
      const r = await fn();
      message = r?.state ? `${label}: ${r.state}` : `${label}: done`;
    } catch (e: any) {
      message = `${label} failed: ${e.message}`;
    } finally {
      busy = null;
      onchange();
      load();
    }
  }
</script>

<div class="grid">
  <section class="card">
    <h2>Watch</h2>
    {#if status}
      <dl>
        <dt>State</dt><dd>{status.link.state}</dd>
        <dt>Battery</dt><dd class="num">{status.link.battery ?? "–"}%</dd>
        <dt>Firmware</dt><dd>{status.link.firmware ?? "–"}</dd>
        <dt>Connected since</dt><dd>{time(status.link.connected_since)}</dd>
        <dt>Last keepalive</dt><dd>{time(status.link.last_keepalive_at)}</dd>
        <dt>Faces on watch</dt>
        <dd>{status.link.dials ? `${status.link.dials.total}/${status.link.dials.max}, showing ${status.link.dials.active_id === 0xffffffff ? "the custom face" : "a store face"}` : "–"}</dd>
      </dl>
      {#if status.link.state === "needs_pair"}<p class="error">Needs pairing: run <code>uv run cmf pair</code>.</p>{/if}
    {/if}
    <div class="row">
      <button disabled={!!busy} onclick={() => run("Sync", () => post("/sync"))}>{busy === "Sync" ? "Syncing…" : "Sync health data"}</button>
    </div>
    {#if message}<p class="muted">{message}</p>{/if}
  </section>

  <section class="card">
    <h2>Send a notification</h2>
    <label>Title <input bind:value={title} maxlength="20" /></label>
    <label>Body <input bind:value={body} maxlength="128" /></label>
    <button disabled={!!busy || !title} onclick={() => run("Notify", () => post("/notify", { title, body }))}>Send</button>
  </section>

  <section class="card wide">
    <h2>Recent activity</h2>
    <table>
      <tbody>
        {#each jobs as j}
          <tr><td class="faint num">{time(j.created_at)}</td><td>{j.kind}</td><td class:error={j.state === "failed"}>{j.state}</td>
            <td class="muted small">{j.result ? JSON.stringify(j.result).slice(0, 90) : j.error ?? ""}</td></tr>
        {/each}
      </tbody>
    </table>
    <h2 class="sub">Events</h2>
    <table>
      <tbody>
        {#each events.slice(0, 15) as e}
          <tr><td class="faint num">{time(e.ts)}</td><td>{e.kind}</td><td class="muted small" colspan="2">{JSON.stringify(e.data).slice(0, 120)}</td></tr>
        {/each}
      </tbody>
    </table>
  </section>
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 16px; }
  .wide { grid-column: 1 / -1; overflow-x: auto; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; margin: 12px 0; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  label { display: grid; gap: 4px; margin: 10px 0; color: var(--muted); }
  label input { color: var(--text); }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  td { padding: 4px 8px 4px 0; border-bottom: 1px solid var(--border); vertical-align: top; }
  .small { font-size: 12px; word-break: break-all; }
  .sub { margin-top: 18px; }
</style>
