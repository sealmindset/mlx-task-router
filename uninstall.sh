#!/usr/bin/env bash
# Uninstall script for MLX Task Router
# Removes: launchd service, binary, config, shell profile entry, and MLX model cache

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

LABEL="com.sealmindset.mlx-task-router"
PLIST_DAEMON="/Library/LaunchDaemons/${LABEL}.plist"
PLIST_AGENT="$HOME/Library/LaunchAgents/${LABEL}.plist"
INSTALL_DIR="/opt/mlx-task-router"
CONFIG_DIR="$HOME/.config/mlx-task-router"
LOG_DIR="/var/log/mlx-task-router"
CTL_BIN="/usr/local/bin/mlx-router-ctl"
HF_CACHE="$HOME/.cache/huggingface/hub"

MODELS=(
    "models--mlx-community--Qwen2.5-Coder-32B-Instruct-4bit"
    "models--mlx-community--Qwen3-32B-4bit"
    "models--mlx-community--Qwen3-30B-A3B-4bit"
    "models--mlx-community--Qwen3-Coder-30B-A3B-Instruct-4bit"
    "models--mlx-community--Qwen3-Coder-Next-4bit"
)

SHELL_FILES=(
    "$HOME/.zshrc"
    "$HOME/.bashrc"
    "$HOME/.bash_profile"
    "$HOME/.profile"
)

# ── Header ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}MLX Task Router — Uninstaller${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "The following will be permanently removed:"
echo ""
echo "  Service"
echo "    • Stop and unload launchd service: $LABEL"
if [ -f "$PLIST_DAEMON" ]; then
    echo "    • Delete daemon plist: $PLIST_DAEMON"
fi
if [ -f "$PLIST_AGENT" ]; then
    echo "    • Delete agent plist: $PLIST_AGENT (legacy)"
fi
echo ""
echo "  Package"
if [ -d "$INSTALL_DIR" ]; then
    echo "    • Remove install directory: $INSTALL_DIR"
fi
if [ -f "$CTL_BIN" ]; then
    echo "    • Remove service control: $CTL_BIN"
fi
echo "    • Uninstall mlx-router binary (uv tool / pipx)"
echo ""
echo "  Config & Logs"
echo "    • $CONFIG_DIR"
if [ -d "$LOG_DIR" ]; then
    echo "    • $LOG_DIR"
fi
echo ""
echo "  Shell Profiles"
for f in "${SHELL_FILES[@]}"; do
    if [ -f "$f" ] && grep -q "ANTHROPIC_BASE_URL\|mlx-task-router" "$f" 2>/dev/null; then
        echo "    • Remove ANTHROPIC_BASE_URL block from $f"
    fi
done
echo ""
echo "  MLX Models"
TOTAL_MODEL_SIZE=0
for m in "${MODELS[@]}"; do
    dir="$HF_CACHE/$m"
    if [ -d "$dir" ]; then
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo "    • $size   $m"
    fi
done
echo ""
echo -e "${YELLOW}Will NOT remove:${NC}"
echo "    • This project directory (source code)"
echo ""

# ── Single confirmation ───────────────────────────────────────────────────────

read -rp "Continue? This cannot be undone. [y/N] " confirm
echo ""
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

ERRORS=0

ok()   { echo -e "${GREEN}done${NC}"; }
skip() { echo -e "skipped (${1})"; }
fail() { echo -e "${RED}failed${NC} — $1"; ERRORS=$((ERRORS + 1)); }

# ── 1. Stop launchd service ───────────────────────────────────────────────────

echo -n "  Stopping launchd service... "
if launchctl list "$LABEL" &>/dev/null; then
    # Try system-level (LaunchDaemon) first, then user-level (LaunchAgent)
    if sudo launchctl bootout system/"$LABEL" 2>/dev/null; then
        ok
    elif launchctl unload "$PLIST_DAEMON" 2>/dev/null; then
        ok
    elif launchctl unload "$PLIST_AGENT" 2>/dev/null; then
        ok
    else
        fail "launchctl unload returned an error"
    fi
else
    skip "not running"
fi

# ── 2. Remove plist files ─────────────────────────────────────────────────────

echo -n "  Removing LaunchDaemon plist... "
if [ -f "$PLIST_DAEMON" ]; then
    if sudo rm "$PLIST_DAEMON" 2>/dev/null; then
        ok
    else
        fail "could not delete $PLIST_DAEMON (try with sudo)"
    fi
else
    skip "not found"
fi

echo -n "  Removing LaunchAgent plist (legacy)... "
if [ -f "$PLIST_AGENT" ]; then
    if rm "$PLIST_AGENT" 2>/dev/null; then
        ok
    else
        fail "could not delete $PLIST_AGENT"
    fi
else
    skip "not found"
fi

