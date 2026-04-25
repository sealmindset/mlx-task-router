#!/usr/bin/env bash
# =============================================================================
# MLX Task Router — System Installer
# =============================================================================
# Installs mlx-task-router as a system-level service (LaunchDaemon) that
# starts automatically at boot / after restart.
#
# What this script does:
#   1. Validates prerequisites (Python 3.11+, mlx-lm, macOS)
#   2. Copies the project to /opt/mlx-task-router
#   3. Copies the existing .venv (or creates a new one)
#   4. Installs the package into the venv
#   5. Creates config directory ~/.config/mlx-task-router with .env
#   6. Creates log directory /var/log/mlx-task-router
#   7. Installs LaunchDaemon plist (starts at boot, runs as your user)
#   8. Adds ANTHROPIC_BASE_URL export to shell profile
#   9. Starts the service
#
# Usage:
#   sudo ./install.sh                # Full install
#   sudo ./install.sh --no-start     # Install without starting
#   sudo ./install.sh --upgrade      # Upgrade existing install in-place
#
# Requires: sudo (for /opt, /Library/LaunchDaemons, /var/log)
# =============================================================================

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Constants ────────────────────────────────────────────────────────────────

INSTALL_DIR="/opt/mlx-task-router"
LABEL="com.sealmindset.mlx-task-router"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
LOG_DIR="/var/log/mlx-task-router"
CONFIG_DIR="$HOME/.config/mlx-task-router"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")
CONFIG_DIR="${REAL_HOME}/.config/mlx-task-router"
PORT="${MLX_ROUTER_PORT:-8888}"
HOST="${MLX_ROUTER_HOST:-0.0.0.0}"

# ── Parse arguments ──────────────────────────────────────────────────────────

NO_START=false
UPGRADE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-start)  NO_START=true; shift ;;
        --upgrade)   UPGRADE=true; shift ;;
        -h|--help)
            echo "Usage: sudo ./install.sh [--no-start] [--upgrade]"
            echo ""
            echo "Options:"
            echo "  --no-start   Install without starting the service"
            echo "  --upgrade    Upgrade existing installation in-place"
            echo "  -h, --help   Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

info()  { echo -e "  ${BLUE}▸${NC} $1"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
step()  { echo ""; echo -e "${BOLD}$1${NC}"; }

die() {
    fail "$1"
    exit 1
}

# ── Preflight checks ────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}MLX Task Router — System Installer${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

step "1/9  Preflight checks"

# Must be root (via sudo)
if [[ $EUID -ne 0 ]]; then
    die "This script must be run with sudo: sudo ./install.sh"
fi
ok "Running as root (real user: $REAL_USER)"

# Must be macOS
if [[ "$(uname)" != "Darwin" ]]; then
    die "This installer is for macOS only"
fi
ok "macOS detected ($(sw_vers -productVersion))"

# Must be Apple Silicon
if [[ "$(uname -m)" != "arm64" ]]; then
    die "MLX requires Apple Silicon (arm64). This machine is $(uname -m)."
fi
ok "Apple Silicon ($(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'arm64'))"

# Check for existing .venv in project dir
VENV_SOURCE="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_SOURCE" ]]; then
    die "No .venv found at $VENV_SOURCE. Create one first: python3 -m venv .venv && .venv/bin/pip install -e ."
fi
ok "Project .venv found at $VENV_SOURCE"

# Validate Python version in venv
PYTHON_VERSION=$("$VENV_SOURCE/bin/python3" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 11 ]]; }; then
    die "Python >= 3.11 required. Found $PYTHON_VERSION in .venv"
fi
ok "Python $PYTHON_VERSION in venv"

# Validate mlx-lm is installed
if ! "$VENV_SOURCE/bin/python3" -c "import mlx_lm" 2>/dev/null; then
    die "mlx-lm not installed in .venv. Run: .venv/bin/pip install mlx-lm"
fi
ok "mlx-lm available in venv"

# Validate mlx_task_router is installed
if ! "$VENV_SOURCE/bin/python3" -c "import mlx_task_router" 2>/dev/null; then
    warn "mlx_task_router not installed in venv — will install during setup"
