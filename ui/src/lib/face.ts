// FaceSpec v0 as the Studio edits it (mirrors src/cmfstudio/render/spec.py).

export const SIZE = 466;

export type TextLayer = {
  type: "text"; text?: string | null; bind?: string | null; x: number; y: number; font?: string | null;
  size: number; weight: number; color: string; align: "left" | "center" | "right"; maxWidth?: number | null;
  letterSpacing: number; opacity: number;
};
export type ImageLayer = { type: "image"; src: string; x: number; y: number; w: number; h: number; opacity: number };
export type CircleLayer = {
  type: "circle"; cx: number; cy: number; r: number; fill?: string | null; stroke?: string | null; width: number; opacity: number;
};
export type RectLayer = { type: "rect"; x: number; y: number; w: number; h: number; fill: string; radius: number; opacity: number };
export type RingLayer = {
  type: "ring"; bind: string; max: number; cx: number; cy: number; r: number; width: number; color: string;
  track?: string | null; startDeg: number;
};
export type Layer = TextLayer | ImageLayer | CircleLayer | RectLayer | RingLayer;
export type FontRef = { family: string; source: "google" | "local" | "system"; path?: string | null; weights: number[] };
export type Clock = { style: number; x: number; y: number; color: string };
export type FaceSpec = {
  version: 0; name: string; background: string; fonts: FontRef[]; layers: Layer[]; clock: Clock; refresh: "on_sync" | "manual";
};

export const blankFace = (): FaceSpec => ({
  version: 0,
  name: "my-face",
  background: "#000000",
  fonts: [],
  clock: { style: 0, x: 140, y: 150, color: "#ffffff" },
  refresh: "on_sync",
  layers: [
    { type: "text", bind: "date:%a %d %b", x: 233, y: 110, size: 28, weight: 400, color: "#e8c66a", align: "center", letterSpacing: 1, opacity: 1 },
    { type: "text", bind: "metric:steps", x: 233, y: 320, size: 48, weight: 600, color: "#ffffff", align: "center", letterSpacing: 0, opacity: 1 },
  ],
});

export const newLayer = (type: Layer["type"]): Layer => {
  switch (type) {
    case "text":
      return { type, text: "Hello", bind: null, x: 233, y: 233, size: 32, weight: 400, color: "#ffffff", align: "center", letterSpacing: 0, opacity: 1 };
    case "image":
      return { type, src: "", x: 0, y: 0, w: SIZE, h: SIZE, opacity: 1 };
    case "circle":
      return { type, cx: 233, cy: 233, r: 200, fill: null, stroke: "#ffffff", width: 2, opacity: 1 };
    case "rect":
      return { type, x: 183, y: 360, w: 100, h: 4, fill: "#ffffff", radius: 2, opacity: 1 };
    case "ring":
      return { type, bind: "metric:steps", max: 8000, cx: 233, cy: 233, r: 215, width: 10, color: "#e8c66a", track: "#1a1a1a", startDeg: -90 };
  }
};

export const BINDINGS: { group: string; options: { value: string; label: string }[] }[] = [
  {
    group: "Health (today)",
    options: [
      ["metric:steps", "Steps"], ["metric:kcal", "Calories (kcal)"], ["metric:km", "Distance (km)"],
      ["metric:hr", "Heart rate (latest)"], ["metric:hr_resting", "Resting heart rate"], ["metric:hr_avg", "Average heart rate"],
      ["metric:sleep", "Sleep last night"], ["metric:stress", "Stress"], ["metric:spo2", "Blood oxygen"], ["metric:battery", "Battery"],
    ].map(([value, label]) => ({ value, label })),
  },
  {
    group: "Date",
    options: [
      ["date:%a %d %b", "Mon 26 Oct"], ["date:%A", "Monday"], ["date:%d", "26"], ["date:%b %d", "Oct 26"],
      ["date:%d.%m", "26.10"], ["date:%a", "Mon"],
    ].map(([value, label]) => ({ value, label })),
  },
  { group: "Other", options: [{ value: "insight", label: "Insight line" }] },
];

export const RING_METRICS = BINDINGS[0].options;

// A short list of Google fonts that read well on a small round screen; any family name can be typed.
export const GOOGLE_FONTS = [
  "Poiret One", "Josefin Sans", "Orbitron", "Rajdhani", "Oswald", "Bebas Neue", "Barlow Condensed", "Space Grotesk",
  "Space Mono", "JetBrains Mono", "IBM Plex Mono", "Chakra Petch", "Audiowide", "Michroma", "Syncopate", "Major Mono Display",
  "Inter", "Manrope", "DM Sans", "Outfit", "Sora", "Lexend", "Playfair Display", "Cormorant Garamond", "DM Serif Display",
  "Abril Fatface", "Righteous", "Monoton", "Pacifico", "Caveat",
];

export const layerTitle = (l: Layer): string => {
  if (l.type === "text") return l.bind ? (labelFor(l.bind) ?? l.bind) : `"${(l.text ?? "").slice(0, 18)}"`;
  if (l.type === "ring") return `Ring · ${labelFor(l.bind) ?? l.bind}`;
  if (l.type === "image") return "Image";
  return l.type === "circle" ? "Circle" : "Rectangle";
};

export const labelFor = (bind: string) =>
  BINDINGS.flatMap((g) => g.options).find((o) => o.value === bind)?.label ??
  (bind.startsWith("static:") ? `"${bind.slice(7)}"` : undefined);
