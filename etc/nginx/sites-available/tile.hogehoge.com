proxy_cache_path /var/cache/nginx/tiles
    levels=1:2
    keys_zone=tiles:100m
    max_size=20g
    inactive=7d
    use_temp_path=off;

# CORS preflight handling (OPTIONS = 1)
map $request_method $cors_preflight {
    OPTIONS 1;
    default 0;
}

# HTTP -> HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name tile.hogehoge.com;

    location /.well-known/acme-challenge/ {
        root /home/shimotsuki/http/tile.hogehoge.com;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS reverse proxy in front of tileserver-gl
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name tile.hogehoge.com;

    ssl_certificate     /etc/letsencrypt/live/tile.hogehoge.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tile.hogehoge.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options    "nosniff"          always;
    add_header Referrer-Policy           "no-referrer"      always;

    access_log /var/log/nginx/tile.hogehoge.com.access.log;
    error_log  /var/log/nginx/tile.hogehoge.com.error.log;

    client_max_body_size 10m;

    # Tiles, styles, sprites, fonts (cacheable)
    location ~* \.(pbf|mvt|png|jpg|jpeg|webp|json)$ {
        # CORS for browser MapLibre access
        add_header Access-Control-Allow-Origin  "*" always;
        add_header Access-Control-Allow-Methods "GET, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Range, If-None-Match" always;
        add_header Access-Control-Expose-Headers "ETag, Content-Length" always;
        if ($cors_preflight) { return 204; }

        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;

        proxy_cache            tiles;
        proxy_cache_valid      200 7d;
        proxy_cache_valid      404 10m;
        proxy_cache_use_stale  error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_lock       on;
        add_header X-Cache-Status $upstream_cache_status always;

        expires 7d;
    }

    # Demo page (served directly by nginx, not proxied)
    location = /demo.html {
        root /home/shimotsuki/http/tile.hogehoge.com;
    }

    # Everything else (admin UI, etc.)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    }
}
