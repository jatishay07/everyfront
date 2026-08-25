/**
 * Tells the client whether to run against the live services/api or the
 * in-memory mock. Read `API_BASE_URL` here — a plain server-side env var,
 * NOT `NEXT_PUBLIC_*` — so the decision is made per-request inside the
 * running container, not baked into the client bundle at `next build` time.
 *
 * Why this matters (CANVAS WO-live-api, task item 2): Cloud Run lets you
 * change a service's env vars and roll a new revision in seconds, reusing
 * the exact same image. If the live API base URL were a `NEXT_PUBLIC_*`
 * build-arg (the previous design — see git history on Dockerfile), flipping
 * back to mock mode mid-demo would require a full rebuild. Reading it here
 * means `gcloud run services update ef-web --set-env-vars=API_BASE_URL=...`
 * (or clearing it) is enough, with no image rebuild — the §6 risk register's
 * "demo-day failure is a live risk" deserves that fast a lever.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const apiBase = process.env.API_BASE_URL?.trim();
  return Response.json({ usingMock: !apiBase });
}