fi

# Check for existing installation
if [[ "$UPGRADE" == false ]] && [[ -d "$INSTALL_DIR" ]]; then
    warn "Existing installation found at $INSTALL_DIR"
    info "Use --upgrade to update, or uninstall first: sudo ./uninstall.sh"
    read -rp "  Overwrite existing installation? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "  Aborted."
        exit 0
    fi
fi

# ── Stop existing service if running ─────────────────────────────────────────

step "2/9  Stopping existing service (if running)"

if launchctl list "$LABEL" &>/dev/null 2>&1; then
    info "Stopping $LABEL..."
    launchctl bootout system/"$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 1
    ok "Service stopped"
else
    info "Service not running — nothing to stop"
fi

# Also check for user-level LaunchAgent (from old installs)
OLD_PLIST="$REAL_HOME/Library/LaunchAgents/${LABEL}.plist"
if [[ -f "$OLD_PLIST" ]]; then
    warn "Found old user-level LaunchAgent at $OLD_PLIST"
    sudo -u "$REAL_USER" launchctl unload "$OLD_PLIST" 2>/dev/null || true
    rm -f "$OLD_PLIST"
    ok "Old LaunchAgent removed"
fi

# ── Copy project to /opt ─────────────────────────────────────────────────────

step "3/9  Installing to $INSTALL_DIR"

# Create install dir
mkdir -p "$INSTALL_DIR"

# rsync project files (excluding .git, __pycache__, .venv for now)
info "Copying project files..."
rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='.pytest_cache' \
    --exclude='*.pyc' \
    --exclude='.mypy_cache' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"
ok "Project files copied"

# Copy .venv
info "Copying Python virtual environment..."
if [[ -d "$INSTALL_DIR/.venv" ]]; then
    rm -rf "$INSTALL_DIR/.venv"
fi
rsync -a --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "$VENV_SOURCE/" "$INSTALL_DIR/.venv/"

