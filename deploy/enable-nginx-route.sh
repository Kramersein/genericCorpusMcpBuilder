#!/bin/sh
# Adds `location /mcp` (proxy to the local service) to an nginx HTTPS server block. Called by install.sh;
# can also be run alone:  sudo sh deploy/enable-nginx-route.sh
# Settings: CORPUS_MCP_PORT (8765), CORPUS_MCP_LAN_CIDR (192.168.0.0/24), CORPUS_MCP_NGINX_CERT (ssl_certificate path
# identifying the server block; default: the first server block with an ssl_certificate).
set -eu
# Settings used to be called TORAH_MCP_*: those names still work.
for setting in HOME DATA_SRC PORT PUBLIC_HOST NGINX NGINX_CERT LAN_CIDR; do
    eval "[ -n \"\${CORPUS_MCP_$setting:-}\" ] || [ -z \"\${TORAH_MCP_$setting:-}\" ] || CORPUS_MCP_$setting=\"\$TORAH_MCP_$setting\""
done

BUNDLE=$(cd "$(dirname "$0")/.." && pwd)
PORT=${CORPUS_MCP_PORT:-8765}
LAN_CIDR=${CORPUS_MCP_LAN_CIDR:-192.168.0.0/24}
CERT=${CORPUS_MCP_NGINX_CERT:-}
SNIPPET=/etc/nginx/snippets/corpus-mcp.conf
INCLUDE_LINE="    include snippets/corpus-mcp.conf;"

install -d /etc/nginx/snippets
sed -e "s#@PORT@#$PORT#g" -e "s#@LAN_CIDR@#$LAN_CIDR#g" "$BUNDLE/deploy/nginx-corpus-mcp.conf" > "$SNIPPET"
chmod 644 "$SNIPPET"

# a server-block line naming the certificate: "ssl_certificate <CERT>;" or any ssl_certificate if CERT is empty
IS_CERT_LINE='$1 == "ssl_certificate" && (cert == "" || $2 == cert ";")'
SITE=$(nginx -T 2>/dev/null | awk -v cert="$CERT" \
    "/^# configuration file /{file = \$4; sub(/:\$/, \"\", file)} $IS_CERT_LINE {print file; exit}" || true)

if [ -z "$SITE" ]; then
    echo "no nginx HTTPS server block found; add this inside your HTTPS server block and reload nginx:"
    echo "$INCLUDE_LINE"
elif grep -q "snippets/torahMcp.conf" "$SITE"; then       # included under the project's old name: rename it
    SITE=$(readlink -f "$SITE")
    cp "$SITE" "$SITE.before-corpus-mcp"
    sed -i "s#snippets/torahMcp.conf#snippets/corpus-mcp.conf#" "$SITE"
    rm -f /etc/nginx/snippets/torahMcp.conf
    if nginx -t; then
        systemctl reload nginx
        echo "nginx: renamed the torahMcp include in $SITE (backup: $SITE.before-corpus-mcp)"
    else
        mv "$SITE.before-corpus-mcp" "$SITE"
        echo "nginx test failed; restored $SITE"
    fi
elif grep -q "snippets/corpus-mcp.conf" "$SITE"; then
    echo "nginx include already present in $SITE"
    nginx -t && systemctl reload nginx
else
    SITE=$(readlink -f "$SITE")
    cp "$SITE" "$SITE.before-genericCorpusMcpBuilder"
    LINE=$(awk -v cert="$CERT" "$IS_CERT_LINE {print NR; exit}" "$SITE")
    sed -i "${LINE}a\\$INCLUDE_LINE" "$SITE"
    if nginx -t; then
        systemctl reload nginx
        echo "nginx: added include to $SITE (backup: $SITE.before-genericCorpusMcpBuilder)"
    else
        mv "$SITE.before-genericCorpusMcpBuilder" "$SITE"
        echo "nginx test failed; restored $SITE. Add the include line by hand:"
        echo "$INCLUDE_LINE"
    fi
fi
