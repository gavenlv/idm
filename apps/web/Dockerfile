# IDM Web Dockerfile
# Build: docker build -t idm-web:dev -f apps/web/Dockerfile .
# Run:   docker run -p 5173:80 idm-web:dev

# === 1. Builder ===
FROM node:20-alpine AS builder

WORKDIR /app
COPY apps/web/package*.json ./
RUN npm ci

COPY apps/web/ ./
RUN npm run build

# === 2. Runtime (nginx) ===
FROM nginx:1.27-alpine AS runtime

# nginx config
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O - http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
