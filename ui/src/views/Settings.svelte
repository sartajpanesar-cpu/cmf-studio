<script lang="ts">
  // Watch settings. Values are read from the watch; every change is written and read back before it counts.
  import { get, post, put } from "../lib/api";

  type Alarm = { time: string; enabled: boolean; days: string[]; label: string };
  type Reminder = { enabled: boolean; every_min: number; quiet_start: string | null; quiet_end: string | null };
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

  let snap = $state<any>(null);
  let written = $state<any>({});
  let alarms = $state<Alarm[]>([]);
  let goals = $state({ steps: 5000, distance_m: 4000, kcal: 400 });
  let reminders = $state<Record<string, Reminder>>({});
  let sports = $state<number[]>([]);
  let alerts = $state({ low: null as number | null, high: null as number | null, sport_high: null as number | null, spo2_low: null as number | null });
  let units = $state({ metric: true, celsius: true });
  let loading = $state(true);
  let error = $state<string | null>(null);
  let notes = $state<Record<string, string>>({});

  async function load() {
    loading = true;
    error = null;
    try {
      [snap, written] = await Promise.all([get("/settings"), get("/settings/written")]);
      alarms = (snap.alarms ?? []).map((a: Alarm) => ({ ...a }));
      if (snap.goals) goals = { steps: snap.goals.steps, distance_m: snap.goals.distance_m, kcal: snap.goals.kcal };
      reminders = {
        standing_reminder: { ...snap.standing_reminder },
        water_reminder: { ...snap.water_reminder },
      };
      sports = (snap.sports ?? []).map((s: any) => s.code);
      if (written.hr_alerts) alerts = written.hr_alerts;
      if (written.units) units = written.units;
    } catch (e: any) {
      error = e.status === 503 ? "The watch isn't connected — settings are read from it live." : e.message;
    } finally {
      loading = false;
    }
  }
  load();

  async function save(key: string, fn: () => Promise<any>, ok = "Saved on the watch.") {
    notes[key] = "Saving…";
    try {
      const r = await fn();
      notes[key] = r?.encoding?.includes("unverified") ? "Sent (the watch acknowledged it but can't report it back yet)." : ok;
      if (key !== "find") await load();
    } catch (e: any) {
      notes[key] = e.message;
    }
  }

  const toggleDay = (a: Alarm, d: string) => (a.days = a.days.includes(d) ? a.days.filter((x) => x !== d) : [...a.days, d]);
  const sportName = (code: number) => snap?.sports_available.find((s: any) => s.code === code)?.name ?? `Sport ${code}`;
</script>

