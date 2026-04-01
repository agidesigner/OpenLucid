#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*"; exit 1; }

cd "$(dirname "$0")"

echo -e "${GREEN}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         OpenLucid · Install                ║${NC}"
echo -e "${GREEN}║   Marketing World Model                       ║${NC}"
echo -e "${GREEN}║   Your data — found, understood, and used by AI║${NC}"
echo -e "${GREEN}║   Interfaces: MCP / Agent / App               ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════╝${NC}"
echo

# ── 1. Check OS ──
OS="$(uname -s)"
case "$OS" in
  Linux)  OS_TYPE="linux" ;;
  Darwin) OS_TYPE="mac" ;;
  *)      error "Unsupported OS: $OS. Only Linux and macOS are supported." ;;
esac

# ── 2. Check / Install Docker ──
install_docker_linux() {
  echo "Installing Docker via official script..."
  curl -fsSL https://get.docker.com | sh
  sudo systemctl enable docker
  sudo systemctl start docker

  # Add current user to docker group so sudo is not needed
  if ! groups | grep -q docker; then
    sudo usermod -aG docker "$USER"
    warn "Added $USER to docker group. You may need to log out and back in."
  fi
}

install_docker_mac() {
  if command -v brew &>/dev/null; then
    echo "Installing Docker via Homebrew..."
    brew install --cask docker
    echo ""
    warn "Docker Desktop installed. Please launch it from Applications,"
    warn "wait for it to start, then re-run this script."
    exit 0
  else
    echo ""
    error "Docker is not installed. Please install Docker Desktop from:\n  https://docs.docker.com/desktop/install/mac-install/"
  fi
}

if command -v docker &>/dev/null; then
  info "Docker found: $(docker --version)"
else
  warn "Docker not found. Attempting to install..."
  if [ "$OS_TYPE" = "linux" ]; then
    install_docker_linux
  else
    install_docker_mac
  fi
  # Verify
  if ! command -v docker &>/dev/null; then
    error "Docker installation failed. Please install manually:\n  https://docs.docker.com/get-docker/"
  fi
  info "Docker installed: $(docker --version)"
fi

# ── 3. Check Docker daemon is running ──
if ! docker info &>/dev/null; then
  if [ "$OS_TYPE" = "linux" ]; then
    warn "Docker daemon not running. Starting..."
    sudo systemctl start docker
    sleep 2
    if ! docker info &>/dev/null; then
      error "Cannot connect to Docker daemon. Try: sudo systemctl start docker"
    fi
  else
    error "Docker daemon not running. Please start Docker Desktop and re-run this script."
  fi
fi
info "Docker daemon is running"

# ── 4. Check Docker Compose ──
if docker compose version &>/dev/null; then
  info "Docker Compose found: $(docker compose version --short)"
elif command -v docker-compose &>/dev/null; then
  error "Found legacy docker-compose but not the Compose plugin.\n  Please upgrade: https://docs.docker.com/compose/install/"
else
  if [ "$OS_TYPE" = "linux" ]; then
    warn "Docker Compose plugin not found. Installing..."
    sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null \
      || sudo yum install -y docker-compose-plugin 2>/dev/null \
      || error "Could not install Docker Compose plugin. Please install manually:\n  https://docs.docker.com/compose/install/linux/"
    info "Docker Compose installed: $(docker compose version --short)"
  else
    error "Docker Compose not available. Please ensure Docker Desktop is running."
  fi
fi

# ── 5. Ensure Docker Hub connectivity (auto-configure mirror if needed) ──
DOCKER_MIRROR="https://docker.1ms.run"
BASE_IMAGE="python:3.11-slim"

_docker_daemon_json() {
  if [ "$OS_TYPE" = "linux" ]; then
    echo "/etc/docker/daemon.json"
  else
    echo "$HOME/.docker/daemon.json"
  fi
}

_has_mirror() {
  local cfg
  cfg="$(_docker_daemon_json)"
  [ -f "$cfg" ] && grep -q "registry-mirrors" "$cfg" 2>/dev/null
}

