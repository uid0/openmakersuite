FROM node:24-alpine

WORKDIR /app

# Copy package files (npmrc carries legacy-peer-deps for eslint v10 +
# eslint-plugin-react@7.x peer mismatch — needs to land before npm ci).
COPY package*.json .npmrc ./

# Install dependencies using npm ci for reproducible builds
# Fall back to npm install if lock file is missing (shouldn't happen in production)
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy application code
COPY . .

EXPOSE 3000

CMD ["npm", "start"]
