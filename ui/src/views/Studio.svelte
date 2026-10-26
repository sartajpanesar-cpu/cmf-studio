<script lang="ts">
  import { get, post } from "../lib/api";
  import {
    BINDINGS, GOOGLE_FONTS, RING_METRICS, SIZE, blankFace, layerTitle, newLayer,
    type FaceSpec, type Layer,
  } from "../lib/face";

  let { status = null }: { status?: any } = $props();

  const initial = loadDraft();
  let spec = $state<FaceSpec>(initial);
  let selected = $state<number | null>(initial.layers.length ? 0 : null);
  let preview = $state<{ png: string; checks: any; bytes: number } | null>(null);
  let rendering = $state(false);
  let error = $state<string | null>(null);
  let notice = $state<string | null>(null);
  let saved = $state<{ id: number; name: string }[]>([]);
  let confirming = $state(false);
  let installing = $state(false);
  let previewEl = $state<HTMLImageElement | null>(null);
  let idea = $state("");
  let describing = $state(false);

  async function describe() {
    describing = true;
    error = null;
    try {
      const r = await post("/faces/describe", { description: idea });
      spec = r.spec;
      selected = spec.layers.length ? 0 : null;
      notice = `Claude designed “${r.spec.name}”. Tweak it, then Save or Install.`;
    } catch (e: any) {
      error = e.message;
    } finally {
      describing = false;
    }
  }

  function loadDraft(): FaceSpec {
    try {
      const raw = localStorage.getItem("cmf-studio-draft");
      if (raw) return JSON.parse(raw);
    } catch { /* fall through */ }
    return blankFace();
  }

  // Google fonts are derived from the layers that use them; local fonts stay as uploaded.
  function withFonts(s: FaceSpec): FaceSpec {
    const local = s.fonts.filter((f) => f.source === "local");
    const used = new Set(s.layers.flatMap((l) => (l.type === "text" && l.font ? [l.font] : [])));
    const google = [...used]
      .filter((f) => !local.some((l) => l.family === f))
      .map((family) => ({ family, source: "google" as const, path: null, weights: [400, 600, 700] }));
    return { ...s, fonts: [...local.filter((f) => used.has(f.family)), ...google] };
  }

  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const snapshot = JSON.stringify(spec); // tracks every nested change
    try { localStorage.setItem("cmf-studio-draft", snapshot); } catch { /* draft is a convenience */ }
    clearTimeout(timer);
    timer = setTimeout(() => render(JSON.parse(snapshot)), 600);
  });

  async function render(s: FaceSpec) {
    const mine = ++seq;
    rendering = true;
    try {
      const r = await post("/faces/preview", withFonts(s));
      if (mine !== seq) return;
      preview = { png: `data:image/png;base64,${r.preview_png}`, checks: r.checks, bytes: r.bytes };
      error = null;
    } catch (e: any) {
      if (mine === seq) error = e.message;
    } finally {
      if (mine === seq) rendering = false;
    }
  }

  async function loadSaved() {
    const r = await get("/faces");
    saved = r.faces.map((f: any) => ({ id: f.id, name: f.name }));
  }
  loadSaved();

  async function open(id: number) {
    try {
      spec = (await get(`/faces/${id}`)).spec;
      selected = spec.layers.length ? 0 : null;
      notice = `Opened “${spec.name}”.`;
    } catch (e: any) {
      error = e.message;
    }
  }

  async function save(): Promise<number | null> {
    try {
      const r = await post("/faces", withFonts(spec));
      notice = `Saved “${r.name}”.`;
      await loadSaved();
      return r.id;
    } catch (e: any) {
      error = e.message;
      return null;
    }
  }

  async function install() {
    installing = true;
    confirming = false;
    const id = await save();
    if (id == null) { installing = false; return; }
    try {
      const r = await post(`/faces/${id}/install?confirm=true&force=true`);
      notice = r.job?.state === "done" ? "Installed — it's on your watch now." : `Install ${r.job?.state ?? "queued"}: it runs when the watch connects.`;
    } catch (e: any) {
      error = e.message;
    } finally {
      installing = false;
    }
  }

  function add(type: Layer["type"]) {
    spec.layers.push(newLayer(type));
    selected = spec.layers.length - 1;
  }
  function remove(i: number) {
    spec.layers.splice(i, 1);
    selected = spec.layers.length ? Math.min(i, spec.layers.length - 1) : null;
  }
  function move(i: number, d: number) {
    const j = i + d;
    if (j < 0 || j >= spec.layers.length) return;
    [spec.layers[i], spec.layers[j]] = [spec.layers[j], spec.layers[i]];
    selected = j;
  }

  // click the preview: move the selected layer (or the clock) there
  let placing = $state<"layer" | "clock">("layer");
  function place(e: MouseEvent) {
    if (!previewEl) return;
    const r = previewEl.getBoundingClientRect();
    const x = Math.round(((e.clientX - r.left) / r.width) * SIZE);
    const y = Math.round(((e.clientY - r.top) / r.height) * SIZE);
    if (placing === "clock") {
      spec.clock.x = Math.max(0, Math.min(SIZE - 1, x));
      spec.clock.y = Math.max(0, Math.min(SIZE - 1, y));
      placing = "layer";
      return;
    }
    if (selected == null) return;
    const l = spec.layers[selected];
    if (l.type === "circle" || l.type === "ring") { l.cx = x; l.cy = y; }
    else if (l.type === "text") { l.x = x; l.y = y; }
    else { l.x = Math.round(x - l.w / 2); l.y = Math.round(y - l.h / 2); }
  }

  function readFile(file: File): Promise<string> {
    return new Promise((ok, bad) => {
      const fr = new FileReader();
      fr.onload = () => ok(fr.result as string);
      fr.onerror = () => bad(fr.error);
      fr.readAsDataURL(file);
    });
  }
  async function uploadImage(e: Event, l: Layer) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (f && l.type === "image") l.src = await readFile(f);
  }
  async function uploadFont(e: Event, l: Layer) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!f || l.type !== "text") return;
    const family = f.name.replace(/\.(ttf|otf|woff2?)$/i, "");
    let uri = await readFile(f);
    const ext = f.name.split(".").pop()!.toLowerCase();
    uri = uri.replace(/^data:[^;]*;/, `data:font/${ext};`); // browsers often report fonts as octet-stream
    spec.fonts = [...spec.fonts.filter((x) => x.family !== family), { family, source: "local", path: uri, weights: [400] }];
    l.font = family;
  }

  function textMode(l: Layer & { type: "text" }) {
    return l.bind ? (l.bind.startsWith("static:") ? "static" : "bind") : "text";
  }
  function setTextMode(l: Layer & { type: "text" }, mode: string) {
    if (mode === "text") { l.text = l.text ?? "Hello"; l.bind = null; }
    else { l.text = null; l.bind = "metric:steps"; }
  }

  const sel = $derived(selected != null ? spec.layers[selected] : null);
  const clockStyles = [0, 1, 2, 3, 4];
