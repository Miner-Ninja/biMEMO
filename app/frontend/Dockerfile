# Frontend Dockerfile for Vite dev server
FROM node:24-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Update npm to latest version
RUN npm install -g npm@11.7.0

# Install dependencies
RUN npm ci

# Copy source code (will be overridden by volume mount in dev)
COPY . .

# Expose Vite dev server port
EXPOSE 5173

# Default command (overridden in docker-compose.yml)
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
