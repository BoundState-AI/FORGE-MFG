# Module:   Frontend Dockerfile
# Purpose:  Multi-stage build — npm build then nginx serve.
# Rev:      0.1.0
#
# BoundState Proprietary
# Co-Authored-By: Dr. Joseph Wilde, BoundState AI
# Co-Authored-By: Claude "Beth" (Opus 4.6)
#
# Why multi-stage: Build stage installs deps and runs Vite build.
# Serve stage copies only the dist/ output into an nginx image.
# This keeps the production image small and dependency-free.

# ── Build stage ──
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# ── Serve stage ──
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
