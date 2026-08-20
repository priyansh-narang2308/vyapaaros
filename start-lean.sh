#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
PID_FILE="$ROOT_DIR/.service_pids"
ROOT_VENV="$ROOT_DIR/.venv"
UI_DIR="$ROOT_DIR/src/ui"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
info() { printf "${BOLD}[..]${NC} %s\n" "$1"; }

pkill -f uvicorn 2>/dev/null || true
pkill -f "pnpm start" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 1

rm -f "$PID_FILE"
mkdir -p "$LOG_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
    ok "Loaded .env"
fi

if [ ! -d "$ROOT_VENV" ]; then
    info "Setting up Python venv..."
    cd "$ROOT_DIR"
    uv venv --quiet 2>/dev/null || uv venv
    uv sync --quiet 2>/dev/null || uv sync
    ok "Venv ready"
fi

if [ ! -d "$UI_DIR/node_modules" ]; then
    info "Installing UI deps..."
    cd "$UI_DIR"
    pnpm install --frozen-lockfile 2>/dev/null || pnpm install
    ok "UI deps installed"
fi

cd "$ROOT_DIR"

info "Starting VyapaarOS (lean mode - no Docker)..."

"$ROOT_VENV/bin/uvicorn" src.merchant.main:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/merchant.log" 2>&1 &
echo "$!:merchant" >> "$PID_FILE"
ok "Merchant API (port 8000)"

"$ROOT_VENV/bin/uvicorn" src.payment.main:app --host 0.0.0.0 --port 8001 > "$LOG_DIR/psp.log" 2>&1 &
echo "$!:psp" >> "$PID_FILE"
ok "Payment Service (port 8001)"

"$ROOT_VENV/bin/uvicorn" src.apps_sdk.main:app --host 0.0.0.0 --port 2091 > "$LOG_DIR/apps-sdk.log" 2>&1 &
echo "$!:apps-sdk" >> "$PID_FILE"
ok "Apps SDK MCP (port 2091)"

cd "$UI_DIR"
pnpm start > "$LOG_DIR/ui.log" 2>&1 &
echo "$!:ui" >> "$PID_FILE"
ok "UI (port 3000)"
cd "$ROOT_DIR"

info "Waiting 10s for boot..."
sleep 10

printf "\n${BOLD}%-20s %-6s %-8s${NC}\n" "SERVICE" "PORT" "STATUS"
printf "%-20s %-6s %-8s\n" "-------" "----" "------"

for pair in "merchant:8000:/health" "psp:8001:/health" "apps-sdk:2091:/health" "ui:3000:/"; do
    IFS=: read -r name port path <<< "$pair"
    if curl -sf "http://localhost:$port$path" -o /dev/null --max-time 5 2>/dev/null; then
        printf "%-20s %-6s ${GREEN}%-8s${NC}\n" "$name" "$port" "OK"
    else
        printf "%-20s %-6s ${YELLOW}%-8s${NC}\n" "$name" "$port" "WAIT"
    fi
done

printf "\n${GREEN}${BOLD}VyapaarOS is running!${NC}\n"
printf "\n${BOLD}Access:${NC}\n"
echo "  Demo UI:       http://localhost:3000"
echo "  Merchant API:  http://localhost:8000/docs"
echo "  Apps SDK MCP:  http://localhost:2091/docs"
printf "\n${BOLD}Stop:${NC} ./stop.sh\n\n"
