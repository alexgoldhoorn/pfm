# Web Client Docker Setup

This directory contains a Dockerized static web application using nginx:alpine as the base image.

## Architecture

- **Base Image**: `nginx:alpine` - Lightweight nginx server
- **Static Files**: All HTML, CSS, JS files are served from `/usr/share/nginx/html`
- **SPA Support**: Nginx configured to serve `index.html` for client-side routing
- **Health Check**: Built-in health check using `wget --quiet --spider http://localhost`
- **Port**: Exposes port 80 internally (map to any external port)
- **API proxy**: `/api/` is proxied to the backend container, so the browser uses one origin

## Files

- `Dockerfile` - Single-stage build on `nginx:alpine` (copies the files, installs `wget` for the health check)
- `nginx.conf` - Custom nginx configuration for SPA support, the `/api/` proxy and security headers
- `nginx.demo.conf` - Variant mounted by `docker-compose.demo.yml` (proxies to `portf_demo_backend:8000`)
- `.dockerignore` - Excludes unnecessary files from build context
- `docker-build.sh` - Convenience script for building and running

## Building the Image

```bash
# Build the image
docker build -t web_client:latest .

# Or use the convenience script (builds, then runs a container named
# web_client_container on port 8080)
./docker-build.sh
```

In the project itself the image is built and run through Docker Compose (see below), not by hand.

## Running the Container

```bash
# Run on port 8080
docker run -d --name web_client -p 8080:80 web_client:latest

# Run on port 3000
docker run -d --name web_client -p 3000:80 web_client:latest
```

## Features

### SPA (Single Page Application) Support
The nginx configuration includes fallback routing to serve `index.html` for all non-static file requests, enabling client-side routing to work properly.

### Performance Optimizations
- Gzip compression enabled for text files
- Images and fonts cached for 1 year (`Cache-Control: public, immutable`)
- JS and CSS served with `Cache-Control: no-cache` so a deploy reaches the browser (ETag revalidation)
- `client_max_body_size 25m` so a large broker statement (e.g. a full Mintos export) can be uploaded

### Security Headers
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Content-Security-Policy`: `'self'` plus `https://cdn.jsdelivr.net` for scripts, styles and fonts (Bootstrap, Chart.js, Marked), with inline scripts/styles allowed for the static app shell

### Health Check
- **Endpoint**: `http://localhost/health`
- **Docker Health**: Built-in health check every 30 seconds
- **Command**: `wget --quiet --spider http://localhost`

## Useful Commands

```bash
# Check container health
docker inspect web_client | grep Health -A 10

# View nginx access logs
docker logs web_client

# Access container shell
docker exec -it web_client /bin/sh

# Stop and remove container
docker stop web_client && docker rm web_client

# Test health endpoint
curl http://localhost:8080/health
```

## Nginx Configuration Details

The custom nginx configuration (`nginx.conf`) provides:

1. **Static Asset Caching**: long-term caching for images/fonts, revalidation for JS/CSS
2. **API Proxy**: the `/api/` location proxies to `portf_backend_dev:8000`, resolving the hostname per request through Docker's DNS (`resolver 127.0.0.11`) so nginx starts even when the backend container is not up yet; `proxy_read_timeout 200s`
3. **SPA Fallback**: All non-matching routes serve `index.html`
4. **Health Endpoint**: `/health` returns simple health status

## Integration with Docker Compose

The project's `docker-compose.yml` defines the service as `web` (container `portf_web`,
profiles `dev` and `prod`, on the external `portf_net` network shared with the backend):

```yaml
services:
  web:
    build:
      context: ./web_client
      dockerfile: Dockerfile
    container_name: portf_web
    ports:
      - "${WEB_PORT:-80}:80"
    networks:
      - portf_net
    healthcheck:
      test: ["CMD-SHELL", "wget --quiet --spider http://localhost || exit 1"]
      interval: 30s
      timeout: 3s
      start_period: 5s
      retries: 3
```

Rebuild and redeploy after editing any web file:

```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

## Development vs Production

This configuration is suitable for both development and production:

- **Development**: Quick rebuilds, local port mapping
- **Production**: Add reverse proxy (e.g., Traefik, nginx), SSL termination, CDN for static assets

## Troubleshooting

### Container won't start
```bash
docker logs web_client
```

### Health check failing
```bash
docker exec web_client wget --spider http://localhost/health
```

### Files not updating
The files are baked into the image, not mounted. Rebuild after making changes to static files:
```bash
docker compose build web
# or, standalone:
docker build --no-cache -t web_client:latest .
```
