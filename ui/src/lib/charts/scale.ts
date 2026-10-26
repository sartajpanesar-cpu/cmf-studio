// Small helpers shared by the SVG charts.

export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/** Ticks that always enclose [lo, hi] (first ≤ lo, last ≥ hi), never finer than minStep. */
export function rangeTicks(lo: number, hi: number, count = 4, minStep = 0): number[] {
  const span = hi - lo || 1;
  const raw = Math.max(span / count, minStep);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  const first = Math.floor(lo / step) * step;
  const last = Math.ceil(hi / step) * step;
  for (let v = first; v <= last + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

export const compact = (v: number) =>
  Math.abs(v) >= 10_000 ? `${(v / 1000).toFixed(0)}K` : Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}K` : `${Math.round(v * 10) / 10}`;

/** Path for a column with a 4px rounded data-end and a square base. */
export function columnPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}
