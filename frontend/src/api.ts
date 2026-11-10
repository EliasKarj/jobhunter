import { track } from "./inflight";

// Backendin kutsut. Muuttavat pyynnöt vaativat X-Requested-With-otsikon (backendin CSRF-suoja).

export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string = "error") {
    super(message);
  }
}

async function handle<T>(r: Response): Promise<T> {
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : null;
  if (!r.ok) {
    throw new ApiError(body?.error || `Palvelin vastasi ${r.status}`, r.status, body?.code);
  }
  return body as T;
}

export async function get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const q = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]))
    : "";
  try {
    return await track(fetch(path + q).then((r) => handle<T>(r)));
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("Palvelimeen ei saada yhteyttä. Onko jobhunter käynnissä?", 0, "network");
  }
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  try {
    return await track(fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "jobhunter" },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)));
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("Palvelimeen ei saada yhteyttä. Onko jobhunter käynnissä?", 0, "network");
  }
}

/** Server-Sent Events POST-pyynnöstä (EventSource tukee vain GETiä). */
export function stream(path: string, body: unknown, onEvent: (ev: any) => void, signal?: AbortSignal) {
  return track(streamInner(path, body, onEvent, signal));
}

async function streamInner(path: string, body: unknown, onEvent: (ev: any) => void, signal?: AbortSignal) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Requested-With": "jobhunter" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) {
    let msg = `Palvelin vastasi ${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* ei JSONia */ }
    throw new ApiError(msg, r.status);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}
