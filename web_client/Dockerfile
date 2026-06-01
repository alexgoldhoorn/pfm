# Use nginx:alpine as base image for serving static files
FROM nginx:alpine

# Copy web_client content to nginx html directory
COPY . /usr/share/nginx/html

# Copy nginx configuration for SPA fallback to index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Health check using wget
RUN apk add --no-cache wget
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --spider http://localhost || exit 1

# nginx runs as daemon off by default in the base image
CMD ["nginx", "-g", "daemon off;"]