_configure_mirror() {
  local cfg
  cfg="$(_docker_daemon_json)"
  warn "Configuring Docker Hub mirror: $DOCKER_MIRROR"

  if [ "$OS_TYPE" = "linux" ]; then
    if [ -f "$cfg" ]; then
      sudo python3 -c "
import json, sys
with open('$cfg') as f: c = json.load(f)
c['registry-mirrors'] = ['$DOCKER_MIRROR']
with open('$cfg','w') as f: json.dump(c, f, indent=2)
"
    else
      sudo mkdir -p /etc/docker
      echo "{\"registry-mirrors\":[\"$DOCKER_MIRROR\"]}" | sudo tee "$cfg" >/dev/null
    fi
    sudo systemctl restart docker
    sleep 3
  else
    mkdir -p "$HOME/.docker"
    if [ -f "$cfg" ]; then
      python3 -c "
import json
with open('$cfg') as f: c = json.load(f)
c['registry-mirrors'] = ['$DOCKER_MIRROR']
with open('$cfg','w') as f: json.dump(c, f, indent=2)
"
    else
      echo "{\"registry-mirrors\":[\"$DOCKER_MIRROR\"]}" > "$cfg"
    fi
    # Restart Docker Desktop
    osascript -e 'quit app "Docker Desktop"' 2>/dev/null || osascript -e 'quit app "Docker"' 2>/dev/null || true
    sleep 3
    open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true
    echo "  Waiting for Docker to restart..."
    local w=0
    while [ $w -lt 60 ]; do
      if docker info &>/dev/null; then break; fi
      sleep 3
      w=$((w + 3))
    done
    if ! docker info &>/dev/null; then
      error "Docker did not restart in time. Please restart Docker Desktop manually and re-run this script."
    fi
  fi
  info "Docker Hub mirror configured"
}

_try_pull() {
  # Pull with a timeout (background + deadline)
  local deadline=60
  docker pull "$BASE_IMAGE" &>/dev/null &
  local pid=$!
  local elapsed=0
  while [ $elapsed -lt $deadline ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null
      return $?
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  # Timed out
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  return 1
}

# Skip pull check if the image already exists locally
if docker image inspect "$BASE_IMAGE" &>/dev/null; then
  info "Base image $BASE_IMAGE is cached"
else
  echo "Pulling base image $BASE_IMAGE (timeout 60s)..."
  if _try_pull; then
    info "Base image ready"
  else
    warn "Cannot pull $BASE_IMAGE from Docker Hub (network issue)"
    if _has_mirror; then
      error "Docker Hub mirror is already configured but pull still failed.\n  Please check your network connection and try again."
    fi
    _configure_mirror
    echo "Retrying pull with mirror..."
    if _try_pull; then
      info "Base image ready (via mirror)"
    else
      error "Still cannot pull $BASE_IMAGE.\n  Please check your network and try again, or pull it manually:\n  docker pull $BASE_IMAGE"
    fi
  fi
fi

# ── 6. Setup .env ──
if [ -f .env ]; then
  info "Config file .env already exists (keeping current values)"
else
  cp .env.example .env
  # Generate a random SECRET_KEY
  NEW_SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
  if [ -n "$NEW_SECRET" ]; then
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${NEW_SECRET}/" .env && rm -f .env.bak
    info "Generated random SECRET_KEY"
  else
    warn "Could not generate SECRET_KEY. Please set it manually in .env"
  fi
  info "Created .env from template"
fi

# ── 7. Start services ──
echo ""
echo "Starting OpenLucid (this may take a few minutes on first run)..."
echo ""
docker compose up -d --build

# ── 8. Wait for app to be healthy ──
echo ""
echo "Waiting for application to start..."
MAX_WAIT=60
WAITED=0
APP_PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2 | cut -d'#' -f1 | tr -d ' ' || echo "80")
APP_PORT="${APP_PORT:-80}"

while [ $WAITED -lt $MAX_WAIT ]; do
  if curl -sf "http://localhost:${APP_PORT}/health" &>/dev/null; then
    break
  fi
  sleep 2
  WAITED=$((WAITED + 2))
  printf "."
done
echo ""

if [ $WAITED -ge $MAX_WAIT ]; then
  warn "App is still starting. Check logs with: docker compose logs -f app"
else
  info "Application is ready!"
fi

# ── 9. Done ──
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Installation complete!                     ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════╝${NC}"
echo ""
if [ "$APP_PORT" = "80" ]; then
  echo -e "  Open ${GREEN}http://localhost${NC} in your browser"
else
  echo -e "  Open ${GREEN}http://localhost:${APP_PORT}${NC} in your browser"
fi
echo ""
echo "  Next steps:"
echo "    1. Create your admin account on the setup page"
echo "    2. Go to Settings to configure your LLM"
echo "    3. Create your first product and start planning!"
echo ""
echo -e "  View logs:    ${YELLOW}cd docker && docker compose logs -f app${NC}"
echo -e "  Stop:         ${YELLOW}cd docker && docker compose down${NC}"
echo -e "  Upgrade:      ${YELLOW}cd docker && ./upgrade.sh${NC}"
