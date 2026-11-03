<script lang="ts">
  // Pick a font for the watch-drawn digits: a gallery of clock-friendly Google fonts, any Google font by name,
  // or an uploaded .ttf / .otf. `font = null` means the layout's own digits.
  import { GOOGLE_FONTS, type FontRef } from "./face";

  let { font, weight, onchange }: {
    font: FontRef | null; weight: number; onchange: (font: FontRef | null, weight: number) => void;
  } = $props();

  // Fonts that read well as big digits, by style. [family, weight] — each checked against Google Fonts.
  const PRESETS: [string, [string, number][]][] = [
    ["Digital", [["Orbitron", 600], ["Share Tech Mono", 400], ["Audiowide", 400], ["Michroma", 400], ["Chakra Petch", 600],
      ["Rajdhani", 600], ["Oxanium", 600], ["Major Mono Display", 400], ["VT323", 400], ["Silkscreen", 400],
      ["Doto", 700], ["Sixtyfour", 400], ["Tektur", 600]]],
    ["Clean", [["Inter", 600], ["Manrope", 700], ["Outfit", 600], ["Sora", 600], ["Space Grotesk", 600], ["Lexend", 500],
      ["Barlow Condensed", 600], ["Oswald", 500], ["Bebas Neue", 400], ["Anton", 400], ["Big Shoulders Display", 700],
      ["Teko", 500], ["Unbounded", 600]]],
    ["Elegant", [["Playfair Display", 700], ["DM Serif Display", 400], ["Cormorant Garamond", 600], ["Abril Fatface", 400], ["Fraunces", 600]]],
    ["Playful", [["Nunito", 800], ["Fredoka", 600], ["Righteous", 400], ["Bungee", 400], ["Monoton", 400], ["Pacifico", 400], ["Caveat", 600]]],
    ["Mono", [["JetBrains Mono", 600], ["IBM Plex Mono", 600], ["Space Mono", 700], ["Roboto Mono", 600]]],
  ];
  // only the digits and colon of each font, so the gallery stays light
  const PRESET_CSS =
    "https://fonts.googleapis.com/css2?" +
    PRESETS.flatMap(([, fs]) => fs).map(([f, w]) => `family=${f.replace(/ /g, "+")}:wght@${w}`).join("&") +
    "&display=swap&text=" + encodeURIComponent("0123456789:");

  const google = (family: string, w: number): FontRef => ({ family, source: "google", weights: [w] });

  function upload(e: Event) {
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    const fr = new FileReader();
    fr.onload = () => {
      const ext = file.name.toLowerCase().endsWith(".otf") ? "otf" : "ttf";
      const uri = String(fr.result).replace(/^data:[^;]*;/, `data:font/${ext};`);
      const family = file.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9 _.-]/g, " ").trim().slice(0, 60) || "Uploaded";
      onchange({ family, source: "local", path: uri, weights: [weight] }, weight);
    };
    fr.readAsDataURL(file);
  }
</script>

<svelte:head><link rel="stylesheet" href={PRESET_CSS} /></svelte:head>

<div class="gallery">
  {#each PRESETS as [group, fonts]}
    <p class="muted tiny group">{group}</p>
    <div class="chips">
      {#each fonts as [family, w]}
        <button class="chip" class:on={font?.family === family} title={family} onclick={() => onchange(google(family, w), w)}>
          <span style="font-family:'{family}',sans-serif;font-weight:{w}">10:08</span><small>{family}</small>
        </button>
      {/each}
    </div>
  {/each}
  <button class="small" class:on={font == null} onclick={() => onchange(null, weight)}>Layout's own digits</button>
</div>
<p class="muted tiny">{font ? `${font.family} · ${weight}` : "Layout's own digits"} — or type any Google font:</p>
<div class="row">
  <input list="gallery-gfonts" placeholder="Font name" value={font?.source === "google" ? font.family : ""}
    onchange={(e) => { const f = (e.currentTarget as HTMLInputElement).value.trim(); onchange(f ? google(f, weight) : null, weight); }} />
  <datalist id="gallery-gfonts">{#each GOOGLE_FONTS as f}<option value={f}></option>{/each}</datalist>
  <select value={weight} aria-label="weight" onchange={(e) => { const w = Number((e.currentTarget as HTMLSelectElement).value); onchange(font ? { ...font, weights: [w] } : null, w); }}>
    {#each [300, 400, 500, 600, 700, 800, 900] as w}<option value={w}>{w}</option>{/each}
  </select>
</div>
<label class="file">or upload a .ttf / .otf
  <input type="file" accept=".ttf,.otf,font/ttf,font/otf" onchange={upload} />
</label>
{#if font?.source === "local"}<p class="muted tiny">Using uploaded font “{font.family}”.</p>{/if}

<style>
  .gallery { max-height: 260px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; padding: 4px 8px 8px; }
  .group { margin: 8px 0 4px; }
  .chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); gap: 4px; }
  .chip { display: grid; padding: 4px 2px; background: none; line-height: 1.2; white-space: nowrap; overflow: hidden; }
  .chip span { font-size: 21px; }
  .chip small { font-size: 9px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; }
  .chip.on, .small.on { border-color: var(--accent); }
  .chip.on { color: var(--accent); }
  .small { font-size: 12px; padding: 3px 8px; background: none; margin-top: 6px; }
  .tiny { font-size: 11px; margin: 4px 0 0; }
  .row { display: flex; gap: 6px; margin-top: 6px; }
  .row input { flex: 1; min-width: 0; }
  .file { display: grid; gap: 3px; color: var(--muted); font-size: 12px; margin-top: 8px; }
</style>
