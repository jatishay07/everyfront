# Multi-stage build -> a small standalone Next.js image that listens on
# $PORT, per infra/deploy.sh (ATLAS persona 1). Cloud Run injects PORT at
# runtime; the Next.js standalone server reads it directly.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
ENV PORT=8080
EXPOSE 8080
# API_BASE_URL is read at REQUEST time by app/api/config and app/api/proxy
# (plain server env var, deliberately not NEXT_PUBLIC_* so it is never
# inlined into the client bundle at build time above). Unset/empty ->
# mock data layer. Set it — or change it — with
# `gcloud run services update ef-web --set-env-vars=API_BASE_URL=...`,
# which rolls a new revision of this same image in seconds; no rebuild
# needed to repoint the app or to fall back to mock mid-demo.
ENV API_BASE_URL=""
CMD ["node", "server.js"]
