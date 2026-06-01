# Web Client Docker Setup

This directory contains a Dockerized static web application using nginx:alpine as the base image.

## Architecture

- **Base Image**: `nginx:alpine` - Lightweight nginx server
- **Static Files**: All HTML, CSS, JS files are served from `/usr/share/nginx/html`
- **SPA Support**: Nginx configured to serve `index.html` for client-side routing
- **Health Check**: Built-in health check using `wget --quiet --spider http://localhost`
- **Port**: Exposes port 80 internally (map to any external port)

## Files

- `Dockerfile` - Multi-stage build configuration
- `nginx.conf` - Custom nginx configuration for SPA support
- `.dockerignore` - Excludes unnecessary files from build context
- `docker-build.sh` - Convenience script for building and running

## Building the Image

```bash
# Build the image
docker build -t web_client:latest .

# Or use the convenience script
./docker-build.sh
```

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
- Cache headers for static assets (1 year cache)
- Separate handling for JS, CSS, images

### Security Headers
- `X-Frame-Options: SAMEORIGIN`
- `X-XSS-Protection: 1; mode=block`
- `X-Content-Type-Options: nosniff`

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

1. **Static Asset Caching**: Long-term caching for JS/CSS/images
2. **API Route Placeholder**: `/api/` location block for future backend integration
3. **SPA Fallback**: All non-matching routes serve `index.html`
4. **Health Endpoint**: `/health` returns simple health status

## Integration with Docker Compose

```yaml
version: '3.8'
services:
  web_client:
    build: ./web_client
    ports:
      - "3000:80"
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--spider", "http://localhost/health"]
      interval: 30s
      timeout: 3s
      retries: 3
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
Rebuild the image after making changes to static files:
```bash
docker build --no-cache -t web_client:latest .
```
