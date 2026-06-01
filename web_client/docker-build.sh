#!/bin/bash

# Build and run script for web_client Docker image

set -e

IMAGE_NAME="web_client"
IMAGE_TAG="latest"
CONTAINER_NAME="web_client_container"
PORT="8080"

echo "🏗️  Building web_client Docker image..."
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo "🚀 Starting web_client container..."
docker run -d \
  --name ${CONTAINER_NAME} \
  -p ${PORT}:80 \
  ${IMAGE_NAME}:${IMAGE_TAG}

echo "✅ Web client is running!"
echo "📱 Access the application at: http://localhost:${PORT}"
echo "🏥 Health check endpoint: http://localhost:${PORT}/health"

echo ""
echo "📋 Useful commands:"
echo "  - View logs: docker logs ${CONTAINER_NAME}"
echo "  - Stop container: docker stop ${CONTAINER_NAME}"
echo "  - Remove container: docker rm ${CONTAINER_NAME}"
echo "  - Check health: docker inspect ${CONTAINER_NAME} | grep Health -A 10"
