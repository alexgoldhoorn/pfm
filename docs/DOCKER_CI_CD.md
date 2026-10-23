# Docker CI/CD Pipeline

This document describes the automated Docker build and deployment pipeline for the Portfolio Manager application.

## Overview

The pipeline builds and publishes Docker images for both the backend and web components when a version tag is pushed (or on manual dispatch); ordinary pushes to `main` run only `ci.yml` (lint, unit tests, JS tests). The pipeline supports:

- **Multi-platform builds**: `linux/amd64` and `linux/arm64`
- **Caching**: GitHub Actions cache to speed up builds
- **Testing**: Automated testing of built images
- **Registry**: Images are pushed to GitHub Container Registry (GHCR)

## Workflow Configuration

The pipeline is defined in `.github/workflows/docker-build-push.yml` and triggers on:

- Push of a tag matching `v*` (e.g. `v1.0.0`) — builds and pushes images
- Manual `workflow_dispatch`

It does not run on pushes or pull requests to `main`, so the "Run tests on
... image" steps inside it, which are conditioned on `pull_request`, are never
reached.

## Image Naming Convention

The workflow names the images `ghcr.io/<owner>/<repo>/portfolio-backend` and
`ghcr.io/<owner>/<repo>/portfolio-web` (`${{ github.repository }}/...`), and
`docker/metadata-action` derives the tags (`latest` on the default branch,
branch/PR refs, `<branch>-<sha>`). `docker-compose.yml` defaults to
`ghcr.io/yourusername/portfolio-backend:${IMAGE_TAG}` and
`ghcr.io/yourusername/portfolio-web:${IMAGE_TAG}`, so set `BACKEND_IMAGE` /
`WEB_IMAGE` (or `IMAGE_TAG`) to whatever the registry actually holds, e.g.:

- `ghcr.io/yourusername/portfolio-backend:latest`
- `ghcr.io/yourusername/portfolio-backend:main-abc123`
- `ghcr.io/yourusername/portfolio-web:latest`
- `ghcr.io/yourusername/portfolio-web:main-abc123`

## Production Deployment

### Using docker-compose

The `docker-compose.yml` file supports environment variable-based image selection for production deployments. The compose file uses the external network `portf_net`, so create it once (`docker network create portf_net`; `scripts/deploy-prod.sh` does this for you):

```bash
# Deploy using latest images
export IMAGE_TAG=latest
docker-compose --profile prod up -d

# Deploy using specific commit
export IMAGE_TAG=main-abc123def
docker-compose --profile prod up -d

# Deploy using custom images
export BACKEND_IMAGE=your-registry.com/portfolio-backend:v1.0.0
export WEB_IMAGE=your-registry.com/portfolio-web:v1.0.0
docker-compose --profile prod up -d
```

### Environment Variables

Configure these variables in your `.env.production` file:

```bash
# Image tag to deploy
IMAGE_TAG=latest

# Optional: Override default image names
BACKEND_IMAGE=ghcr.io/yourusername/portfolio-backend:${IMAGE_TAG}
WEB_IMAGE=ghcr.io/yourusername/portfolio-web:${IMAGE_TAG}

# Database configuration
PORTF_DATABASE_URL=postgresql://user:pass@postgres:5432/portf_db
# At least 32 characters: the production image refuses to start otherwise
PORTF_SECRET_KEY=your-production-secret-key-at-least-32-chars
```

## Registry Authentication

### GitHub Container Registry (GHCR)

For private repositories, authenticate with GHCR:

```bash
# Login to GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Pull private images
docker pull ghcr.io/yourusername/portfolio-backend:latest
```

### Alternative: Docker Hub

To use Docker Hub instead of GHCR, update the workflow:

1. Set repository secrets:
   - `DOCKER_USERNAME`: Your Docker Hub username
   - `DOCKER_PASSWORD`: Your Docker Hub password or access token

2. Update the registry URL in `.github/workflows/docker-build-push.yml`:
   ```yaml
   env:
     REGISTRY: docker.io
     IMAGE_NAME_BACKEND: yourusername/portfolio-backend
     IMAGE_NAME_WEB: yourusername/portfolio-web
   ```

## Manual Image Building

Build images locally for testing:

```bash
# Build backend image with buildx
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag portfolio-backend:local \
  --build-arg IMAGE_TAG=local \
  .

# Build web image
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag portfolio-web:local \
  --build-arg IMAGE_TAG=local \
  ./web_client/
```

## Monitoring and Troubleshooting

### Viewing Build Status

Check the Actions tab in your GitHub repository to monitor build progress and view logs.

### Common Issues

1. **Authentication failures**: Ensure `GITHUB_TOKEN` has proper permissions
2. **Multi-platform build failures**: Check that base images support both architectures
3. **Cache issues**: Clear GitHub Actions cache if builds fail unexpectedly

### Testing Images Locally

Test the built images before deployment:

```bash
# Test backend (the image bakes PORTF_ENVIRONMENT=production, so the secret
# must not be the placeholder and must be at least 32 characters long;
# the health endpoint is /health)
docker run --rm -p 8000:8000 \
  -e PORTF_DATABASE_URL="sqlite:///test.db" \
  -e PORTF_SECRET_KEY="local-test-secret-key-0123456789abcdef" \
  ghcr.io/yourusername/portfolio-backend:latest
curl -f http://localhost:8000/health

# Test web client
docker run --rm -p 80:80 \
  ghcr.io/yourusername/portfolio-web:latest
```

## Security Considerations

- Images are built from the production `Dockerfile` which runs as non-root user
- Sensitive information is passed via environment variables, not baked into images
- Registry access uses GitHub's built-in token authentication
- No vulnerability scanning step is configured in the workflow

## Customization

### Adding Custom Build Steps

Modify `.github/workflows/docker-build-push.yml` to add:

- Security scanning
- Image signing
- Deployment notifications
- Integration tests

### Using Different Registries

Update the workflow to push to multiple registries or use private registries by modifying the `docker/login-action` and image tags.
