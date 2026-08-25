/**
 * Same-origin proxy in front of services/api (§3.3). The browser only ever
 * calls `/api/proxy/*` on its own origin; this Route Handler makes the real
 * server-to-server call to `API_BASE_URL`, read fresh from process.env on
 * every request (see app/api/config/route.ts for why that matters for
 * runtime repointing).
 *
 * This also happens to be the only thing that makes "point the browser at
 * the live API" *work* at all from the deployed web/ origin: ef-api-* does
 * not send Access-Control-Allow-Origin (verified — an OPTIONS preflight to
 * /cases gets a bare 405 with no CORS headers), so a direct
 * `fetch("https://ef-api-....run.app/...")` from client JS on a different
 * origin is blocked by the browser's CORS policy. Server-to-server requests
 * are not subject to CORS, so routing through here sidesteps the problem
 * without needing a CORS change on services/api. Flagged as a HANDOFF in the
 * PR in case SWARM/ATLAS want CORS on the API anyway for other consumers.
 */
export const dynamic = "force-dynamic";

async function forward(req: Request, path: string[]): Promise<Response> {
  const base = process.env.API_BASE_URL?.trim().replace(/\/+$/, "");
  if (!base) {
    return Response.json(
      { detail: "API_BASE_URL is not configured; mock mode is active" },
      { status: 503 }
    );
  }

  const incoming = new URL(req.url);
  const target = `${base}/${path.join("/")}${incoming.search}`;

  const init: RequestInit = {
    method: req.method,
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    const body = await req.text();
    if (body) init.body = body;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (e) {
    return Response.json(
      { detail: `upstream request failed: ${e instanceof Error ? e.message : "unknown error"}` },
      { status: 502 }
    );
  }

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}

type RouteParams = { params: { path: string[] } };

export async function GET(req: Request, { params }: RouteParams) {
  return forward(req, params.path);
}

export async function POST(req: Request, { params }: RouteParams) {
  return forward(req, params.path);
}
