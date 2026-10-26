<script lang="ts">
  // Instant face preview: the spec's HTML (from watchd, same builder as the exact render) in two
  // double-buffered iframes, scaled into a round bezel. A pointer layer on top drags layers / the clock.
  import { SIZE, type Clock } from "./face";
  import ClockOverlay from "./ClockOverlay.svelte";

  let {
    html, exactPng = null, mode = "live", clock, onpoint,
  }: {
    html: string; exactPng?: string | null; mode?: "live" | "exact"; clock: Clock;
    onpoint: (x: number, y: number, phase: "start" | "move" | "end", onClock: boolean) => void;
  } = $props();

  let box = $state<HTMLDivElement | null>(null);
  let size = $state(400);
  let front = $state(0); // which iframe is visible
  let docs = $state<[string, string]>(["", ""]);
  let dragging = false;
  let dragClock = false;

  // load new HTML into the hidden frame, swap when it has loaded (no font flash)
  $effect(() => {
    const next = 1 - front;
    if (html && html !== docs[front]) docs[next] = html;
  });
  function loaded(i: number) {
    if (docs[i] && i !== front) front = i;
  }

  const scale = $derived(size / SIZE);

  function toFace(e: PointerEvent) {
    const r = box!.getBoundingClientRect();
    return [Math.round(((e.clientX - r.left) / r.width) * SIZE), Math.round(((e.clientY - r.top) / r.height) * SIZE)];
  }
  function down(e: PointerEvent) {
    const [x, y] = toFace(e);
    dragClock = x >= clock.x - 6 && x <= clock.x + 156 && y >= clock.y - 6 && y <= clock.y + 66;
    dragging = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    onpoint(x, y, "start", dragClock);
  }
  function move(e: PointerEvent) {
    if (!dragging) return;
    const [x, y] = toFace(e);
    onpoint(x, y, "move", dragClock);
  }
  function up(e: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    const [x, y] = toFace(e);
    onpoint(x, y, "end", dragClock);
  }
</script>

<div class="bezel">
  <div class="face" bind:this={box} bind:clientWidth={size}>
    {#if mode === "exact" && exactPng}
      <img src={exactPng} alt="exact watch render" />
    {:else}
      {#each [0, 1] as i}
        <iframe
          title="live face preview" sandbox="allow-same-origin" srcdoc={docs[i]} onload={() => loaded(i)}
          style="transform:scale({scale});opacity:{i === front ? 1 : 0}" tabindex="-1"
        ></iframe>
      {/each}
    {/if}
    <ClockOverlay {clock} {scale} />
    <div class="hit" role="presentation" onpointerdown={down} onpointermove={move} onpointerup={up}></div>
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
  .hit { position: absolute; inset: 0; cursor: grab; touch-action: none; }
  .hit:active { cursor: grabbing; }
</style>
