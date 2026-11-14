#!/bin/sh
# Installs genericCorpusMcpBuilder as a systemd service (Linux, x86_64). Run from a checkout that has a built data directory:
#
#   sudo sh deploy/install.sh
#
# Settings (environment variables, all optional):
#   CORPUS_MCP_HOME         install root; app goes to $CORPUS_MCP_HOME/app, data to $CORPUS_MCP_HOME/data
#                          (default /opt/corpus-mcp)
#   CORPUS_MCP_DATA_SRC     built data directory to copy from (default: ./data in this checkout).
#                          Set it to $CORPUS_MCP_HOME/data to keep data already in place.
#   CORPUS_MCP_PORT         local port the service listens on (default 8765)
#   CORPUS_MCP_PUBLIC_HOST  host name clients use to reach the server via nginx (default: `hostname`.local)
#   CORPUS_MCP_NGINX        "auto" (default): add `location /mcp` to an nginx HTTPS server block if nginx is
#                          installed; "no": skip nginx
#   CORPUS_MCP_NGINX_CERT   ssl_certificate path that identifies the server block to extend (default: the first
#                          server block that has an ssl_certificate)
#   CORPUS_MCP_LAN_CIDR     network allowed through nginx (default 192.168.0.0/24)
#   UV                     path to uv (default: uv on PATH, else .tools/uv in this checkout)
set -eu
# Settings used to be called TORAH_MCP_*: those names still work.
for setting in HOME DATA_SRC PORT PUBLIC_HOST NGINX NGINX_CERT LAN_CIDR; do
    eval "[ -n \"\${CORPUS_MCP_$setting:-}\" ] || [ -z \"\${TORAH_MCP_$setting:-}\" ] || CORPUS_MCP_$setting=\"\$TORAH_MCP_$setting\""
done

BUNDLE=$(cd "$(dirname "$0")/.." && pwd)
BASE=${CORPUS_MCP_HOME:-/opt/corpus-mcp}
APP=$BASE/app
DATA=$BASE/data
DATA_SRC=${CORPUS_MCP_DATA_SRC:-$BUNDLE/data}
PORT=${CORPUS_MCP_PORT:-8765}
PUBLIC_HOST=${CORPUS_MCP_PUBLIC_HOST:-$(hostname).local}
WORK=$BASE/install-tmp
APP_ROOM_BYTES=4000000000

UV=${UV:-$(command -v uv || true)}
[ -z "$UV" ] && [ -x "$BUNDLE/.tools/uv" ] && UV=$BUNDLE/.tools/uv
if [ -z "$UV" ]; then
    echo "uv not found: install it (https://docs.astral.sh/uv/) or set UV=/path/to/uv"
    exit 1
fi

if [ ! -f "$DATA_SRC/corpus.db" ]; then
    echo "no corpus.db in $DATA_SRC; build it first (corpus-mcp build) or set CORPUS_MCP_DATA_SRC"
    exit 1
fi

if id corpusmcp >/dev/null 2>&1; then
    [ "$(getent passwd corpusmcp | cut -d: -f6)" = "$BASE" ] || usermod --home "$BASE" corpusmcp
else
    useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin corpusmcp
fi
install -d -m 755 -o corpusmcp -g corpusmcp "$BASE"
install -d -m 755 "$APP" "$DATA" "$DATA/hf" "$WORK"

SAME_DATA=no
[ "$(readlink -f "$DATA_SRC")" = "$(readlink -f "$DATA")" ] && SAME_DATA=yes

needed=$APP_ROOM_BYTES
[ "$SAME_DATA" = no ] && needed=$(( $(du -sb --exclude=raw "$DATA_SRC" | cut -f1) + APP_ROOM_BYTES ))
available=$(df -PB1 "$BASE" | awk 'NR == 2 {print $4}')
if [ "$available" -lt "$needed" ]; then
    echo "not enough space on the filesystem holding $BASE: need $((needed / 1000000000)) GB, have $((available / 1000000000)) GB"
    df -h "$BASE"
    exit 1
fi
echo "space check ok: $((available / 1000000000)) GB free on $(df -P "$BASE" | awk 'NR == 2 {print $6}')"

replace_data() {
    rm -rf "$DATA/$1.incoming" "$DATA/$1.previous"
    cp -r "$DATA_SRC/$1" "$DATA/$1.incoming"
    [ -e "$DATA/$1" ] && mv "$DATA/$1" "$DATA/$1.previous"
    mv "$DATA/$1.incoming" "$DATA/$1"
}

if [ "$SAME_DATA" = no ]; then
    echo "copying index data from $DATA_SRC (several GB, takes a while)..."
    for item in corpus.db precomputed.db vectors hf; do
        if [ -e "$DATA_SRC/$item" ]; then
            replace_data "$item"
        fi
    done
fi

rm -rf "$APP/genericCorpusMcpBuilder"
cp -r "$BUNDLE/genericCorpusMcpBuilder" "$BUNDLE/pyproject.toml" "$BUNDLE/uv.lock" "$BUNDLE/README.md" "$APP/"
rm -rf "$APP/genericCorpusMcpBuilder/__pycache__"
install -m 755 "$UV" "$APP/uv"
echo "installing Python and dependencies (downloads about 1 GB once)..."
cd "$APP"
TMPDIR="$WORK" UV_PYTHON_INSTALL_DIR="$APP/.python" UV_CACHE_DIR="$WORK/uv-cache" \
    ./uv sync --frozen --python 3.11 --extra semantic --no-dev
rm -rf "$WORK"
chown -R corpusmcp:corpusmcp "$BASE"

if [ -f /etc/systemd/system/torahMcp.service ]; then     # the service's name before the project was renamed
    echo "replacing the old torahMcp.service with corpus-mcp.service"
    systemctl disable --now torahMcp.service || true
    rm -f /etc/systemd/system/torahMcp.service
fi
sed -e "s#@BASE@#$BASE#g" -e "s#@PORT@#$PORT#g" -e "s#@PUBLIC_HOST@#$PUBLIC_HOST#g" \
    "$BUNDLE/deploy/corpus-mcp.service" > /etc/systemd/system/corpus-mcp.service
chmod 644 /etc/systemd/system/corpus-mcp.service
systemctl daemon-reload
systemctl enable corpus-mcp.service
systemctl restart corpus-mcp.service
rm -rf "$DATA/corpus.db.previous" "$DATA/precomputed.db.previous" "$DATA/vectors.previous" "$DATA/hf.previous"

if [ "${CORPUS_MCP_NGINX:-auto}" != no ] && command -v nginx >/dev/null 2>&1; then
    CORPUS_MCP_PORT=$PORT sh "$BUNDLE/deploy/enable-nginx-route.sh"
    ENDPOINT="https://$PUBLIC_HOST/mcp"
else
    ENDPOINT="http://127.0.0.1:$PORT/mcp (local only; put a reverse proxy in front to share it)"
fi

echo "waiting for the server to start (the first start downloads the embedding model, ~2 GB)..."
sleep 20
systemctl --no-pager --lines=8 status corpus-mcp.service || true
echo
echo "MCP endpoint: $ENDPOINT"
