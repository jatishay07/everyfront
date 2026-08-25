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
# NEXT_PUBLIC_API_BASE_URL is baked in at build time (Next.js inlines
# NEXT_PUBLIC_* vars) — pass it as a build-arg once services/api has a
# stable URL. Empty/unset keeps the mock data layer (lib/api.ts).
ARG NEXT_PUBLIC_API_BASE_URL=""
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
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
CMD ["node", "server.js"]
