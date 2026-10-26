<script lang="ts">
  // Where and how the watch firmware draws its live clock on a photo dial.
  // The 5 built-in styles haven't been captured yet: until then this is a labelled stand-in at the real
  // anchor (clock.x, clock.y = top-left) in the chosen colour. See `cmf face calibrate-clock`.
  import type { Clock } from "./face";

  let { clock, scale }: { clock: Clock; scale: number } = $props();
</script>

<div class="clock" style="left:{clock.x * scale}px;top:{clock.y * scale}px;color:{clock.color};font-size:{44 * scale}px">
  <span class="digits">10:09</span>
  <span class="tag" style="font-size:{Math.max(9, 10 * scale)}px">style {clock.style + 1} · approximate</span>
</div>

<style>
  .clock { position: absolute; pointer-events: none; line-height: 1; white-space: nowrap; }
  .digits { font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: 0.02em; opacity: 0.85; }
  .tag { display: block; color: #9a9aa2; margin-top: 2px; font-weight: 400; }
</style>
