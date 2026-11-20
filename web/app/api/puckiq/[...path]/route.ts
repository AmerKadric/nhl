import { API_BASE } from "@/lib/api";

/** Narrow same-origin proxy for the few endpoints the browser calls directly
 * (live refresh and the scenario lab). Keeps the API address server-side and
 * forwards nothing but an allow-listed path, query string and JSON body. */
const GET_ALLOWED = [/^v1\/games$/, /^v1\/live$/];
const POST_ALLOWED = [/^v1\/games\/\d{6,12}\/lab$/];

type Ctx = { params: Promise<{ path: string[] }> };

async function forward(path: string, init: RequestInit) {
  try {
    const r = await fetch(`${API_BASE}/${path}`, { ...init, cache: "no-store", signal: AbortSignal.timeout(20000) });
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
  } catch {
    return Response.json({ detail: "Prediction service unavailable." }, { status: 502 });
  }
}

export async function GET(req: Request, ctx: Ctx) {
  const path = (await ctx.params).path.join("/");
  if (!GET_ALLOWED.some((re) => re.test(path))) return Response.json({ detail: "Not found." }, { status: 404 });
  const qs = new URL(req.url).searchParams;
  const clean = new URLSearchParams();
  for (const k of ["date", "start", "end"]) {
    const v = qs.get(k);
    if (v && /^\d{4}-\d{2}-\d{2}$/.test(v)) clean.set(k, v);
  }
  const q = clean.toString();
  return forward(q ? `${path}?${q}` : path, { headers: clientHeaders(req) });
}

export async function POST(req: Request, ctx: Ctx) {
  const path = (await ctx.params).path.join("/");
  if (!POST_ALLOWED.some((re) => re.test(path))) return Response.json({ detail: "Not found." }, { status: 404 });
  const body = await req.text();
  if (body.length > 2000) return Response.json({ detail: "Request too large." }, { status: 413 });
  return forward(path, { method: "POST", body, headers: { "content-type": "application/json", ...clientHeaders(req) } });
}

function clientHeaders(req: Request): Record<string, string> {
  // pass the caller's address through so API rate limits apply per visitor
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return ip ? { "x-forwarded-for": ip } : {};
}
