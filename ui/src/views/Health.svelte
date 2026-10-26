<script lang="ts">
  import { get, post, put } from "../lib/api";
  import Bars from "../lib/charts/Bars.svelte";
  import Line from "../lib/charts/Line.svelte";

  const ranges = [7, 30, 90] as const;
  let days = $state<number>(readRange());
  let series = $state<any[]>([]);
  let base = $state<any>(null);
  let intra = $state<any>(null);
  let day = $state(isoDay(new Date()));
  let loading = $state(false);
  let error = $state<string | null>(null);
  let tables = $state<Record<string, boolean>>({});
  let weekly = $state<any>(null);
  let writing = $state(false);
  let weeklyError = $state<string | null>(null);
  let sentLine = $state(false);

  get("/health/summary").then((r) => (weekly = r.summary)).catch(() => {});
  async function writeSummary() {
    writing = true;
    weeklyError = null;
    sentLine = false;
    try { weekly = (await post("/health/summary")).summary; } catch (e: any) { weeklyError = e.message; } finally { writing = false; }
  }
  async function useLine() {
    await put("/insight", { text: weekly.insight_line });
    sentLine = true;
  }

  function readRange(): number {
    try { return Number(localStorage.getItem("cmf-range")) || 30; } catch { return 30; }
  }
  function isoDay(d: Date) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  const short = (iso: string) => new Date(iso + "T12:00").toLocaleDateString([], { month: "short", day: "numeric" });
  const long = (iso: string) => new Date(iso + "T12:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  const hm = (s: number) => `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
  const n0 = (v: number) => Math.round(v).toLocaleString();

  async function load() {
    loading = true;
    try {
      [series, base] = await Promise.all([get(`/health/series?days=${days}`), get("/health/baselines")]).then(([s, b]) => [s.days, b]);
      error = null;
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
  async function loadDay() {
    try { intra = await get(`/health/intraday?date=${day}`); } catch (e: any) { error = e.message; }
  }
  $effect(() => { days; load(); });
  $effect(() => { day; loadDay(); });

  function setRange(r: number) {
    days = r;
    try { localStorage.setItem("cmf-range", String(r)); } catch { /* convenience only */ }
  }
  function shiftDay(delta: number) {
    const d = new Date(day + "T12:00");
    d.setDate(d.getDate() + delta);
    if (d <= new Date()) day = isoDay(d);
  }

  const today = $derived(series.length ? series[series.length - 1] : {});
  const flags = $derived(base ? Object.entries(base).filter(([k, v]: any) => k !== "_meta" && v.flag).map(([, v]: any) => v.flag) : []);
  const rhrBase = $derived(base?.hr_resting);
  const rhrDelta = $derived(rhrBase?.baseline != null && today.hr_resting != null && rhrBase.n >= 3 ? today.hr_resting - rhrBase.baseline : null);

  const stepRows = $derived(series.map((r) => ({ label: short(r.date), title: long(r.date), values: { steps: r.steps } })));
  const sleepSeries = [
    { key: "sleep_deep_s", label: "Deep", color: "var(--series-1)" },
    { key: "sleep_core_s", label: "Core", color: "var(--series-2)" },
    { key: "sleep_rem_s", label: "REM", color: "var(--series-3)" },
    { key: "sleep_awake_s", label: "Awake", color: "var(--series-4)" },
  ];
  const sleepRows = $derived(series.map((r) => ({
    label: short(r.date), title: `Night ending ${long(r.date)}`,
    values: Object.fromEntries(sleepSeries.map((s) => [s.key, r[s.key] ? r[s.key] / 3600 : undefined])),
  })));
  const dayStart = (iso: string) => new Date(iso + "T00:00").getTime() / 1000;
  const rhrPoints = $derived(series.filter((r) => r.hr_resting != null).map((r) => [dayStart(r.date) + 43200, r.hr_resting] as [number, number]));
  const rangeX0 = $derived(series.length ? dayStart(series[0].date) : 0);
  const rangeX1 = $derived(series.length ? dayStart(series[series.length - 1].date) + 86400 : 1);
  const dateTicks = $derived(series.filter((_, i) => (series.length - 1 - i) % Math.ceil(series.length / 6) === 0).map((r) => ({ t: dayStart(r.date) + 43200, label: short(r.date) })));
  const rhrBand = $derived(rhrBase?.n >= 7 && rhrBase.spread ? { lo: rhrBase.baseline - rhrBase.spread, hi: rhrBase.baseline + rhrBase.spread } : null);
  const hourTicks = $derived([0, 6, 12, 18, 24].map((h) => ({ t: dayStart(day) + h * 3600, label: h === 24 ? "" : `${String(h).padStart(2, "0")}:00` })));
  const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
</script>

<div class="filters">
  <span class="muted">Range</span>
  {#each ranges as r}
    <button class:on={days === r} onclick={() => setRange(r)}>{r} days</button>
  {/each}
  {#if error}<span class="error">{error}</span>{/if}
</div>

<div class:loading>
  <section class="tiles">
    <div class="tile hero">
      <div class="label">Steps today</div>
      <div class="value big">{today.steps != null ? n0(today.steps) : "–"}</div>
      <div class="sub muted">{today.distance_m != null ? `${(today.distance_m / 1000).toFixed(2)} km` : ""}</div>
    </div>
    <div class="tile">
      <div class="label">Active energy</div>
      <div class="value">{today.calories != null ? `${n0(today.calories)} kcal` : "–"}</div>
    </div>
    <div class="tile">
      <div class="label">Resting heart rate</div>
      <div class="value">{today.hr_resting != null ? `${n0(today.hr_resting)} bpm` : "–"}</div>
      <div class="sub muted">
        {#if rhrDelta != null}{rhrDelta > 0 ? "+" : ""}{rhrDelta.toFixed(0)} vs your usual {n0(rhrBase.baseline)}
        {:else}baseline after {base?._meta.min_days ?? 7} days of data{/if}
      </div>
    </div>
    <div class="tile">
      <div class="label">Sleep last night</div>
      <div class="value">{today.sleep_s != null ? hm(today.sleep_s) : "–"}</div>
      <div class="sub muted">{today.sleep_deep_s != null ? `${hm(today.sleep_deep_s)} deep` : ""}</div>
    </div>
    <div class="tile">
      <div class="label">Stress</div>
      <div class="value">{today.stress_avg != null ? n0(today.stress_avg) : "–"}</div>
      <div class="sub muted">{today.stress_avg != null ? (today.stress_avg < 30 ? "relaxed" : today.stress_avg < 60 ? "normal" : today.stress_avg < 80 ? "medium" : "high") : ""}</div>
    </div>
    <div class="tile">
      <div class="label">Blood oxygen</div>
      <div class="value">{today.spo2_avg != null ? `${n0(today.spo2_avg)}%` : "–"}</div>
    </div>
  </section>

  {#if flags.length}
    <section class="flags">
      {#each flags as f}<div class="flag"><span class="icon" aria-hidden="true">▲</span><strong>Worth a look:</strong> {f}</div>{/each}
      <p class="faint small">Personal-trend hints against your own baseline, not medical advice.</p>
    </section>
  {/if}

  <section class="card weekly">
    <header>
      <h2>This week</h2>
      <button class="link" onclick={writeSummary} disabled={writing}>{writing ? "Claude is writing…" : weekly ? "Rewrite" : "Write my weekly recap"}</button>
    </header>
    {#if weekly}
      <p class="headline">{weekly.headline}</p>
      <ul>{#each weekly.observations as o}<li>{o}</li>{/each}</ul>
      <p class="faint small">
        Watch line: “{weekly.insight_line}” ·
        <button class="link" onclick={useLine} disabled={sentLine}>{sentLine ? "on your face after the next sync" : "use on the watch face"}</button>
        · written {weekly.generated_at.replace("T", " ")} by Claude from daily totals only
      </p>
    {:else if !weeklyError}
      <p class="faint small">Claude reads your daily totals (no minute-by-minute data) and writes a short recap.</p>
    {/if}
    {#if weeklyError}<p class="error small">{weeklyError}</p>{/if}
  </section>

  <section class="card">
    <header><h2>Steps per day</h2><button class="link" onclick={() => (tables.steps = !tables.steps)}>{tables.steps ? "Chart" : "Table"}</button></header>
    {#if tables.steps}
      <table><thead><tr><th>Day</th><th>Steps</th><th>km</th><th>kcal</th></tr></thead><tbody>
        {#each [...series].reverse() as r}<tr><td>{long(r.date)}</td><td class="num">{r.steps != null ? n0(r.steps) : "–"}</td><td class="num">{r.distance_m != null ? (r.distance_m / 1000).toFixed(2) : "–"}</td><td class="num">{r.calories != null ? n0(r.calories) : "–"}</td></tr>{/each}
      </tbody></table>
    {:else}
      <Bars rows={stepRows} series={[{ key: "steps", label: "steps", color: "var(--series-1)" }]} fmt={n0} />
    {/if}
  </section>

  <div class="two">
    <section class="card">
      <header><h2>Resting heart rate</h2><button class="link" onclick={() => (tables.rhr = !tables.rhr)}>{tables.rhr ? "Chart" : "Table"}</button></header>
      <p class="faint small">{rhrBand ? "Shaded band: your usual range (last 28 days)." : "Your usual range appears after 7 days of data."}</p>
      {#if tables.rhr}
        <table><tbody>{#each [...series].reverse().filter((r) => r.hr_resting != null) as r}<tr><td>{long(r.date)}</td><td class="num">{n0(r.hr_resting)} bpm</td></tr>{/each}</tbody></table>
      {:else}
        <Line points={rhrPoints} x0={rangeX0} x1={rangeX1} color="var(--series-1)" label="bpm" xTicks={dateTicks}
          xTitle={(t) => long(isoDay(new Date(t * 1000)))} band={rhrBand} />
      {/if}
    </section>

    <section class="card">
      <header><h2>Sleep per night</h2><button class="link" onclick={() => (tables.sleep = !tables.sleep)}>{tables.sleep ? "Chart" : "Table"}</button></header>
      {#if tables.sleep}
        <table><thead><tr><th>Night ending</th><th>Total</th>{#each sleepSeries as s}<th>{s.label}</th>{/each}</tr></thead><tbody>
          {#each [...series].reverse().filter((r) => r.sleep_s != null) as r}
            <tr><td>{long(r.date)}</td><td class="num">{hm(r.sleep_s)}</td>{#each sleepSeries as s}<td class="num">{r[s.key] != null ? hm(r[s.key]) : "–"}</td>{/each}</tr>
          {/each}
        </tbody></table>
      {:else}
        <Bars rows={sleepRows} series={sleepSeries} fmt={(h) => hm(h * 3600)} yFmt={(h) => `${h}h`} />
      {/if}
    </section>
  </div>

  <section class="card">
    <header>
      <h2>Heart rate · {long(day)}</h2>
      <div class="daynav">
        <button onclick={() => shiftDay(-1)} aria-label="previous day">‹</button>
        <button onclick={() => shiftDay(1)} disabled={day === isoDay(new Date())} aria-label="next day">›</button>
        <button class="link" onclick={() => (tables.hr = !tables.hr)}>{tables.hr ? "Chart" : "Table"}</button>
      </div>
    </header>
    {#if intra}
      {#if tables.hr}
        <table><tbody>{#each intra.hr as [t, v]}<tr><td>{clock(t)}</td><td class="num">{v} bpm</td></tr>{/each}</tbody></table>
      {:else}
        <Line points={intra.hr} x0={dayStart(day)} x1={dayStart(day) + 86400} color="var(--series-2)" label="bpm"
          xTicks={hourTicks} xTitle={clock} gapOver={1800} />
        <p class="faint small">Gaps are times the watch wasn't measuring (off-wrist or between readings).</p>
      {/if}
    {/if}
  </section>
</div>

<style>
  .filters { display: flex; align-items: center; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }
  .filters button { background: none; }
  .filters button.on { background: var(--surface-2); border-color: var(--faint); }
  .loading { opacity: 0.6; transition: opacity 0.2s; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr)); gap: 12px; margin-bottom: 16px; }
  .tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; }
  .hero { grid-row: span 1; }
  .label { color: var(--muted); font-size: 12px; }
  .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
  .value.big { font-size: 48px; line-height: 1.05; }
  .sub { font-size: 12px; margin-top: 2px; }
  .flags { margin-bottom: 16px; }
  .flag { border: 1px solid var(--border); border-left: 3px solid var(--status-serious); border-radius: 8px; padding: 8px 12px; margin-bottom: 6px; background: var(--surface); }
  .flag .icon { color: var(--status-serious); margin-right: 6px; font-size: 11px; }
  .card { margin-bottom: 16px; min-width: 0; } /* grid items may shrink below their chart's width */
  .card header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; gap: 8px; }
  .two { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 16px; }
  .two .card { margin-bottom: 0; }
  .two { margin-bottom: 16px; }
  .link { background: none; border: none; color: var(--muted); padding: 2px 4px; font-size: 12px; }
  .link:hover { color: var(--text); }
  .weekly .headline { font-size: 15px; font-weight: 600; margin: 0 0 6px; }
  .weekly ul { margin: 0 0 8px; padding-left: 18px; }
  .weekly li { margin: 2px 0; }
  .daynav { display: flex; gap: 4px; align-items: center; }
  .small { font-size: 12px; margin: 4px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: 500; }
  th, td { padding: 4px 8px 4px 0; border-bottom: 1px solid var(--border); }
  @media (max-width: 640px) { .two { grid-template-columns: 1fr; } .value.big { font-size: 40px; } }
</style>
