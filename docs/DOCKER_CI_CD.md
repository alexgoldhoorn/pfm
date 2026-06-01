# Docker CI/CD Pipeline

This document describes the automated Docker build and deployment pipeline for the Portfolio Manager application.

## Overview

The CI/CD pipeline automatically builds and publishes Docker images for both the backend and web components when changes are pushed to the `main` branch. The pipeline supports:

- **Multi-platform builds**: `linux/amd64` and `linux/arm64`
- **Caching**: GitHub Actions cache to speed up builds
- **Testing**: Automated testing of built images
- **Registry**: Images are pushed to GitHub Container Registry (GHCR)

## Workflow Configuration

The pipeline is defined in `.github/workflows/docker-build-push.yml` and triggers on:

- Push to `main` branch (builds and pushes images)
- Pull requests to `main` (builds and tests images, but doesn't push)

## Image Naming Convention

Images are tagged with multiple tags for flexibility:

- `ghcr.io/yourusername/portfolio-backend:latest` - Latest stable version
- `ghcr.io/yourusername/portfolio-backend:main-abc123` - Commit-specific tag
- `ghcr.io/yourusername/portfolio-web:latest` - Latest web client version
- `ghcr.io/yourusername/portfolio-web:main-abc123` - Commit-specific tag

## Production Deployment

### Using docker-compose

The `docker-compose.yml` file supports environment variable-based image selection for production deployments:

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
PORTF_SECRET_KEY=your-production-secret-key
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
# Test backend
docker run --rm -p 8000:8000 \
  -e PORTF_DATABASE_URL="sqlite:///test.db" \
  -e PORTF_SECRET_KEY="test-key" \
  ghcr.io/yourusername/portfolio-backend:latest

# Test web client
docker run --rm -p 80:80 \
  ghcr.io/yourusername/portfolio-web:latest
```

## Security Considerations

- Images are built from the production `Dockerfile` which runs as non-root user
- Sensitive information is passed via environment variables, not baked into images
- Registry access uses GitHub's built-in token authentication
- Images are scanned for vulnerabilities as part of the build process

## Customization

### Adding Custom Build Steps

Modify `.github/workflows/docker-build-push.yml` to add:

- Security scanning
- Image signing
- Deployment notifications
- Integration tests

### Using Different Registries

Update the workflow to push to multiple registries or use private registries by modifying the `docker/login-action` and image tags.
