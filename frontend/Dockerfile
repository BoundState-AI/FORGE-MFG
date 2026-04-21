# Module:   Frontend Dockerfile
# Purpose:  Multi-stage build — npm build then nginx serve.  Both
#           stages apply current Alpine security patches so the image
#           does not ship with whatever CVEs the base tag carried on
#           its build day; the staging Trivy CRITICAL/HIGH gate relies
#           on the final image being current.
# Rev:      0.2.0
#
# BoundState Proprietary
# Co-Authored-By: Dr. Joseph Wilde, BoundState AI
# Co-Authored-By: Claude "Beth" (Opus 4.7)
#
# Why multi-stage: Build stage installs deps and runs Vite build.
# Serve stage copies only the dist/ output into an nginx image.
# This keeps the production image small and dependency-free.

# ── Build stage ──
FROM node:20-alpine AS build
WORKDIR /app
# Pull current Alpine package security patches before npm ci so the
# build toolchain (node_modules' native deps, openssl linkage) is
# current; this doesn't affect the runtime image but does keep the
# build stage from triggering its own separate CVE noise.
RUN apk upgrade --no-cache
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# ── Serve stage ──
FROM nginx:1.27-alpine
# `apk upgrade` pulls current Alpine security patches for whatever
# the nginx:1.27-alpine tag shipped with.  Equivalent in intent to
# apt-get upgrade on the Debian-based API image — the scan gate
# needs to see a patched base, not a month-stale one.
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
