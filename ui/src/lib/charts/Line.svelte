<script lang="ts">
  import { rangeTicks } from "./scale";
  import Tooltip from "./Tooltip.svelte";

  type Pt = [number, number];
  let {
    points, x0, x1, color, label, height = 200, fmt = (v: number) => `${Math.round(v)}`,
    xTicks = [], xTitle = (t: number) => `${t}`, band = null, gapOver = Infinity, yMin = null, minStep = 1,
  }: {
    points: Pt[]; x0: number; x1: number; color: string; label: string; height?: number;
    fmt?: (v: number) => string; xTicks?: { t: number; label: string }[]; xTitle?: (t: number) => string;
    band?: { lo: number; hi: number } | null; gapOver?: number; yMin?: number | null; minStep?: number;
  } = $props();

  let width = $state(300); // measured from the container after mount
  let hover = $state<number | null>(null);
  const pad = { l: 40, r: 14, t: 14, b: 22 };
  const plotW = $derived(Math.max(10, width - pad.l - pad.r));
  const plotH = $derived(height - pad.t - pad.b);
  const vals = $derived(points.map((p) => p[1]).concat(band ? [band.lo, band.hi] : []));
  const lo = $derived(yMin ?? (vals.length ? Math.min(...vals) : 0));
  const hi = $derived(vals.length ? Math.max(...vals) : 1);
  // a flat or single-value series still gets a readable span around its value
  const span = $derived(Math.max(hi - lo, minStep * 4));
  const mid = $derived((hi + lo) / 2);
  const yTicks = $derived(rangeTicks(mid - span * 0.55, mid + span * 0.55, 4, minStep));
  const ylo = $derived(yTicks[0] ?? lo);
  const yhi = $derived(yTicks[yTicks.length - 1] ?? hi);
  const sx = (t: number) => pad.l + ((t - x0) / Math.max(1, x1 - x0)) * plotW;
  const sy = (v: number) => pad.t + plotH - ((v - ylo) / Math.max(1e-9, yhi - ylo)) * plotH;

  // break the line where readings are further apart than gapOver (watch off-wrist)
  const path = $derived(
    points.map((p, i) => `${i === 0 || p[0] - points[i - 1][0] > gapOver ? "M" : "L"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(""),
  );

  function move(e: PointerEvent) {
    if (!points.length) return;
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
    const t = x0 + ((e.clientX - rect.left - pad.l) / plotW) * (x1 - x0);
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i][0] - t) < Math.abs(points[best][0] - t)) best = i;
    hover = best;
  }
  const last = $derived(points.length ? points[points.length - 1] : null);
</script>

<div class="wrap" bind:clientWidth={width}>
  <svg {width} {height} role="img" aria-label={label} onpointermove={move} onpointerleave={() => (hover = null)}>
    {#if band}
      <rect x={pad.l} width={plotW} y={sy(band.hi)} height={Math.max(0, sy(band.lo) - sy(band.hi))} fill={color} opacity="0.1" />
    {/if}
    {#each yTicks as t}
      <line x1={pad.l} x2={width - pad.r} y1={sy(t)} y2={sy(t)} class="grid" />
      <text x={pad.l - 6} y={sy(t) + 4} text-anchor="end" class="axis">{fmt(t)}</text>
    {/each}
    {#each xTicks as tk}
      <text x={sx(tk.t)} y={height - 6} text-anchor="middle" class="axis">{tk.label}</text>
    {/each}
    <path d={path} fill="none" stroke={color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
    {#each points as p, i}
      {#if (i === 0 || p[0] - points[i - 1][0] > gapOver) && (i === points.length - 1 || points[i + 1][0] - p[0] > gapOver)}
        <circle cx={sx(p[0])} cy={sy(p[1])} r="3" fill={color} />
      {/if}
    {/each}
    {#if last}
      <circle cx={sx(last[0])} cy={sy(last[1])} r="4" fill={color} stroke="var(--surface)" stroke-width="2" />
      <text x={Math.min(sx(last[0]) + 8, width - pad.r)} y={sy(last[1]) - 8} class="val" text-anchor={sx(last[0]) > width - 60 ? "end" : "start"}>{fmt(last[1])}</text>
    {/if}
    {#if hover !== null}
      <line x1={sx(points[hover][0])} x2={sx(points[hover][0])} y1={pad.t} y2={pad.t + plotH} class="cross" />
      <circle cx={sx(points[hover][0])} cy={sy(points[hover][1])} r="4" fill={color} stroke="var(--surface)" stroke-width="2" />
    {/if}
  </svg>
  {#if hover !== null}
    <Tooltip x={sx(points[hover][0])} y={sy(points[hover][1])} {width} title={xTitle(points[hover][0])}
      rows={[{ label, value: fmt(points[hover][1]), color }]} />
  {/if}
  {#if !points.length}<p class="empty faint">No readings in this period.</p>{/if}
</div>

<style>
  .wrap { position: relative; min-width: 0; }
  svg { display: block; overflow: visible; touch-action: pan-y; }
  .grid { stroke: var(--chart-grid); stroke-width: 1; }
  .cross { stroke: var(--chart-axis); stroke-width: 1; }
  .axis { fill: var(--faint); font-size: 11px; font-variant-numeric: tabular-nums; }
  .val { fill: var(--text); font-size: 12px; font-weight: 600; }
  .empty { position: absolute; inset: 40% 0 auto; text-align: center; margin: 0; }
</style>
