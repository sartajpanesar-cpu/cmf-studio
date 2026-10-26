<script lang="ts">
  // Native faces: the watch's own clock, drawn by the firmware in your font. Built from a template layout;
  // installing replaces one face on the watch (a stock face the first time, then this native face).
  import { get, post } from "../lib/api";
  import { GOOGLE_FONTS } from "../lib/face";

  type Template = { id: number; name: string; description: string; has_background: boolean };
  type Font = { family: string; source: "google" | "local"; path?: string | null; weights: number[] };
  type Spec = {
    name: string; template: number; font: Font | null; weight: number; digit_color: string | null;
    background: string | null; background_bottom: string | null; background_face_id: number | null;
    show_date: boolean; show_data: boolean;
  };
  type Dial = { id: number; name: string; active: boolean; kind: "stock" | "custom" | "native" };

  // Fonts that read well as big digits, by style. [family, weight]
  const PRESETS: [string, [string, number][]][] = [
    ["Digital", [["Orbitron", 600], ["Share Tech Mono", 400], ["Audiowide", 400], ["Michroma", 400], ["Chakra Petch", 600],
      ["Rajdhani", 600], ["Oxanium", 600], ["Major Mono Display", 400], ["VT323", 400], ["Silkscreen", 400]]],
    ["Clean", [["Inter", 600], ["Manrope", 700], ["Outfit", 600], ["Sora", 600], ["Space Grotesk", 600], ["Lexend", 500],
      ["Barlow Condensed", 600], ["Oswald", 500], ["Bebas Neue", 400], ["Anton", 400], ["Big Shoulders Display", 700]]],
    ["Elegant", [["Playfair Display", 700], ["DM Serif Display", 400], ["Cormorant Garamond", 600], ["Abril Fatface", 400], ["Fraunces", 600]]],
    ["Playful", [["Nunito", 800], ["Fredoka", 600], ["Righteous", 400], ["Bungee", 400], ["Monoton", 400], ["Pacifico", 400], ["Caveat", 600]]],
    ["Mono", [["JetBrains Mono", 600], ["IBM Plex Mono", 600], ["Space Mono", 700], ["Roboto Mono", 600]]],
  ];
  // only the digits and colon of each font, so the gallery stays light
  const PRESET_CSS =
    "https://fonts.googleapis.com/css2?" +
    PRESETS.flatMap(([, fs]) => fs).map(([f, w]) => `family=${f.replace(/ /g, "+")}:wght@${w}`).join("&") +
    "&display=swap&text=" + encodeURIComponent("0123456789:");

  function pick(family: string, weight: number) {
    spec.weight = weight;
    spec.font = { family, source: "google", weights: [weight] };
  }

  const blank = (): Spec => ({
    name: "my-native-face", template: 362, font: { family: "Orbitron", source: "google", weights: [600] },
    weight: 600, digit_color: "#e8c66a", background: "#000000", background_bottom: null, background_face_id: null,
    show_date: true, show_data: false,
  });

  let templates = $state<Template[]>([]);
  let spec = $state<Spec>(loadDraft());
  let preview = $state<{ png: string; bytes: number; warnings: string[] } | null>(null);
  let rendering = $state(false);
  let error = $state<string | null>(null);
  let notice = $state<string | null>(null);
  let saved = $state<any[]>([]);
  let studioFaces = $state<{ id: number; name: string }[]>([]);
  let dials = $state<{ known: boolean; dials: Dial[] }>({ known: false, dials: [] });
  let replace = $state<number | null>(null);
  let confirming = $state(false);
  let installing = $state<string | null>(null);

  let bgMode = $derived(
    spec.background_face_id != null ? "design" : spec.background_bottom ? "gradient" : spec.background ? "colour" : "template",
  );
  const template = $derived(templates.find((t) => t.id === spec.template));
  const onWatch = $derived(dials.dials.find((d) => d.kind === "native"));
  const stock = $derived(dials.dials.filter((d) => d.kind === "stock"));
  const target = $derived(onWatch ?? stock.find((d) => d.id === replace));

  function loadDraft(): Spec {
    try {
      const raw = localStorage.getItem("cmf-native-draft");
      if (raw) {
        const draft = JSON.parse(raw);
        if (!("show_data" in draft) && draft.background == null) draft.background = "#000000"; // pre-cleanup draft
        return { ...blank(), ...draft };
      }
    } catch { /* per-viewer convenience only */ }
    return blank();
  }

  async function load() {
    try {
      [templates, saved, studioFaces, dials] = await Promise.all([
        get("/native/templates"), get("/native/faces"), get("/faces"), get("/watch/dials"),
      ]);
    } catch (e: any) {
      error = e.message;
    }
  }
  load();

  // live preview, debounced; the spec is also kept as a local draft
  let timer: ReturnType<typeof setTimeout> | undefined;
  let generation = 0;
  $effect(() => {
    const body = JSON.stringify(spec);
    try { localStorage.setItem("cmf-native-draft", body); } catch { /* ignore */ }
    clearTimeout(timer);
    timer = setTimeout(() => render(JSON.parse(body)), 450);
  });

  async function render(s: Spec) {
    const mine = ++generation;
    rendering = true;
    try {
      const r = await post("/native/preview", s);
      if (mine !== generation) return;
      preview = { png: `data:image/png;base64,${r.preview_png}`, bytes: r.bytes, warnings: r.warnings };
      error = null;
    } catch (e: any) {
      if (mine === generation) error = e.message;
    } finally {
      if (mine === generation) rendering = false;
    }
  }

  function setFont(family: string) {
    spec.font = family ? { family, source: "google", weights: [spec.weight] } : null;
  }

  function upload(e: Event) {
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    const fr = new FileReader();
    fr.onload = () => {
      const ext = file.name.toLowerCase().endsWith(".otf") ? "otf" : "ttf";
      const uri = String(fr.result).replace(/^data:[^;]*;/, `data:font/${ext};`);
      const family = file.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9 _.-]/g, " ").trim().slice(0, 60) || "Uploaded";
      spec.font = { family, source: "local", path: uri, weights: [spec.weight] };
    };
    fr.readAsDataURL(file);
  }

  function setBg(mode: string) {
    if (mode === "template") Object.assign(spec, { background: null, background_bottom: null, background_face_id: null });
    if (mode === "colour") Object.assign(spec, { background: spec.background ?? "#0b1d3a", background_bottom: null, background_face_id: null });
    if (mode === "gradient")
      Object.assign(spec, { background: spec.background ?? "#0b1d3a", background_bottom: spec.background_bottom ?? "#000000", background_face_id: null });
    if (mode === "design") spec.background_face_id = studioFaces[0]?.id ?? null;
  }

  async function save(): Promise<number> {
    const r = await post("/native/faces", spec);
    saved = await get("/native/faces");
    return r.id;
  }

  async function install() {
    installing = "Saving…";
    error = null;
    notice = null;
    try {
      const id = await save();
      installing = "Sending to the watch…";
      const params = new URLSearchParams({ confirm: "true", ...(onWatch ? {} : { replace: String(replace) }) });
      const r = await post(`/native/faces/${id}/install?${params}`);
      let job = r.job;
      while (!["done", "failed", "expired"].includes(job.state)) {
        await new Promise((res) => setTimeout(res, 1500));
        job = await get(`/jobs/${job.id}`);
      }
      notice = job.state === "done" ? `Installed over ${r.replaces.name} — it's on your watch now.` : null;
      if (job.state !== "done") error = `Install ${job.state}: ${job.error ?? ""}`;
      confirming = false;
      await load();
    } catch (e: any) {
      error = e.message;
    } finally {
      installing = null;
    }
  }

  function open(face: any) {
    spec = { ...blank(), ...face.spec };
    notice = `Loaded “${face.name}”.`;
  }