{#if loading && !snap}
  <p class="muted">Reading settings from the watch…</p>
{:else if error}
  <section class="card"><p class="error">{error}</p><button onclick={load}>Try again</button></section>
{:else if snap}
  <div class="grid">
    <section class="card">
      <h2>Alarms</h2>
      {#each alarms as a, i}
        <div class="alarm">
          <div class="row">
            <input type="time" bind:value={a.time} />
            <label class="check"><input type="checkbox" bind:checked={a.enabled} /> On</label>
            <input class="label" placeholder="Label" maxlength="32" bind:value={a.label} />
            <button class="small" aria-label="delete alarm" onclick={() => (alarms = alarms.filter((_, j) => j !== i))}>✕</button>
          </div>
          <div class="days">
            {#each DAYS as d}<button class="day" class:on={a.days.includes(d)} onclick={() => toggleDay(a, d)}>{d.slice(0, 2)}</button>{/each}
            <span class="muted tiny">{a.days.length ? "" : "one-time"}</span>
          </div>
        </div>
      {/each}
      <div class="row">
        <button disabled={alarms.length >= 10} onclick={() => (alarms = [...alarms, { time: "07:00", enabled: true, days: [], label: "" }])}>Add alarm</button>
        <button class="primary" onclick={() => save("alarms", () => put("/settings/alarms", alarms))}>Save alarms</button>
      </div>
      {#if notes.alarms}<p class="muted small">{notes.alarms}</p>{/if}
    </section>

    <section class="card">
      <h2>Daily goals</h2>
      <label>Steps <input type="number" min="1000" max="60000" step="500" bind:value={goals.steps} /></label>
      <label>Distance (m) <input type="number" min="500" max="99000" step="500" bind:value={goals.distance_m} /></label>
      <label>Active calories (kcal) <input type="number" min="50" max="5000" step="10" bind:value={goals.kcal} /></label>
      <button class="primary" onclick={() => save("goals", () => put("/settings/goals", goals))}>Save goals</button>
      {#if notes.goals}<p class="muted small">{notes.goals}</p>{/if}
      {#if written.goals_sent}<p class="faint tiny">Last sent from here: {written.goals_sent.steps} steps, {written.goals_sent.distance_m} m, {written.goals_sent.kcal} kcal.</p>{/if}
    </section>

    <section class="card">
      <h2>Watch</h2>
      {#each [["dnd", "Do Not Disturb", snap.dnd], ["wrist_raise", "Wake on wrist raise", snap.wrist_raise], ["time_format", "12-hour clock", snap.time_format === "12h"]] as [key, label, value]}
        <label class="check"><input type="checkbox" checked={value as boolean}
          onchange={(e) => save(key as string, () => put(`/settings/switch/${key}`, { on: (e.currentTarget as HTMLInputElement).checked }))} /> {label}</label>
        {#if notes[key as string]}<p class="muted tiny">{notes[key as string]}</p>{/if}
      {/each}
      <h3>Units</h3>
      <div class="row">
        <select bind:value={units.metric}><option value={true}>Kilometres</option><option value={false}>Miles</option></select>
        <select bind:value={units.celsius}><option value={true}>°C</option><option value={false}>°F</option></select>
        <button onclick={() => save("units", () => put("/settings/units", units), "Sent.")}>Set</button>
      </div>
      {#if notes.units}<p class="muted tiny">{notes.units}</p>{/if}
      <h3>Find my watch</h3>
      <button onclick={() => save("find", async () => { const r = await post("/find-watch"); if (!r.acked) throw new Error("no answer from the watch"); return r; }, "Ringing.")}>Ring the watch</button>
      {#if notes.find}<p class="muted tiny">{notes.find}</p>{/if}
      <p class="faint tiny">Using “Find phone” on the watch makes this Mac play a sound.</p>
    </section>

    {#each [["standing_reminder", "Standing reminder"], ["water_reminder", "Water reminder"]] as [key, title]}
      {@const r = reminders[key]}
      <section class="card">
        <h2>{title}</h2>
        {#if r}
          <label class="check"><input type="checkbox" bind:checked={r.enabled} /> On</label>
          <label>Every (minutes) <input type="number" min="30" max="180" step="15" bind:value={r.every_min} /></label>
          <div class="row">
            <label>Quiet from <input type="time" value={r.quiet_start ?? ""} onchange={(e) => (r.quiet_start = (e.currentTarget as HTMLInputElement).value || null)} /></label>
            <label>until <input type="time" value={r.quiet_end ?? ""} onchange={(e) => (r.quiet_end = (e.currentTarget as HTMLInputElement).value || null)} /></label>
          </div>
          <button onclick={() => save(key, () => put(`/settings/reminders/${key}`, r))}>Save</button>
          {#if notes[key]}<p class="muted tiny">{notes[key]}</p>{/if}
        {:else}<p class="muted">The watch didn't report this.</p>{/if}
      </section>
    {/each}

    <section class="card">
      <h2>Health measurements</h2>
      <p class="muted small">All-day measuring on the watch (it can't report these back, so this shows what was last sent from here).</p>
      {#each [["heart_rate", "Heart rate"], ["spo2", "Blood oxygen"], ["stress", "Stress"]] as [kind, label]}
        <label class="check"><input type="checkbox" checked={written.monitoring?.[kind] ?? false}
          onchange={(e) => save(`mon-${kind}`, () => put(`/settings/monitoring/${kind}`, { on: (e.currentTarget as HTMLInputElement).checked }), "Sent.")} /> {label}</label>
        {#if notes[`mon-${kind}`]}<p class="muted tiny">{notes[`mon-${kind}`]}</p>{/if}
      {/each}
      <h3>Alerts</h3>
      <div class="pairs">
        <label>Heart rate below <input type="number" min="30" max="120" placeholder="off" bind:value={alerts.low} /></label>
        <label>Heart rate above <input type="number" min="80" max="220" placeholder="off" bind:value={alerts.high} /></label>
        <label>During workouts above <input type="number" min="80" max="220" placeholder="off" bind:value={alerts.sport_high} /></label>
        <label>Blood oxygen below <input type="number" min="70" max="99" placeholder="off" bind:value={alerts.spo2_low} /></label>
      </div>
      <button onclick={() => save("alerts", () => put("/settings/hr_alerts", Object.fromEntries(Object.entries(alerts).map(([k, v]) => [k, v || null]))), "Sent.")}>Set alerts</button>
      {#if notes.alerts}<p class="muted tiny">{notes.alerts}</p>{/if}
    </section>

    <section class="card">
      <h2>Workout menu</h2>
      <p class="muted small">The sports on the watch's workout list, in order.</p>
      <ol class="sports">
        {#each sports as code, i}
          <li>
            <span>{sportName(code)}</span>
            <button class="small" disabled={i === 0} onclick={() => { const s = [...sports]; [s[i - 1], s[i]] = [s[i], s[i - 1]]; sports = s; }} aria-label="move up">↑</button>
            <button class="small" onclick={() => (sports = sports.filter((c) => c !== code))} aria-label="remove">✕</button>
          </li>
        {/each}
      </ol>
      <div class="row">
        <select onchange={(e) => { const v = Number((e.currentTarget as HTMLSelectElement).value); if (v && !sports.includes(v)) sports = [...sports, v]; (e.currentTarget as HTMLSelectElement).value = ""; }}>
          <option value="">Add a sport…</option>
          {#each snap.sports_available.filter((s: any) => !sports.includes(s.code)) as s}<option value={s.code}>{s.name}</option>{/each}
        </select>
        <button class="primary" disabled={!sports.length} onclick={() => save("sports", () => put("/settings/sports", sports))}>Save menu</button>
      </div>
      {#if notes.sports}<p class="muted tiny">{notes.sports}</p>{/if}
    </section>
  </div>
{/if}

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 16px; align-items: start; }
  h3 { font-size: 13px; margin: 14px 0 6px; }
  label { display: grid; gap: 4px; margin: 8px 0; color: var(--muted); font-size: 13px; }
  label input { color: var(--text); }
  .check { display: flex; gap: 8px; align-items: center; color: var(--text); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
  .alarm { border-bottom: 1px solid var(--border); padding: 8px 0; }
  .alarm .label { flex: 1; min-width: 90px; }
  .days { display: flex; gap: 4px; margin-top: 6px; align-items: center; flex-wrap: wrap; }
  .day { font-size: 11px; padding: 2px 7px; background: none; text-transform: capitalize; }
  .day.on { border-color: var(--accent); color: var(--accent); }
  .pairs { display: grid; grid-template-columns: 1fr 1fr; gap: 0 10px; }
  .sports { padding-left: 20px; margin: 6px 0; display: grid; gap: 4px; }
  .sports li span { display: inline-block; min-width: 150px; }
  .small { font-size: 12px; padding: 2px 8px; }
  .tiny { font-size: 11px; margin: 4px 0 0; }
  .faint { color: var(--faint); }
  .error { color: var(--bad); }
</style>
