<script lang="ts">
  // Instant face preview: the spec's HTML (from watchd, same builder as the exact render) in two
  // double-buffered iframes, scaled into a round bezel. On top: the firmware clock, a selection outline,
  // snap guides, and a pointer layer that picks the element under the pointer and drags it.
  import { SIZE, type Layer } from "./face";
  import ClockOverlay from "./ClockOverlay.svelte";

  export type Target = number | "clock";
  type Box = [number, number, number, number];

  let {
    html, exactPng = null, mode = "live", clockSvg = "", clockBox = null, clockAnchor, layers, selected,
    onselect, onmove, onboxes,
  }: {
    html: string; exactPng?: string | null; mode?: "live" | "exact"; clockSvg?: string; clockBox?: Box | null;
    clockAnchor: [number, number];
    layers: Layer[]; selected: Target | null;
    onselect: (t: Target | null) => void;
    onmove: (t: Target, x: number, y: number) => void; // new anchor (layer x/y, cx/cy, or clock x/y)
    onboxes?: (boxes: Record<number, Box>) => void;
  } = $props();

  const SNAP = 7;
  const C = SIZE / 2;

  let box = $state<HTMLDivElement | null>(null);
  let frames: HTMLIFrameElement[] = [];
  let size = $state(400);
  let front = $state(0);
  let docs = $state<[string, string]>(["", ""]);
  let domBoxes = $state<Record<number, Box>>({});
  let drag = $state<{ t: Target; p0: [number, number]; a0: [number, number]; b0: Box; d: [number, number] } | null>(null);
  let guides = $state<{ v: boolean; h: boolean }>({ v: false, h: false });

  $effect(() => {
    const next = 1 - front;
    if (html && html !== docs[front]) docs[next] = html;
  });

  function loaded(i: number) {
    if (!docs[i] || i === front) return;
    front = i;
    measure();
  }

  // bounding boxes of text/image layers as rendered (iframe document px == face px)
  function measure() {
    const doc = frames[front]?.contentDocument;
    if (!doc) return;
    const out: Record<number, Box> = {};
    doc.querySelectorAll<HTMLElement>("[data-layer]").forEach((el) => {
      const r = el.getBoundingClientRect();
      out[Number(el.dataset.layer)] = [r.left, r.top, r.right, r.bottom];
    });
    domBoxes = out;
    onboxes?.(out);
  }

  function anchor(t: Target): [number, number] {
    if (t === "clock") return clockAnchor;
    const l = layers[t];
    return l.type === "circle" || l.type === "ring" ? [l.cx, l.cy] : [l.x, l.y];
  }

  function bbox(t: Target): Box | null {
    if (t === "clock") return clockBox;
    const l = layers[t];
    if (l.type === "circle" || l.type === "ring") {
      const e = l.r + l.width / 2;
      return [l.cx - e, l.cy - e, l.cx + e, l.cy + e];
    }
    if (l.type === "rect") return [l.x, l.y, l.x + l.w, l.y + l.h];
    return domBoxes[t] ?? null;
  }

  function hits(t: Target, x: number, y: number): boolean {
    if (t === "clock") return !!clockBox && x >= clockBox[0] - 6 && x <= clockBox[2] + 6 && y >= clockBox[1] - 6 && y <= clockBox[3] + 6;
    const l = layers[t];
    if (l.type === "circle" || l.type === "ring") {
      const d = Math.hypot(x - l.cx, y - l.cy);
      return Math.abs(d - l.r) <= l.width / 2 + 10 || (l.type === "circle" && !!l.fill && d <= l.r);
    }
    const b = bbox(t);
    return !!b && x >= b[0] - 4 && x <= b[2] + 4 && y >= b[1] - 4 && y <= b[3] + 4;
  }

  function pick(x: number, y: number): Target | null {
    if (hits("clock", x, y)) return "clock";
    for (let i = layers.length - 1; i >= 0; i--) if (hits(i, x, y)) return i;
    return null;
  }

  const scale = $derived(size / SIZE);
  function toFace(e: PointerEvent): [number, number] {
    const r = box!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * SIZE, ((e.clientY - r.top) / r.height) * SIZE];
  }

  function down(e: PointerEvent) {
    const p = toFace(e);
    const t = pick(p[0], p[1]);
    onselect(t);
    if (t === null) return;
    const b = bbox(t);
    if (!b) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag = { t, p0: p, a0: anchor(t), b0: b, d: [0, 0] };
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    const p = toFace(e);
    let dx = p[0] - drag.p0[0];
    let dy = p[1] - drag.p0[1];
    // snap the element's visual centre to the screen's centre lines
    const cx = (drag.b0[0] + drag.b0[2]) / 2 + dx;
    const cy = (drag.b0[1] + drag.b0[3]) / 2 + dy;
    const v = Math.abs(cx - C) < SNAP;
    const h = Math.abs(cy - C) < SNAP;
    if (v) dx += C - cx;
    if (h) dy += C - cy;
    guides = { v, h };
    drag.d = [dx, dy];
    onmove(drag.t, Math.round(drag.a0[0] + dx), Math.round(drag.a0[1] + dy));
  }

  function up() {
    drag = null;
    guides = { v: false, h: false };
  }

  // outline: while dragging, the starting box shifted by the drag (instant); otherwise the measured box
  const outline = $derived.by(() => {
    if (drag) return [drag.b0[0] + drag.d[0], drag.b0[1] + drag.d[1], drag.b0[2] + drag.d[0], drag.b0[3] + drag.d[1]] as Box;
    return selected === null ? null : bbox(selected);
  });
</script>

<div class="bezel">
  <div class="face" bind:this={box} bind:clientWidth={size}>
    {#if mode === "exact" && exactPng}
      <img src={exactPng} alt="exact watch render" />
    {:else}
      {#each [0, 1] as i}
        <iframe
          bind:this={frames[i]} title="live face preview" sandbox="allow-same-origin" srcdoc={docs[i]}
          onload={() => loaded(i)} style="transform:scale({scale});opacity:{i === front ? 1 : 0}" tabindex="-1"
        ></iframe>
      {/each}
    {/if}
    {#if mode === "live" && clockSvg}<ClockOverlay svg={clockSvg} {scale} />{/if}
    <svg class="ui" viewBox="0 0 466 466" aria-hidden="true">
      {#if guides.v}<line x1="233" x2="233" y1="0" y2="466" class="guide" />{/if}
      {#if guides.h}<line y1="233" y2="233" x1="0" x2="466" class="guide" />{/if}
      {#if outline}
        <rect x={outline[0] - 3} y={outline[1] - 3} width={outline[2] - outline[0] + 6} height={outline[3] - outline[1] + 6}
          rx="4" class="sel" />
      {/if}
    </svg>
    <div class="hit" role="presentation" onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}></div>
  </div>
</div>

<style>
  .bezel {
    width: min(100%, 420px); aspect-ratio: 1; border-radius: 50%; background: #000; padding: 10px;
    box-shadow: 0 0 0 8px #1c1c1f, 0 0 0 9px #2c2c30;
  }
  .face { position: relative; width: 100%; height: 100%; border-radius: 50%; overflow: hidden; background: #000; }
  iframe {
    position: absolute; left: 0; top: 0; width: 466px; height: 466px; border: 0; transform-origin: 0 0;
    pointer-events: none; background: #000;
  }
  img { width: 100%; height: 100%; display: block; }
  .ui { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .guide { stroke: #ff4fd8; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .sel { fill: none; stroke: #4fb3ff; stroke-width: 1.5; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
  .hit { position: absolute; inset: 0; cursor: default; touch-action: none; }
</style>