# ── 2b. Remove install directory ─────────────────────────────────────────────

echo -n "  Removing install directory ($INSTALL_DIR)... "
if [ -d "$INSTALL_DIR" ]; then
    if sudo rm -rf "$INSTALL_DIR" 2>/dev/null; then
        ok
    else
        fail "could not delete $INSTALL_DIR (try with sudo)"
    fi
else
    skip "not found"
fi

# ── 2c. Remove mlx-router-ctl ────────────────────────────────────────────────

echo -n "  Removing mlx-router-ctl... "
if [ -f "$CTL_BIN" ]; then
    if sudo rm "$CTL_BIN" 2>/dev/null; then
        ok
    else
        fail "could not delete $CTL_BIN"
    fi
else
    skip "not found"
fi

# ── 3. Uninstall binary ───────────────────────────────────────────────────────

echo -n "  Uninstalling mlx-router binary... "
if uv tool list 2>/dev/null | grep -q "mlx-task-router"; then
    if uv tool uninstall mlx-task-router 2>/dev/null; then
        ok
    else
        fail "uv tool uninstall failed"
    fi
elif command -v pipx &>/dev/null && pipx list 2>/dev/null | grep -q "mlx-task-router"; then
    if pipx uninstall mlx-task-router 2>/dev/null; then
        ok
    else
        fail "pipx uninstall failed"
    fi
elif [ -f "$HOME/.local/bin/mlx-router" ]; then
    # Fallback: remove the binary directly if installed manually
    if rm "$HOME/.local/bin/mlx-router" 2>/dev/null; then
        ok
    else
        fail "could not delete $HOME/.local/bin/mlx-router"
    fi
else
    skip "not installed via uv, pipx, or ~/.local/bin"
fi

# ── 4. Remove config directory ────────────────────────────────────────────────

echo -n "  Removing config directory... "
if [ -d "$CONFIG_DIR" ]; then
    if rm -rf "$CONFIG_DIR" 2>/dev/null; then
        ok
    else
        fail "could not delete $CONFIG_DIR"
    fi
else
    skip "not found"
fi

# ── 4b. Remove log directory ─────────────────────────────────────────────────

echo -n "  Removing log directory... "
if [ -d "$LOG_DIR" ]; then
    if sudo rm -rf "$LOG_DIR" 2>/dev/null; then
        ok
    else
        fail "could not delete $LOG_DIR (try with sudo)"
    fi
else
    skip "not found"
fi

# ── 5. Clean shell profiles ───────────────────────────────────────────────────

echo -n "  Cleaning shell profiles... "
CLEANED=0
for f in "${SHELL_FILES[@]}"; do
    [ -f "$f" ] || continue
    grep -q "ANTHROPIC_BASE_URL\|mlx-task-router" "$f" 2>/dev/null || continue

    python3 - "$f" <<'PYEOF'
import sys, re

path = sys.argv[1]
lines = open(path).read().splitlines(keepends=True)

result = []
i = 0
while i < len(lines):
    line = lines[i]
    # Find the ANTHROPIC_BASE_URL export line.
    if re.match(r'export ANTHROPIC_BASE_URL=http://localhost:\d+\s*(\n|$)', line):
        # Walk backwards through result, removing any comment lines that sit
        # directly above the export (regardless of their content).
        while result and result[-1].lstrip().startswith('#'):
            result.pop()
        # Remove one preceding blank line if present.
        if result and result[-1].strip() == '':
            result.pop()
        i += 1
        continue
    result.append(line)
    i += 1

# Collapse 3+ consecutive blank lines to 2 (cosmetic cleanup).
text = re.sub(r'\n{3,}', '\n\n', ''.join(result))

open(path, 'w').write(text)
PYEOF

    CLEANED=$((CLEANED + 1))
done

if [ "$CLEANED" -gt 0 ]; then
    ok
else
    skip "nothing to remove"
fi

# ── 6. Remove MLX model cache ─────────────────────────────────────────────────

echo ""
echo "  Removing MLX models from Hugging Face cache..."
MODEL_ERRORS=0
for m in "${MODELS[@]}"; do
    dir="$HF_CACHE/$m"
    if [ -d "$dir" ]; then
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo -n "    $m ($size)... "
        if rm -rf "$dir" 2>/dev/null; then
            ok
        else
            fail "could not delete $dir"
            MODEL_ERRORS=$((MODEL_ERRORS + 1))
        fi
    else
        echo "    $m — not found, skipping"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}MLX Task Router fully removed.${NC}"
else
    echo -e "${YELLOW}${BOLD}Completed with $ERRORS error(s). Some items may need manual cleanup.${NC}"
fi
echo ""
echo "  Restart your terminal (or run 'source ~/.zshrc') to clear"
echo "  ANTHROPIC_BASE_URL from your current session."
echo ""
