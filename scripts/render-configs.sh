#!/usr/bin/env bash
# Render per-deployment configs.
#
# Reads $REPO/deploy.env and substitutes the operator's values into all
# template files under etc/, data/, web/, writing the rendered output to
# $REPO/staging/. Install commands in tileserver-noborder.md §9 reference
# the staging tree (e.g. $REPO/staging/etc/systemd/system/...).
#
# Idempotent: clears staging/ before rendering. Safe to re-run after
# editing deploy.env.
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${1:-$REPO/deploy.env}"
STAGING="$REPO/staging"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found." >&2
    echo "       Copy deploy.env.example to deploy.env and edit your values." >&2
    exit 1
fi

# shellcheck disable=SC1090
. "$ENV_FILE"

# Required vars
: "${USER_NAME:?USER_NAME must be set in $ENV_FILE}"
: "${DOMAIN:?DOMAIN must be set in $ENV_FILE}"

# Optional vars (resolve to defaults derived from required vars).
# These mirror the defaults documented in deploy.env.example.
: "${BUILD_ROOT:=/work/${USER_NAME}/planetiler}"
: "${TILESERVER_HOME:=/home/${USER_NAME}/tileserver-gl}"
: "${TILESERVER_DATA:=${TILESERVER_HOME}/data}"
: "${HTTP_ROOT:=/home/${USER_NAME}/http/${DOMAIN}}"
: "${REPO_DEPLOYED:=/home/${USER_NAME}/tileserver-noborder}"
# (REPO_DEPLOYED is the path the systemd unit hardcodes for ExecStart;
#  rebuild.sh derives REPO from its own location at runtime so this only
#  matters for the systemd unit ExecStart line.)
# Note: deploy.env may set this as REPO; honor that if so.
if [[ -n "${REPO:-}" && "$REPO" != "$(cd "$(dirname "$0")/.." && pwd)" ]]; then
    REPO_DEPLOYED="$REPO"
fi

# Defaults baked into the source files (the strings to be replaced)
DEF_USER="shimotsuki"
DEF_DOMAIN="tile.hogehoge.com"
DEF_BUILD_ROOT="/work/shimotsuki/planetiler"
DEF_TILESERVER_HOME="/home/shimotsuki/tileserver-gl"
DEF_TILESERVER_DATA="/home/shimotsuki/tileserver-gl/data"
DEF_HTTP_ROOT="/home/shimotsuki/http/tile.hogehoge.com"
DEF_REPO="/home/shimotsuki/tileserver-noborder"

# Reset staging/ for an idempotent render
rm -rf "$STAGING"
mkdir -p "$STAGING"

# Copy source trees into staging/, preserving structure + file modes
for d in etc data web; do
    if [[ -d "$REPO/$d" ]]; then
        cp -a "$REPO/$d" "$STAGING/$d"
    fi
done

# Substitute. Order matters: replace longer/more-specific patterns first
# so the bare USER/DOMAIN replacements at the end only hit standalone
# occurrences.
#
# Using `|` as the sed delimiter so paths with `/` don't need escaping.
substitute() {
    local file="$1"
    sed -i \
        -e "s|${DEF_HTTP_ROOT}|${HTTP_ROOT}|g" \
        -e "s|${DEF_REPO}|${REPO_DEPLOYED}|g" \
        -e "s|${DEF_TILESERVER_DATA}|${TILESERVER_DATA}|g" \
        -e "s|${DEF_TILESERVER_HOME}|${TILESERVER_HOME}|g" \
        -e "s|${DEF_BUILD_ROOT}|${BUILD_ROOT}|g" \
        -e "s|${DEF_DOMAIN}|${DOMAIN}|g" \
        -e "s|\\b${DEF_USER}\\b|${USER_NAME}|g" \
        "$file"
}

# Substitute in every regular file under staging/
find "$STAGING" -type f -print0 | while IFS= read -r -d '' f; do
    substitute "$f"
done

# Rename nginx site config files that embed the domain in their basename
NGINX_DIR="$STAGING/etc/nginx/sites-available"
if [[ -d "$NGINX_DIR" && "$DOMAIN" != "$DEF_DOMAIN" ]]; then
    for f in "$NGINX_DIR/$DEF_DOMAIN"*; do
        [[ -e "$f" ]] || continue
        new="${f/$DEF_DOMAIN/$DOMAIN}"
        mv "$f" "$new"
    done
fi

# Summary
cat <<EOF
Rendered $REPO/etc/, data/, web/ → $STAGING/
  USER_NAME       = ${USER_NAME}
  DOMAIN          = ${DOMAIN}
  BUILD_ROOT      = ${BUILD_ROOT}
  TILESERVER_HOME = ${TILESERVER_HOME}
  TILESERVER_DATA = ${TILESERVER_DATA}
  HTTP_ROOT       = ${HTTP_ROOT}
  REPO (deployed) = ${REPO_DEPLOYED}

Install from $STAGING/{etc,data,web}/... per tileserver-noborder.md §9.
EOF
