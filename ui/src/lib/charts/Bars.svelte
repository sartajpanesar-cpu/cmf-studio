<script lang="ts">
  import { columnPath, compact, niceTicks } from "./scale";
  import Tooltip from "./Tooltip.svelte";

  type Row = { label: string; title: string; values: Record<string, number | undefined> };
  type Series = { key: string; label: string; color: string };
  let {
    rows, series, height = 200, fmt = (v: number) => compact(v), yFmt = (v: number) => compact(v), labelLast = true,
  }: {
    rows: Row[]; series: Series[]; height?: number; fmt?: (v: number) => string; yFmt?: (v: number) => string; labelLast?: boolean;
  } = $props();

  let width = $state(300); // measured from the container after mount
  let hover = $state<number | null>(null);
  const pad = { l: 40, r: 8, t: 18, b: 22 };
  const totals = $derived(rows.map((r) => series.reduce((s, se) => s + (r.values[se.key] ?? 0), 0)));
  const ticks = $derived(niceTicks(Math.max(...totals, 0)));
  const ymax = $derived(ticks[ticks.length - 1] || 1);
  const plotW = $derived(Math.max(10, width - pad.l - pad.r));
  const plotH = $derived(height - pad.t - pad.b);
  const band = $derived(plotW / Math.max(1, rows.length));
  const barW = $derived(Math.min(24, band * 0.62));
  const y = (v: number) => pad.t + plotH - (v / ymax) * plotH;
  const labelEvery = $derived(Math.max(1, Math.ceil(rows.length / Math.max(1, plotW / 46))));
  const lastIdx = $derived(totals.findLastIndex((t) => t > 0));

  function segments(i: number) {
    let base = 0;
    const out: { key: string; color: string; d: string }[] = [];
    const present = series.filter((s) => (rows[i].values[s.key] ?? 0) > 0);
    present.forEach((s, j) => {
      const v = rows[i].values[s.key]!;
      const top = y(base + v);
      const bottom = y(base) - (j > 0 ? 2 : 0); // 2px surface gap between stacked segments
      const x = pad.l + band * i + (band - barW) / 2;
      const h = bottom - top;
      const isTop = j === present.length - 1;
      out.push({ key: s.key, color: s.color, d: isTop ? columnPath(x, top, barW, h) : `M${x},${top}h${barW}v${h}h${-barW}Z` });
      base += v;
    });
    return out;
  }
</script>

<div class="wrap" bind:clientWidth={width}>
  {#if series.length > 1}
    <div class="legend">
      {#each series as s}<span><i style="background:{s.color}"></i>{s.label}</span>{/each}
    </div>
  {/if}
  <svg {width} {height} role="img" aria-label="column chart">
    {#each ticks as t}
      <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} class={t === 0 ? "base" : "grid"} />
      <text x={pad.l - 6} y={y(t) + 4} text-anchor="end" class="axis">{yFmt(t)}</text>
    {/each}
    {#each rows as r, i}
      <g class:dim={hover !== null && hover !== i}>
        {#each segments(i) as seg}<path d={seg.d} fill={seg.color} />{/each}
      </g>
      {#if (rows.length - 1 - i) % labelEvery === 0}
        <text x={pad.l + band * i + band / 2} y={height - 6} text-anchor="middle" class="axis">{r.label}</text>
      {/if}
      <rect
        x={pad.l + band * i} y={pad.t} width={band} height={plotH} fill="transparent" tabindex="0" role="button"
        aria-label="{r.title}: {fmt(totals[i])}"
        onpointerenter={() => (hover = i)} onpointerleave={() => (hover = null)}
        onfocus={() => (hover = i)} onblur={() => (hover = null)}
      />
    {/each}
    {#if labelLast && lastIdx >= 0 && series.length === 1}
      <text x={pad.l + band * lastIdx + band / 2} y={y(totals[lastIdx]) - 6} text-anchor="middle" class="val">{fmt(totals[lastIdx])}</text>
    {/if}
  </svg>
  {#if hover !== null}
    <Tooltip
      x={pad.l + band * hover + band / 2} y={y(totals[hover])} {width} title={rows[hover].title}
      rows={series.length > 1
        ? [...series.map((s) => ({ label: s.label, value: rows[hover!].values[s.key] != null ? fmt(rows[hover!].values[s.key]!) : "–", color: s.color })), { label: "total", value: fmt(totals[hover]) }]
        : [{ label: series[0].label, value: totals[hover] ? fmt(totals[hover]) : "no data" }]}
    />
  {/if}
</div>

<style>
  .wrap { position: relative; min-width: 0; }
  svg { display: block; overflow: visible; }
  .grid { stroke: var(--chart-grid); stroke-width: 1; }
  .base { stroke: var(--chart-axis); stroke-width: 1; }
  .axis { fill: var(--faint); font-size: 11px; font-variant-numeric: tabular-nums; }
  .val { fill: var(--text); font-size: 12px; font-weight: 600; }
  .dim { opacity: 0.45; }
  rect:focus { outline: none; }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
</style>
