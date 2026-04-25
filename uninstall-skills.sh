#!/usr/bin/env bash
# Uninstall script for /mlx-it and /mlx-usage Claude Code skills
# Removes the skill command files and their dedicated reference document.

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

CLAUDE_DIR="$HOME/.claude"

SKILL_FILES=(
    "$CLAUDE_DIR/commands/mlx-it.md"
    "$CLAUDE_DIR/commands/mlx-usage.md"
    "$CLAUDE_DIR/make-it/references/mlx-reference.md"
)

# ── Header ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}MLX Skills — Uninstaller${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "The following skill files will be permanently removed:"
echo ""

ALL_PRESENT=true
for f in "${SKILL_FILES[@]}"; do
    if [ -f "$f" ]; then
        echo "    • $f"
    else
        echo -e "    • $f ${YELLOW}(not found)${NC}"
        ALL_PRESENT=false
    fi
done

echo ""
echo -e "${YELLOW}Will NOT remove:${NC}"
echo "    • Cross-references in other skills (resume-it, design-blueprint, etc.)"
echo "      These are harmless — the skills simply won't be invocable."
echo "    • Project memory under ~/.claude/projects/"
echo ""

if [ "$ALL_PRESENT" = false ]; then
    echo -e "${YELLOW}Note: one or more files were not found — they may already be removed.${NC}"
    echo ""
fi

# ── Single confirmation ───────────────────────────────────────────────────────

read -rp "Continue? [y/N] " confirm
echo ""
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

ERRORS=0

ok()   { echo -e "${GREEN}done${NC}"; }
skip() { echo -e "skipped (${1})"; }
fail() { echo -e "${RED}failed${NC} — $1"; ERRORS=$((ERRORS + 1)); }

# ── Remove skill files ────────────────────────────────────────────────────────

for f in "${SKILL_FILES[@]}"; do
    label="${f/$HOME\//~/}"
    echo -n "  Removing $label... "
    if [ -f "$f" ]; then
        if rm "$f" 2>/dev/null; then
            ok
        else
            fail "could not delete $f"
        fi
    else
        skip "not found"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}/mlx-it and /mlx-usage fully removed.${NC}"
    echo ""
    echo "  The /mlx-it and /mlx-usage commands are no longer available"
    echo "  in Claude Code. No restart required."
else
    echo -e "${YELLOW}${BOLD}Completed with $ERRORS error(s). Some files may need manual cleanup.${NC}"
fi
echo ""