</script>

<div class="studio">
  <aside class="card left">
    <div class="field"><span>Describe a face</span>
      <textarea rows="3" bind:value={idea} placeholder="e.g. art deco, gold on black, big step count, sleep and resting heart rate at the bottom"></textarea>
    </div>
    <button class="small" disabled={describing || idea.trim().length < 3} onclick={describe}>
      {describing ? "Claude is designing…" : "Design it with Claude"}
    </button>
    <p class="faint tiny">Replaces the current draft. Nothing is installed until you choose to.</p>

    <div class="field"><span>Name</span><input bind:value={spec.name} maxlength="60" /></div>
    <div class="row2">
      <div class="field"><span>Background</span><input type="color" bind:value={spec.background} /></div>
      <div class="field"><span>Refresh</span>
        <select bind:value={spec.refresh}><option value="on_sync">After sync</option><option value="manual">Never</option></select>
      </div>
    </div>

    <h3>Live clock <span class="faint">(drawn by the watch)</span></h3>
    <div class="row2">
      <div class="field"><span>Style</span>
        <select bind:value={spec.clock.style}>{#each clockStyles as s}<option value={s}>Style {s + 1}</option>{/each}</select>
      </div>
      <div class="field"><span>Colour</span><input type="color" bind:value={spec.clock.color} /></div>
    </div>
    <div class="row2">
      <div class="field"><span>X</span><input type="number" min="0" max="465" bind:value={spec.clock.x} /></div>
      <div class="field"><span>Y</span><input type="number" min="0" max="465" bind:value={spec.clock.y} /></div>
    </div>
    <button class="small" onclick={() => (placing = "clock")} class:armed={placing === "clock"}>
      {placing === "clock" ? "Click the preview…" : "Place clock on preview"}
    </button>

    <h3>Layers <span class="faint">(bottom → top)</span></h3>
    <ul class="layers">
      {#each spec.layers as l, i}
        <li class:on={selected === i}>
          <button class="pick" onclick={() => (selected = i)}>{layerTitle(l)}</button>
          <button class="icon" onclick={() => move(i, -1)} disabled={i === 0} aria-label="move down">↓</button>
          <button class="icon" onclick={() => move(i, 1)} disabled={i === spec.layers.length - 1} aria-label="move up">↑</button>
          <button class="icon" onclick={() => remove(i)} aria-label="delete layer">✕</button>
        </li>
      {/each}
    </ul>
    <div class="adds">
      <button onclick={() => add("text")}>+ Text</button>
      <button onclick={() => { add("text"); const l = spec.layers[spec.layers.length - 1]; if (l.type === "text") { l.text = null; l.bind = "metric:hr_resting"; } }}>+ Data</button>
      <button onclick={() => add("ring")}>+ Ring</button>
      <button onclick={() => add("circle")}>+ Circle</button>
      <button onclick={() => add("rect")}>+ Rect</button>
      <button onclick={() => add("image")}>+ Image</button>
    </div>
  </aside>

  <section class="center">
    <div class="watch" class:busy={rendering}>
      {#if preview}
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
        <img bind:this={previewEl} src={preview.png} alt="face preview as the watch will show it" onclick={place} />
      {:else}
        <div class="placeholder faint">{error ? "" : "Rendering…"}</div>
      {/if}
    </div>
    <p class="faint small center-text">
      {#if rendering}Rendering…{:else if preview}{(preview.bytes / 1024).toFixed(0)} KB · {Math.round(preview.checks.lit_fraction * 100)}% of the screen lit{/if}
      · click the face to move the selected layer · box = live clock
    </p>
    {#if preview?.checks.warnings.length}
      {#each preview.checks.warnings as w}<p class="warn small">▲ {w}</p>{/each}
    {/if}
    {#if error}<p class="error small">{error}</p>{/if}
    {#if notice}<p class="muted small">{notice}</p>{/if}

    <div class="actions">
      <select onchange={(e) => { const v = (e.currentTarget as HTMLSelectElement).value; if (v) open(Number(v)); (e.currentTarget as HTMLSelectElement).value = ""; }}>
        <option value="">Open saved…</option>
        {#each saved as f}<option value={f.id}>{f.name}</option>{/each}
      </select>
      <button onclick={() => { spec = blankFace(); selected = 0; notice = null; }}>New</button>
      <button onclick={save}>Save</button>
      <button class="primary" onclick={() => (confirming = true)} disabled={installing || status?.link?.state === "needs_pair"}>
        {installing ? "Installing…" : "Install on watch"}
      </button>
    </div>
    {#if confirming}
      <div class="confirm card">
        <p>This replaces your watch's <strong>only custom photo face</strong> and makes this face active.
          {#if spec.refresh === "on_sync"}It will then update itself after each health sync.{/if}</p>
        <button class="primary" onclick={install}>Replace and install</button>
        <button onclick={() => (confirming = false)}>Cancel</button>
      </div>
    {/if}
  </section>

  <aside class="card right">
    {#if sel}
      <h3>{layerTitle(sel)}</h3>
      {#if sel.type === "text"}
        <div class="field"><span>Content</span>
          <select value={textMode(sel)} onchange={(e) => setTextMode(sel, (e.currentTarget as HTMLSelectElement).value)}>
            <option value="text">Fixed text</option><option value="bind">Live data</option>
          </select>
        </div>
        {#if sel.bind}
          <div class="field"><span>Shows</span>
            <select bind:value={sel.bind}>
              {#each BINDINGS as g}<optgroup label={g.group}>{#each g.options as o}<option value={o.value}>{o.label}</option>{/each}</optgroup>{/each}
            </select>
          </div>
        {:else}
          <div class="field"><span>Text</span><input bind:value={sel.text} maxlength="60" /></div>
        {/if}
        <div class="field"><span>Font</span>
          <input list="gfonts" placeholder="System" value={sel.font ?? ""} onchange={(e) => (sel.font = (e.currentTarget as HTMLInputElement).value || null)} />
          <datalist id="gfonts">{#each GOOGLE_FONTS as f}<option value={f}></option>{/each}</datalist>
        </div>
        <label class="file small">or upload .ttf/.otf <input type="file" accept=".ttf,.otf,.woff,.woff2" onchange={(e) => uploadFont(e, sel)} /></label>
        <div class="row2">
          <div class="field"><span>Size</span><input type="number" min="6" max="400" bind:value={sel.size} /></div>
          <div class="field"><span>Weight</span>
            <select bind:value={sel.weight}>{#each [300, 400, 500, 600, 700, 800] as w}<option value={w}>{w}</option>{/each}</select>
          </div>
        </div>
        <div class="row2">
          <div class="field"><span>Colour</span><input type="color" bind:value={sel.color} /></div>
          <div class="field"><span>Align</span>
            <select bind:value={sel.align}><option value="left">Left</option><option value="center">Centre</option><option value="right">Right</option></select>
          </div>
        </div>
        <div class="row2">
          <div class="field"><span>X</span><input type="number" bind:value={sel.x} /></div>
          <div class="field"><span>Y</span><input type="number" bind:value={sel.y} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>Spacing</span><input type="number" step="0.5" bind:value={sel.letterSpacing} /></div>
          <div class="field"><span>Opacity</span><input type="number" min="0" max="1" step="0.1" bind:value={sel.opacity} /></div>
        </div>
      {:else if sel.type === "ring"}
        <div class="field"><span>Progress of</span>
          <select bind:value={sel.bind}>{#each RING_METRICS as o}<option value={o.value}>{o.label}</option>{/each}</select>
        </div>
        <div class="row2">
          <div class="field"><span>Full at</span><input type="number" min="1" bind:value={sel.max} /></div>
          <div class="field"><span>Thickness</span><input type="number" min="1" max="60" bind:value={sel.width} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>Colour</span><input type="color" bind:value={sel.color} /></div>
          <div class="field"><span>Track</span><input type="color" value={sel.track ?? "#000000"} oninput={(e) => (sel.track = (e.currentTarget as HTMLInputElement).value)} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>Radius</span><input type="number" min="1" bind:value={sel.r} /></div>
          <div class="field"><span>Start°</span><input type="number" bind:value={sel.startDeg} /></div>
        </div>
      {:else if sel.type === "circle"}
        <div class="row2">
          <div class="field"><span>Radius</span><input type="number" min="1" bind:value={sel.r} /></div>
          <div class="field"><span>Stroke width</span><input type="number" min="0" bind:value={sel.width} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>Stroke</span><input type="color" value={sel.stroke ?? "#ffffff"} oninput={(e) => (sel.stroke = (e.currentTarget as HTMLInputElement).value)} /></div>
          <div class="field"><span>Fill</span>
            <label class="inline"><input type="checkbox" checked={!!sel.fill} onchange={(e) => (sel.fill = (e.currentTarget as HTMLInputElement).checked ? "#333333" : null)} />
              {#if sel.fill}<input type="color" bind:value={sel.fill} />{/if}</label>
          </div>
        </div>
        <div class="row2">
          <div class="field"><span>Centre X</span><input type="number" bind:value={sel.cx} /></div>
          <div class="field"><span>Centre Y</span><input type="number" bind:value={sel.cy} /></div>
        </div>
      {:else if sel.type === "rect"}
        <div class="row2">
          <div class="field"><span>Width</span><input type="number" min="1" bind:value={sel.w} /></div>
          <div class="field"><span>Height</span><input type="number" min="1" bind:value={sel.h} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>Colour</span><input type="color" bind:value={sel.fill} /></div>
          <div class="field"><span>Corner</span><input type="number" min="0" bind:value={sel.radius} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>X</span><input type="number" bind:value={sel.x} /></div>
          <div class="field"><span>Y</span><input type="number" bind:value={sel.y} /></div>
        </div>
      {:else if sel.type === "image"}
        <label class="file">Image file <input type="file" accept="image/*" onchange={(e) => uploadImage(e, sel)} /></label>
        <div class="row2">
          <div class="field"><span>Width</span><input type="number" min="1" bind:value={sel.w} /></div>
          <div class="field"><span>Height</span><input type="number" min="1" bind:value={sel.h} /></div>
        </div>
        <div class="row2">
          <div class="field"><span>X</span><input type="number" bind:value={sel.x} /></div>
          <div class="field"><span>Y</span><input type="number" bind:value={sel.y} /></div>
        </div>
        <div class="field"><span>Opacity</span><input type="number" min="0" max="1" step="0.1" bind:value={sel.opacity} /></div>
      {/if}
    {:else}
      <p class="muted">Add a layer to start.</p>
    {/if}
  </aside>
</div>

<style>
  .studio { display: grid; grid-template-columns: 260px minmax(0, 1fr) 260px; gap: 16px; align-items: start; }
  .card { min-width: 0; }
  h3 { font-size: 13px; margin: 16px 0 8px; }
  h3:first-child { margin-top: 0; }
  .field { display: grid; gap: 3px; margin-bottom: 8px; }
  .field > span { color: var(--muted); font-size: 12px; }
  .field input:not([type="color"]):not([type="checkbox"]), .field select { width: 100%; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .layers { list-style: none; padding: 0; margin: 0 0 8px; display: grid; gap: 4px; }
  .layers li { display: flex; gap: 2px; align-items: center; border-radius: 8px; }
  .layers li.on { background: var(--surface-2); }
  .pick { flex: 1; text-align: left; background: none; border-color: transparent; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .icon { background: none; border-color: transparent; padding: 4px 6px; color: var(--muted); }
  .adds { display: flex; flex-wrap: wrap; gap: 6px; }
  .adds button, .small { font-size: 12px; padding: 4px 8px; }
  .armed { border-color: var(--accent); color: var(--accent); }
  .center { display: grid; justify-items: center; gap: 4px; }
  .watch {
    width: min(100%, 420px); aspect-ratio: 1; border-radius: 50%; background: #000; padding: 10px;
    box-shadow: 0 0 0 8px #1c1c1f, 0 0 0 9px #2c2c30; transition: opacity 0.2s;
  }
  .watch.busy { opacity: 0.75; }
  .watch img { width: 100%; height: 100%; border-radius: 50%; display: block; cursor: crosshair; }
  .placeholder { display: grid; place-items: center; height: 100%; }
  .center-text { text-align: center; margin: 14px 0 0; }
  .small { font-size: 12px; }
  .warn { color: var(--warn); margin: 2px 0; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 8px; }
  .confirm { max-width: 420px; margin-top: 8px; }
  .confirm p { margin-top: 0; }
  .confirm button { margin-right: 6px; }
  textarea { width: 100%; resize: vertical; }
  .tiny { font-size: 11px; margin: 4px 0 12px; }
  .file { display: grid; gap: 3px; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
  .inline { display: flex; align-items: center; gap: 6px; }
  @media (max-width: 980px) {
    .studio { grid-template-columns: 1fr; }
    .center { order: -1; }
  }
</style>
