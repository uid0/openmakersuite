FROM nginx:1.31-alpine

RUN apk add --no-cache certbot openssl && \
    mkdir -p /var/www/certbot /etc/nginx/templates /docker-entrypoint.d

COPY nginx.conf /etc/nginx/nginx.conf
COPY templates/ /etc/nginx/templates/
COPY docker-entrypoint.d/ /docker-entrypoint.d/

RUN chmod +x /docker-entrypoint.d/*.sh