# Fix venv symlinks to point to correct Python
# The venv might have symlinks back to original Python — fix them
ORIG_PYTHON=$(readlink -f "$VENV_SOURCE/bin/python3" 2>/dev/null || "$VENV_SOURCE/bin/python3" -c "import sys; print(sys.executable)")
if [[ -L "$INSTALL_DIR/.venv/bin/python" ]]; then
    # Re-create the symlinks to be absolute
    cd "$INSTALL_DIR/.venv/bin"
    for link in python python3; do
        if [[ -L "$link" ]]; then
            TARGET=$(readlink "$VENV_SOURCE/bin/$link")
            # If it was a relative link, resolve it
            if [[ ! "$TARGET" = /* ]]; then
                TARGET=$(cd "$VENV_SOURCE/bin" && readlink -f "$link")
            fi
            rm -f "$link"
            ln -s "$TARGET" "$link"
        fi
    done
    cd - >/dev/null
fi
ok "Virtual environment installed"

# Install package in editable mode from the install dir
info "Installing mlx-task-router package..."
"$INSTALL_DIR/.venv/bin/pip" install -q -e "$INSTALL_DIR" 2>&1 | tail -1 || true
ok "Package installed"

# Set ownership
chown -R "$REAL_USER:staff" "$INSTALL_DIR"
ok "Ownership set to $REAL_USER:staff"

# ── Config directory ─────────────────────────────────────────────────────────

step "4/9  Configuration"

sudo -u "$REAL_USER" mkdir -p "$CONFIG_DIR"

ENV_FILE="$CONFIG_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    ok "Config exists at $ENV_FILE (preserved)"
else
    if [[ -f "$SCRIPT_DIR/.env.example" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
        chown "$REAL_USER:staff" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        ok "Created $ENV_FILE from .env.example"
        warn "Edit $ENV_FILE to add your ANTHROPIC_API_KEY"
    else
        cat > "$ENV_FILE" <<'ENVEOF'
# MLX Task Router Config
# See: https://github.com/sealmindset/mlx-task-router

ANTHROPIC_API_KEY=sk-ant-...
MLX_MODEL=mlx-community/Qwen3-32B-4bit
PORT=8888
HOST=0.0.0.0
MLX_TEMPERATURE=0.7
MLX_TOP_P=0.8
MLX_TOP_K=20
MLX_REPETITION_PENALTY=1.05
ROUTING_THRESHOLD=0.5
ADAPTIVE_ROUTING=true
LOG_ROUTING=true
ENVEOF
        chown "$REAL_USER:staff" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        ok "Created $ENV_FILE with defaults"
        warn "Edit $ENV_FILE to add your ANTHROPIC_API_KEY"
    fi
fi

# Read PORT from config if set
if [[ -f "$ENV_FILE" ]]; then
    CONFIGURED_PORT=$(grep -E '^PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "")
    if [[ -n "$CONFIGURED_PORT" ]]; then
        PORT="$CONFIGURED_PORT"
    fi
fi

# ── Log directory ────────────────────────────────────────────────────────────

step "5/9  Log directory"

mkdir -p "$LOG_DIR"
chown "$REAL_USER:staff" "$LOG_DIR"
ok "Log directory created at $LOG_DIR"

# Create empty log files
touch "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
chown "$REAL_USER:staff" "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
ok "Log files initialized (stdout.log, stderr.log)"

# ── Install LaunchDaemon ─────────────────────────────────────────────────────

step "6/9  LaunchDaemon (auto-start at boot)"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

    <key>Label</key>
    <string>${LABEL}</string>

    <key>UserName</key>
    <string>${REAL_USER}</string>

    <key>GroupName</key>
    <string>staff</string>

    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/.venv/bin/python3</string>
        <string>-m</string>
        <string>mlx_task_router.cli</string>
        <string>serve</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${REAL_HOME}</string>
        <key>PATH</key>
        <string>${INSTALL_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stderr.log</string>

    <key>SoftResourceLimits</key>
    <dict>
        <key>NumberOfFiles</key>
        <integer>4096</integer>
    </dict>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>Nice</key>
    <integer>-5</integer>

</dict>
</plist>
PLISTEOF

# Set correct permissions — LaunchDaemons must be owned by root:wheel
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
ok "LaunchDaemon installed at $PLIST"
info "  Label: $LABEL"
info "  Runs as: $REAL_USER"
info "  Auto-start: at boot (before login)"
info "  Auto-restart: on crash"

# ── Install start.sh to /usr/local/bin ───────────────────────────────────────

step "7/9  Service management script"

mkdir -p /usr/local/bin

cat > /usr/local/bin/mlx-router-ctl <<'CTLEOF'
#!/usr/bin/env bash
# MLX Task Router — Service Control (auto-generated by install.sh)
# Usage: mlx-router-ctl {start|stop|restart|status|logs|health|test}

LABEL="com.sealmindset.mlx-task-router"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
LOG_DIR="/var/log/mlx-task-router"
INSTALL_DIR="/opt/mlx-task-router"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

case "${1:-status}" in
    start)
        echo -e "${BOLD}Starting MLX Task Router...${NC}"
        if launchctl list "$LABEL" &>/dev/null 2>&1; then
            echo -e "  ${YELLOW}Already running${NC}"
        else
            sudo launchctl bootstrap system "$PLIST" 2>/dev/null || sudo launchctl load "$PLIST"
            sleep 2
            if launchctl list "$LABEL" &>/dev/null 2>&1; then
                echo -e "  ${GREEN}Started${NC}"
            else
                echo -e "  ${RED}Failed to start — check logs:${NC} tail -50 $LOG_DIR/stderr.log"
                exit 1
            fi
        fi
        ;;
    stop)
        echo -e "${BOLD}Stopping MLX Task Router...${NC}"
        if launchctl list "$LABEL" &>/dev/null 2>&1; then
            sudo launchctl bootout system/"$LABEL" 2>/dev/null || sudo launchctl unload "$PLIST"
            sleep 1
            echo -e "  ${GREEN}Stopped${NC}"
        else
            echo -e "  ${YELLOW}Not running${NC}"
        fi
        ;;
    restart)
        "$0" stop
        sleep 2
        "$0" start
        ;;
    status)
        echo -e "${BOLD}MLX Task Router Status${NC}"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        if launchctl list "$LABEL" &>/dev/null 2>&1; then
            PID=$(launchctl list "$LABEL" 2>/dev/null | grep -oE '^[0-9]+' || echo "unknown")
            echo -e "  Service:  ${GREEN}running${NC} (PID: $PID)"
        else
            echo -e "  Service:  ${RED}stopped${NC}"
        fi
        echo "  Install:  $INSTALL_DIR"
        echo "  Plist:    $PLIST"
        echo "  Logs:     $LOG_DIR/"
        echo ""

        # Try health endpoint
        PORT=$(grep -E '^PORT=' "$HOME/.config/mlx-task-router/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "8888")
        PORT="${PORT:-8888}"
        if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
            HEALTH=$(curl -sf "http://localhost:$PORT/health")
            echo -e "  Health:   ${GREEN}$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null)${NC}"
            echo "  Model:    $(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('model','none'))" 2>/dev/null)"
            echo "  Loaded:   $(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('model_loaded',False))" 2>/dev/null)"
        else
            echo -e "  Health:   ${YELLOW}not reachable${NC} (http://localhost:$PORT)"
        fi

        # Stats
        if curl -sf "http://localhost:$PORT/stats" >/dev/null 2>&1; then
            STATS=$(curl -sf "http://localhost:$PORT/stats")
            echo ""
            echo "  Requests: $(echo "$STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"total={d.get('requests_total',0)}, local={d.get('requests_local',0)}, fwd={d.get('requests_forward',0)}\")" 2>/dev/null)"
            echo "  Savings:  $(echo "$STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('cost_saved_display','$0.0000'))" 2>/dev/null)"
        fi
        echo ""
        ;;
    logs)
        echo -e "${BOLD}MLX Task Router Logs${NC}"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo -e "${DIM}(Ctrl+C to exit)${NC}"
        echo ""
        tail -f "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
        ;;
    health)
        PORT=$(grep -E '^PORT=' "$HOME/.config/mlx-task-router/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "8888")
        PORT="${PORT:-8888}"
        curl -sf "http://localhost:$PORT/health" | python3 -m json.tool 2>/dev/null || echo -e "${RED}Not reachable${NC}"
        ;;
    test)
        PORT=$(grep -E '^PORT=' "$HOME/.config/mlx-task-router/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "8888")
        PORT="${PORT:-8888}"
        echo -e "${BOLD}Testing MLX Task Router (localhost:$PORT)${NC}"
        echo ""

        echo -n "  Root endpoint:       "
        curl -sf "http://localhost:$PORT/" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Health:              "
        curl -sf "http://localhost:$PORT/health" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Stats:               "
        curl -sf "http://localhost:$PORT/stats" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Config:              "
        curl -sf "http://localhost:$PORT/config" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Routing history:     "
        curl -sf "http://localhost:$PORT/routing/history" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Semantic cache:      "
        curl -sf "http://localhost:$PORT/semantic-cache" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Annealing:           "
        curl -sf "http://localhost:$PORT/annealing" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo -n "  Perf metrics:        "
        curl -sf "http://localhost:$PORT/perf" >/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}FAIL${NC}"

        echo ""
        echo -e "${BOLD}Sending test message...${NC}"
        RESPONSE=$(curl -sf -X POST "http://localhost:$PORT/v1/messages" \
            -H "Content-Type: application/json" \
            -H "x-api-key: test" \
            -H "anthropic-version: 2023-06-01" \
            -d '{"model":"claude-sonnet-4-20250514","max_tokens":50,"messages":[{"role":"user","content":"echo hello"}]}' 2>/dev/null)
        if [[ -n "$RESPONSE" ]]; then
            echo -e "  ${GREEN}Response received${NC}"
            echo "$RESPONSE" | python3 -m json.tool 2>/dev/null | head -20
        else
            echo -e "  ${RED}No response${NC}"
        fi
        echo ""
        ;;
    *)
        echo "Usage: mlx-router-ctl {start|stop|restart|status|logs|health|test}"
        echo ""
        echo "Commands:"
        echo "  start     Start the service"
        echo "  stop      Stop the service"
        echo "  restart   Stop then start the service"
        echo "  status    Show service status, health, and stats"
        echo "  logs      Tail live log output (Ctrl+C to exit)"
        echo "  health    Quick health check (JSON output)"
        echo "  test      Run endpoint smoke tests"
        exit 1
        ;;
esac
CTLEOF

# Inject DIM color variable (can't nest single-quoted heredoc)
sed -i '' "s|\${DIM}|\\\\033[2m|g" /usr/local/bin/mlx-router-ctl 2>/dev/null || true

chmod 755 /usr/local/bin/mlx-router-ctl
ok "Installed mlx-router-ctl to /usr/local/bin/"
info "Usage: mlx-router-ctl {start|stop|restart|status|logs|health|test}"

# ── Shell profile ────────────────────────────────────────────────────────────

step "8/9  Shell profile"

SHELL_RC="$REAL_HOME/.zshrc"
if [[ -f "$REAL_HOME/.bashrc" ]] && [[ ! -f "$REAL_HOME/.zshrc" ]]; then
    SHELL_RC="$REAL_HOME/.bashrc"
fi

EXPORT_LINE="export ANTHROPIC_BASE_URL=http://localhost:${PORT}"

if [[ -f "$SHELL_RC" ]] && grep -qF "ANTHROPIC_BASE_URL" "$SHELL_RC" 2>/dev/null; then
    # Update existing line
    sed -i '' "s|export ANTHROPIC_BASE_URL=.*|${EXPORT_LINE}|" "$SHELL_RC"
    ok "Updated ANTHROPIC_BASE_URL in $SHELL_RC"
else
    # Append new block
    cat >> "$SHELL_RC" <<RCEOF

# MLX Task Router — route AI requests through local MLX model
${EXPORT_LINE}
RCEOF
    ok "Added ANTHROPIC_BASE_URL to $SHELL_RC"
fi
info "All Claude Code / Windsurf traffic will route through localhost:$PORT"

# ── Start service ────────────────────────────────────────────────────────────

step "9/9  Starting service"

if [[ "$NO_START" == true ]]; then
    info "Skipping start (--no-start flag)"
    info "Start manually: sudo mlx-router-ctl start"
else
    info "Loading LaunchDaemon..."
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
    sleep 3

    if launchctl list "$LABEL" &>/dev/null 2>&1; then
        ok "Service started"

        # Wait for health endpoint
        info "Waiting for server to become healthy..."
        HEALTHY=false
        for i in $(seq 1 30); do
            if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
                HEALTHY=true
                break
            fi
            sleep 2
        done

        if [[ "$HEALTHY" == true ]]; then
            HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null || echo "{}")
            MODEL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model','unknown'))" 2>/dev/null || echo "unknown")
            ok "Server healthy — model: $MODEL"
        else
            warn "Server started but health endpoint not responding yet"
            info "Model may still be loading (~30-60s for Qwen3-32B)"
            info "Check status: mlx-router-ctl status"
            info "View logs:    mlx-router-ctl logs"
        fi
    else
        fail "Service failed to start"
        info "Check logs: tail -50 $LOG_DIR/stderr.log"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo "  Install:    $INSTALL_DIR"
echo "  Config:     $CONFIG_DIR/.env"
echo "  Logs:       $LOG_DIR/"
echo "  Service:    $PLIST"
echo "  Proxy URL:  http://localhost:$PORT"
echo ""
echo -e "${BOLD}Management commands:${NC}"
echo "  mlx-router-ctl status     — check service & health"
echo "  mlx-router-ctl restart    — restart the service"
echo "  mlx-router-ctl logs       — tail live logs"
echo "  mlx-router-ctl test       — smoke test all endpoints"
echo "  mlx-router-ctl stop       — stop the service"
echo ""
echo -e "${BOLD}Configuration:${NC}"
echo "  Edit:       nano $CONFIG_DIR/.env"
echo "  Reload:     curl -X POST http://localhost:$PORT/config/reload"
echo ""
echo -e "${DIM}Restart your terminal or run: source $SHELL_RC${NC}"
echo ""
