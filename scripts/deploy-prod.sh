#!/bin/bash

# Portfolio Manager Production Deployment Script
# This script demonstrates how to deploy using specific image tags

set -e

# Configuration
COMPOSE_FILE="docker-compose.yml"
PROFILE="prod"

# Function to display usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -t, --tag TAG     Specify image tag (default: latest)"
    echo "  -h, --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                          # Deploy with latest tags"
    echo "  $0 -t v1.0.0               # Deploy specific version"
    echo "  $0 -t main-abc123          # Deploy specific commit"
    echo ""
}

# Default values
IMAGE_TAG="latest"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo "=== Portfolio Manager Production Deployment ==="
echo "Image Tag: $IMAGE_TAG"
echo "Profile: $PROFILE"
echo ""

# Set environment variables for docker-compose
export IMAGE_TAG="$IMAGE_TAG"

# Verify environment file exists
if [ ! -f ".env.production" ]; then
    echo "Warning: .env.production not found. Using .env file."
    if [ ! -f ".env" ]; then
        echo "Error: No environment file found. Please create .env or .env.production"
        exit 1
    fi
fi

# Create network if it doesn't exist
echo "Creating Docker network if not exists..."
docker network create portf_net 2>/dev/null || true

# Pull latest images
echo "Pulling Docker images..."
docker-compose --profile $PROFILE pull --quiet

# Stop existing containers
echo "Stopping existing containers..."
docker-compose --profile $PROFILE down

# Start services
echo "Starting services..."
docker-compose --profile $PROFILE up -d

# Wait for health checks
echo "Waiting for services to be healthy..."
sleep 10

# Check service status
echo ""
echo "=== Service Status ==="
docker-compose --profile $PROFILE ps

# Test endpoints
echo ""
echo "=== Health Checks ==="

# Test backend health
if curl -sf http://localhost:8000/healthz >/dev/null 2>&1; then
    echo "✓ Backend is healthy"
else
    echo "✗ Backend health check failed"
fi

# Test web client
if curl -sf http://localhost/ >/dev/null 2>&1; then
    echo "✓ Web client is healthy"
else
    echo "✗ Web client health check failed"
fi

echo ""
echo "=== Deployment Complete ==="
echo "Backend: http://localhost:8000"
echo "Web Client: http://localhost"
echo ""
echo "To view logs: docker-compose --profile $PROFILE logs -f"
echo "To stop: docker-compose --profile $PROFILE down"
