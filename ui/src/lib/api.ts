// watchd's localhost API. The bearer token arrives once in the URL fragment (`cmf ui` opens
// /ui/#token=…; fragments are never sent over the network), then lives in sessionStorage.

const KEY = "cmf-token";

function takeToken(): string | null {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) {
    try {
      sessionStorage.setItem(KEY, decodeURIComponent(m[1]));
    } catch {
      /* storage blocked: keep it in memory for this page only */
      memoryToken = decodeURIComponent(m[1]);
    }
    history.replaceState(null, "", location.pathname + location.search);
  }
  try {
    return sessionStorage.getItem(KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

let memoryToken: string | null = null;
const token = takeToken();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const hasToken = () => token !== null;

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${token ?? ""}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? res.statusText));
  }
  return data as T;
}

export const get = <T = any>(path: string) => api<T>("GET", path);
export const post = <T = any>(path: string, body?: unknown) => api<T>("POST", path, body);
export const put = <T = any>(path: string, body?: unknown) => api<T>("PUT", path, body);
