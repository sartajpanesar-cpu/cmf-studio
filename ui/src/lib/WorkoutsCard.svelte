<script lang="ts">
  // Workouts recorded on the watch, with the GPS route drawn as a line (no map tiles: nothing leaves the Mac).
  import { get } from "./api";

  let workouts = $state<any[] | null>(null);
  let open = $state<number | null>(null);
  let route = $state<number[][]>([]);

  get("/workouts?limit=30").then((w) => (workouts = w)).catch(() => (workouts = []));

  async function show(w: any) {
    if (open === w.start_ts) return void (open = null);
    open = w.start_ts;
    route = w.route_points ? (await get(`/workouts/${w.start_ts}/route`)).points : [];
  }

  const when = (t: number) => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const dur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h ` : "") + `${Math.floor((s % 3600) / 60)}m`;
  const km = (m: number | null) => (m ? `${(m / 1000).toFixed(2)} km` : null);
  const pace = (s: number | null) => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} /km` : null);

  // project lat/lon into a 300×200 box, keeping the aspect (equirectangular, fine at workout scale)
  const path = $derived.by(() => {
    if (route.length < 2) return "";
    const lats = route.map((p) => p[1]);
    const lons = route.map((p) => p[2]);
    const k = Math.cos(((Math.min(...lats) + Math.max(...lats)) / 2) * (Math.PI / 180));
    const x0 = Math.min(...lons) * k, x1 = Math.max(...lons) * k, y0 = Math.min(...lats), y1 = Math.max(...lats);
    const s = Math.min(280 / (x1 - x0 || 1e-9), 180 / (y1 - y0 || 1e-9));
    const ox = 10 + (280 - (x1 - x0) * s) / 2, oy = 190 - (180 - (y1 - y0) * s) / 2;  // centred
    return route.map((p, i) => `${i ? "L" : "M"}${(ox + (p[2] * k - x0) * s).toFixed(1)},${(oy - (p[1] - y0) * s).toFixed(1)}`).join(" ");
  });
</script>

<section class="card">
  <h2>Workouts</h2>
  {#if workouts === null}
    <p class="muted">Loading…</p>
  {:else if !workouts.length}
    <p class="muted">No workouts yet. Start one on the watch; it arrives with the next sync (hourly, or Sync now).</p>
  {:else}
    <ul class="list">
      {#each workouts as w}
        <li>
          <button class="row" onclick={() => show(w)}>
            <span class="what">{w.sport_name}</span>
            <span class="muted">{when(w.start_ts)}</span>
            <span class="num">{dur(w.duration_s)}</span>
            <span class="num">{[km(w.distance_m), w.kcal ? `${w.kcal} kcal` : null, w.hr_avg ? `${w.hr_avg} bpm avg` : null].filter(Boolean).join(" · ")}</span>
          </button>
          {#if open === w.start_ts}
            <div class="detail">
              {#if path}
                <svg viewBox="0 0 300 200" role="img" aria-label="route"><path d={path} /></svg>
              {:else}
                <p class="muted small">{w.has_gps ? "Route not received yet." : "No GPS route for this workout."}</p>
              {/if}
              <dl>
                {#if w.steps}<dt>Steps</dt><dd class="num">{w.steps.toLocaleString()}</dd>{/if}
                {#if pace(w.pace_avg_s_km)}<dt>Average pace</dt><dd class="num">{pace(w.pace_avg_s_km)}</dd>{/if}
                {#if w.extra?.cadence_spm}<dt>Cadence</dt><dd class="num">{w.extra.cadence_spm} spm</dd>{/if}
                {#if w.extra?.recovery_min}<dt>Recovery</dt><dd class="num">{dur(w.extra.recovery_min * 60)}</dd>{/if}
                {#if w.extra?.training_load}<dt>Training load</dt><dd class="num">{w.extra.training_load}</dd>{/if}
              </dl>
              <p class="faint tiny">Decoded with the {w.layout} layout.</p>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .list { list-style: none; padding: 0; margin: 0; }
  .list li { border-bottom: 1px solid var(--border); }
  .row { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; width: 100%; text-align: left; background: none; border: 0; padding: 8px 0; }
  .what { font-weight: 600; }
  .detail { display: grid; grid-template-columns: minmax(0, 300px) 1fr; gap: 12px; padding: 4px 0 12px; }
  svg { width: 100%; background: var(--surface-2); border-radius: 8px; }
  path { fill: none; stroke: var(--series-1); stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; align-content: start; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; margin: 0; grid-column: 1 / -1; }
  .faint { color: var(--faint); }
  @media (max-width: 640px) { .detail { grid-template-columns: minmax(0, 1fr); } }
</style>
