FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies using npm ci for reproducible builds
# Fall back to npm install if lock file is missing (shouldn't happen in production)
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy application code
COPY . .

EXPOSE 3000

CMD ["npm", "start"]
