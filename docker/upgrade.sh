#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*"; exit 1; }

echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     OpenLucid Upgrade Script      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo

# 1. Backup .env
if [ -f .env ]; then
  mkdir -p env-backup
  BACKUP_FILE="env-backup/.env.backup_$(date +%Y%m%d_%H%M%S)"
  cp .env "$BACKUP_FILE"
  echo -e "${GREEN}✓${NC} Backed up .env → $BACKUP_FILE"
else
  echo -e "${YELLOW}⚠ No .env found — skipping backup${NC}"
fi

# 2. Pull latest code
echo "Pulling latest code..."
git -C .. fetch origin
git -C .. reset --hard origin/main
echo -e "${GREEN}✓${NC} Code updated"

# 3. Sync new config variables from .env.example → .env
if [ -f .env ] && [ -f .env.example ]; then
  ADDED=0
  while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
    # Extract key (everything before the first =)
    KEY="${line%%=*}"
    # Skip if key already exists in .env
    if ! grep -q "^${KEY}=" .env 2>/dev/null; then
      echo "$line" >> .env
      ADDED=$((ADDED + 1))
      echo -e "  ${GREEN}+${NC} Added new variable: $KEY"
    fi
  done < .env.example
  if [ "$ADDED" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No new config variables"
  else
    echo -e "${GREEN}✓${NC} Added $ADDED new variable(s) to .env"
  fi
fi

# 4. Ensure base image is pullable (same mirror logic as install.sh)
DOCKER_MIRROR="https://docker.1ms.run"
BASE_IMAGE="python:3.11-slim"

OS="$(uname -s)"
case "$OS" in
  Linux)  OS_TYPE="linux" ;;
  Darwin) OS_TYPE="mac" ;;
esac

_docker_daemon_json() {
  if [ "$OS_TYPE" = "linux" ]; then echo "/etc/docker/daemon.json"
  else echo "$HOME/.docker/daemon.json"; fi
}

_try_pull() {
  docker pull "$BASE_IMAGE" &>/dev/null &
  local pid=$! elapsed=0
  while [ $elapsed -lt 60 ]; do
    if ! kill -0 "$pid" 2>/dev/null; then wait "$pid" 2>/dev/null; return $?; fi
    sleep 2; elapsed=$((elapsed + 2))
  done
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; return 1
}

echo "Pulling latest base image..."
if ! _try_pull; then
  cfg="$(_docker_daemon_json)"
  if ! grep -q "registry-mirrors" "$cfg" 2>/dev/null; then
    warn "Docker Hub unreachable. Configuring mirror: $DOCKER_MIRROR"
    if [ "$OS_TYPE" = "linux" ]; then
      if [ -f "$cfg" ]; then
        sudo python3 -c "import json; c=json.load(open('$cfg')); c['registry-mirrors']=['$DOCKER_MIRROR']; json.dump(c,open('$cfg','w'),indent=2)"
      else
        sudo mkdir -p /etc/docker
        echo "{\"registry-mirrors\":[\"$DOCKER_MIRROR\"]}" | sudo tee "$cfg" >/dev/null
      fi
      sudo systemctl restart docker; sleep 3
    else
      mkdir -p "$HOME/.docker"
      if [ -f "$cfg" ]; then
        python3 -c "import json; c=json.load(open('$cfg')); c['registry-mirrors']=['$DOCKER_MIRROR']; json.dump(c,open('$cfg','w'),indent=2)"
      else
        echo "{\"registry-mirrors\":[\"$DOCKER_MIRROR\"]}" > "$cfg"
      fi
      osascript -e 'quit app "Docker Desktop"' 2>/dev/null || true; sleep 3
      open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true
      local w=0; while [ $w -lt 60 ]; do docker info &>/dev/null && break; sleep 3; w=$((w+3)); done
    fi
    info "Docker mirror configured"
    _try_pull || warn "Pull still failed — build may fall back to cache"
  else
    warn "Pull failed — build will use cache if available"
  fi
else
  info "Base image updated"
fi

# 5. Rebuild images
echo "Building images (this may take a moment)..."
docker compose build
echo -e "${GREEN}✓${NC} Images rebuilt"

# 6. Restart services
echo "Restarting services..."
docker compose up -d
echo -e "${GREEN}✓${NC} Services restarted"

echo
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Upgrade complete! 🎉          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo
echo -e "Access OpenLucid at: ${GREEN}http://localhost:${APP_PORT:-80}${NC}"
echo -e "View logs: ${YELLOW}docker compose logs -f app${NC}"
