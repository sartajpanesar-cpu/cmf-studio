<script lang="ts">
  // The watch firmware's live clock on a photo dial, drawn from the measured style geometry
  // (src/cmfstudio/render/clock.py, from photos of each style). Preview only.
  let { svg, scale }: { svg: string; scale: number } = $props();
</script>

<div class="clock" style="transform:scale({scale})">{@html svg}</div>

<style>
  .clock { position: absolute; left: 0; top: 0; width: 466px; height: 466px; transform-origin: 0 0; pointer-events: none; }
</style>