</script>

<svelte:head><link rel="stylesheet" href={PRESET_CSS} /></svelte:head>

<div class="native">
  <section class="card controls">
    <h2>Native face</h2>
    <p class="muted small">The watch draws the time itself, in your font, from one of these layouts.</p>

    <h3>Layout</h3>
    <div class="templates">
      {#each templates as t}
        <button class="tpl" class:on={spec.template === t.id} onclick={() => (spec.template = t.id)}>
          <strong>{t.name}</strong><span>{t.description}</span>
        </button>
      {/each}
    </div>

    <h3>Font</h3>
    <div class="gallery">
      {#each PRESETS as [group, fonts]}
        <p class="muted tiny group">{group}</p>
        <div class="chips">
          {#each fonts as [family, weight]}
            <button class="chip" class:on={spec.font?.family === family} title={family} onclick={() => pick(family, weight)}>
              <span style="font-family:'{family}',sans-serif;font-weight:{weight}">10:08</span><small>{family}</small>
            </button>
          {/each}
        </div>
      {/each}
      <button class="small" class:on={spec.font == null} onclick={() => (spec.font = null)}>Template digits</button>
    </div>
    <p class="muted tiny">{spec.font ? `${spec.font.family} · ${spec.weight}` : "Template digits"} — or type any Google font:</p>
    <div class="row">
      <input list="native-gfonts" placeholder="Template digits" value={spec.font?.source === "google" ? spec.font.family : ""}
        onchange={(e) => setFont((e.currentTarget as HTMLInputElement).value.trim())} />
      <datalist id="native-gfonts">{#each GOOGLE_FONTS as f}<option value={f}></option>{/each}</datalist>
      <select bind:value={spec.weight} aria-label="weight">
        {#each [300, 400, 500, 600, 700, 800, 900] as w}<option value={w}>{w}</option>{/each}
      </select>
    </div>
    <label class="file">or upload a .ttf / .otf
      <input type="file" accept=".ttf,.otf,font/ttf,font/otf" onchange={upload} />
    </label>
    {#if spec.font?.source === "local"}<p class="muted small">Using uploaded font “{spec.font.family}”.</p>{/if}

    <h3>Digit colour</h3>
    <div class="row">
      <input type="color" value={spec.digit_color ?? "#ffffff"} oninput={(e) => (spec.digit_color = (e.currentTarget as HTMLInputElement).value)} />
      <button class="small" class:on={spec.digit_color == null} onclick={() => (spec.digit_color = null)}>Template colour</button>
    </div>
    <p class="muted tiny">Always-on screen digits keep their dim colour.</p>

    <h3>Background</h3>
    {#if template && !template.has_background}
      <p class="muted small">{template.name} draws black behind the time (it has no background image).</p>
    {:else}
      <div class="seg">
        {#each [["colour", "Colour"], ["gradient", "Gradient"], ["design", "Studio design"], ["template", "Template art"]] as [m, label]}
          <button class:on={bgMode === m} onclick={() => setBg(m)} disabled={m === "design" && !studioFaces.length}>{label}</button>
        {/each}
      </div>
      {#if bgMode === "colour" || bgMode === "gradient"}
        <div class="row">
          <input type="color" bind:value={spec.background} aria-label="top colour" />
          {#if bgMode === "gradient"}<span class="muted">→</span><input type="color" bind:value={spec.background_bottom} aria-label="bottom colour" />{/if}
        </div>
      {:else if bgMode === "design"}
        <select bind:value={spec.background_face_id}>
          {#each studioFaces as f}<option value={f.id}>{f.name}</option>{/each}
        </select>
      {/if}
    {/if}

    <h3>Show</h3>
    <label class="check"><input type="checkbox" bind:checked={spec.show_date} /> Date and weekday</label>
    <label class="check"><input type="checkbox" bind:checked={spec.show_data} /> Data widgets (steps, calories, heart rate, battery…)</label>
    <p class="muted tiny">Off hides them on the watch too. The templates' data widgets are placeholders whose meaning isn't fully verified.</p>

    <h3>Name</h3>
    <input bind:value={spec.name} maxlength="60" />
    <div class="row">
      <button onclick={async () => { await save(); notice = "Saved."; }}>Save</button>
    </div>

    {#if saved.length}
      <h3>Saved</h3>
      <ul class="saved">
        {#each saved as f}
          <li>
            <button class="link" onclick={() => open(f)}>{f.name}</button>
            {#if f.dial_id}<span class="badge">on watch</span>{/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="card stage">
    <div class="bezel" class:busy={rendering}>
      {#if preview}<img src={preview.png} alt="native face preview" />{:else}<div class="empty muted">Rendering…</div>{/if}
    </div>
    {#if preview}
      <p class="muted small num">{(preview.bytes / 1024).toFixed(0)} KB · time, date and data are drawn live by the watch</p>
      {#each preview.warnings as w}<p class="warn small">{w}</p>{/each}
    {/if}
    {#if error}<p class="error">{error}</p>{/if}
    {#if notice}<p class="good">{notice}</p>{/if}

    <div class="install">
      {#if !dials.known}
        <p class="muted small">Waiting for the watch to report its faces (it does after connecting).</p>
      {:else if onWatch}
        <p class="small">Installing updates the native face already on your watch.</p>
      {:else}
        <label class="small">Replace which face on the watch?
          <select bind:value={replace}>
            <option value={null}>Choose a stock face…</option>
            {#each stock as d}<option value={d.id}>{d.name}{d.active ? " (showing now)" : ""}</option>{/each}
          </select>
        </label>
      {/if}
      {#if !confirming}
        <button class="primary" disabled={!dials.known || (!onWatch && replace == null) || !!installing} onclick={() => (confirming = true)}>Install on watch</button>
      {:else}
        <p class="small confirm">
          This replaces <strong>{target?.name}</strong> on your watch{target?.kind === "stock" ? " (you can get it back from the Nothing X app)" : ""}. The new face shows straight away.
        </p>
        <div class="row">
          <button class="primary" disabled={!!installing} onclick={install}>{installing ?? "Replace and install"}</button>
          <button disabled={!!installing} onclick={() => (confirming = false)}>Cancel</button>
        </div>
      {/if}
    </div>
  </section>
</div>

<style>
  .native { display: grid; grid-template-columns: minmax(0, 360px) minmax(0, 1fr); gap: 16px; align-items: start; }
  h3 { font-size: 13px; margin: 16px 0 8px; }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; margin: 4px 0 0; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
  .row input:not([type="color"]) { flex: 1; }
  .controls > input { width: 100%; }
  .templates { display: grid; gap: 6px; }
  .tpl { display: grid; text-align: left; gap: 2px; background: none; }
  .tpl span { color: var(--muted); font-size: 12px; }
  .tpl.on, .seg button.on, .small.on { border-color: var(--accent); }
  .seg { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 6px; }
  .seg button { flex: 1; font-size: 12px; padding: 4px 6px; background: none; }
  .check { display: flex; gap: 8px; align-items: center; margin: 6px 0; font-size: 13px; }
  .gallery { max-height: 300px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; padding: 4px 8px 8px; }
  .group { margin: 8px 0 4px; }
  .chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr)); gap: 4px; }
  .chip { display: grid; padding: 4px 2px; background: none; line-height: 1.2; white-space: nowrap; overflow: hidden; }
  .chip span { font-size: 22px; }
  .chip small { font-size: 9px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; }
  .chip.on { border-color: var(--accent); color: var(--accent); }
  .file { display: grid; gap: 3px; color: var(--muted); font-size: 12px; margin-top: 8px; }
  .saved { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
  .saved li { display: flex; gap: 8px; align-items: center; }
  .link { background: none; border: none; padding: 0; color: var(--text); text-decoration: underline; }
  .badge { font-size: 11px; color: var(--good); }
  .stage { display: grid; justify-items: center; gap: 8px; }
  .bezel {
    width: min(100%, 420px); aspect-ratio: 1; border-radius: 50%; background: #000; padding: 10px;
    box-shadow: 0 0 0 8px #1c1c1f, 0 0 0 9px #2c2c30; transition: opacity 0.2s;
  }
  .bezel.busy { opacity: 0.75; }
  .bezel img { width: 100%; height: 100%; border-radius: 50%; display: block; }
  .empty { display: grid; place-items: center; height: 100%; }
  .install { width: min(100%, 420px); display: grid; gap: 8px; margin-top: 8px; }
  .install select { width: 100%; margin-top: 4px; }
  .confirm { margin: 0; }
  .warn { color: var(--warn); margin: 0; }
  .good { color: var(--good); }
  .error { color: var(--bad); }
  @media (max-width: 760px) { .native { grid-template-columns: minmax(0, 1fr); } }
</style>
