#!/usr/bin/env bash
# ============================================================
# Houdini Agent 主页 — 服务器一键初始化（在服务器上以 root 运行）
# 用法：
#   1) 改下面的 DOMAIN 为你的真实域名
#   2) sudo bash server-setup.sh
# 作用：装 Nginx、建目录、写站点配置、开机自启
# 注意：本脚本不上传网页文件和证书，按 DEPLOY.md 第 4、5 步操作
# ============================================================
set -euo pipefail

# >>>>>>>>>>>>>>>>>> 你的域名（已填好） <<<<<<<<<<<<<<<<<<
DOMAIN="houdini-agent.com"
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

WEBROOT="/var/www/houdini-agent"
SSL_DIR="/etc/nginx/ssl"
SITE="/etc/nginx/sites-available/houdini-agent.conf"

if [ "$DOMAIN" = "example.com" ]; then
  echo "✗ 请先把脚本里的 DOMAIN 改成你的真实域名再运行。"; exit 1
fi

echo "==> 安装 Nginx"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx

echo "==> 创建目录"
mkdir -p "$WEBROOT" "$SSL_DIR"

echo "==> 写入站点配置（域名：$DOMAIN）"
cat > "$SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN} www.${DOMAIN};

    ssl_certificate     ${SSL_DIR}/${DOMAIN}.crt;
    ssl_certificate_key ${SSL_DIR}/${DOMAIN}.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    root  ${WEBROOT};
    index index.html;

    add_header X-Content-Type-Options nosniff           always;
    add_header X-Frame-Options        SAMEORIGIN         always;
    add_header Referrer-Policy        strict-origin-when-cross-origin always;

    gzip on;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml application/xml;

    location ~* \.(?:css|js|png|jpe?g|gif|webp|svg|woff2?|ttf|ico)\$ {
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
        access_log off;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    # location /api/ {
    #     proxy_pass http://127.0.0.1:8000;
    #     proxy_http_version 1.1;
    #     proxy_set_header Host              \$host;
    #     proxy_set_header X-Real-IP         \$remote_addr;
    #     proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    #     proxy_set_header X-Forwarded-Proto \$scheme;
    # }
}
NGINX

echo "==> 启用站点"
ln -sf "$SITE" /etc/nginx/sites-enabled/houdini-agent.conf
rm -f /etc/nginx/sites-enabled/default

echo "==> 放一个占位页（证书和正式文件就位后会被覆盖）"
if [ ! -f "$WEBROOT/index.html" ]; then
  echo "<h1>Houdini Agent — coming soon</h1>" > "$WEBROOT/index.html"
fi
chown -R www-data:www-data "$WEBROOT"

echo
echo "==> 已就绪。接下来还差两步（见 DEPLOY.md）："
echo "    1. 上传证书到：$SSL_DIR/${DOMAIN}.crt 和 ${SSL_DIR}/${DOMAIN}.key"
echo "    2. 上传网页文件到：$WEBROOT/"
echo "    完成后执行：nginx -t && systemctl reload nginx"
echo
echo "证书就位前先不要 reload（会因找不到证书报错）。"
