import "server-only";

const BASE = (process.env.PUCKIQ_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiUnavailable extends Error {
  constructor(public status: number | null, path: string) {
    super(`PUCKIQ API unavailable (${status ?? "network"}) for ${path}`);
  }
}

/** Server-side fetch from the prediction API. Returns null on 404; throws
 * ApiUnavailable on any other failure so the error boundary can say so
 * instead of rendering stale or empty content as if it were real. */
export async function api<T>(path: string): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { cache: "no-store", signal: AbortSignal.timeout(15000) });
  } catch {
    throw new ApiUnavailable(null, path);
  }
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiUnavailable(res.status, path);
  return (await res.json()) as T;
}

export const API_BASE = BASE;
