<script lang="ts">
  // Values lead, labels follow; series keyed by a short line in the series colour.
  let { x, y, title, rows, width }: {
    x: number; y: number; title: string; width: number;
    rows: { label: string; value: string; color?: string }[];
  } = $props();
  const left = $derived(x > width - 170 ? x - 166 : x + 12);
</script>

<div class="tip" style="left:{left}px;top:{Math.max(0, y - 10)}px" role="status">
  <div class="t">{title}</div>
  {#each rows as r}
    <div class="r">
      {#if r.color}<span class="key" style="background:{r.color}"></span>{/if}
      <strong class="num">{r.value}</strong><span class="muted">{r.label}</span>
    </div>
  {/each}
</div>

<style>
  .tip {
    position: absolute; pointer-events: none; z-index: 3; min-width: 120px; max-width: 160px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; font-size: 12px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  }
  .t { color: var(--muted); margin-bottom: 2px; }
  .r { display: flex; align-items: center; gap: 6px; }
  .key { width: 10px; height: 2px; border-radius: 1px; flex: none; }
  strong { font-weight: 600; }
</style>
