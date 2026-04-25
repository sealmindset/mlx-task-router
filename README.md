# MLX Task Router

An intelligent proxy server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that reduces API costs by routing mundane CLI tasks to a local language model running on Apple Silicon via [MLX](https://github.com/ml-explore/mlx), while transparently forwarding complex tasks to the Anthropic API.

## Table of Contents

- [Problem](#problem)
- [Solution](#solution)
- [How It Works](#how-it-works)
  - [Architecture](#architecture)
  - [Routing Logic](#routing-logic)
  - [Why Claude Code Stays in Control](#why-claude-code-stays-in-control)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Development Install](#development-install)
  - [Global Install (Recommended)](#global-install-recommended)
- [Configuration](#configuration)
  - [Step 1: Initialize Config](#step-1-initialize-config)
  - [Step 2: Set Your API Key](#step-2-set-your-api-key)
  - [Step 3: Understand Where Keys Live](#step-3-understand-where-keys-live)
- [Usage](#usage)
  - [Starting the Router](#starting-the-router)
  - [Starting Claude Code](#starting-claude-code)
  - [Making It Permanent](#making-it-permanent)
  - [Running as a Background Service (launchd)](#running-as-a-background-service-launchd)
  - [Routing Overrides](#routing-overrides)
- [Gear Shifting](#gear-shifting)
  - [Built-in Gears](#built-in-gears)
  - [Switching Gears at Runtime](#switching-gears-at-runtime)
  - [Custom Models](#custom-models)
  - [Memory Considerations](#memory-considerations)
- [Cost Tracking](#cost-tracking)
- [Response Caching](#response-caching)
- [Health Watchdog](#health-watchdog)
- [Routing Feedback](#routing-feedback)
- [Fallback to Cloud](#fallback-to-cloud)
- [CLI Reference](#cli-reference)
- [API Endpoints](#api-endpoints)
- [Configuration Reference](#configuration-reference)
- [Architecture Deep Dive](#architecture-deep-dive)
  - [Request Flow](#request-flow)
  - [Tool Use Format Translation](#tool-use-format-translation)
  - [Streaming Support](#streaming-support)
  - [Project Structure](#project-structure)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Alternatives Considered](#alternatives-considered)
- [Contributing](#contributing)
- [License](#license)

---

## Problem

Claude Code is powerful, but every interaction — including trivial ones — costs API credits. Operations like `git commit`, `git push`, running linters, or creating pull requests don't require the reasoning capability of Claude Opus or Sonnet. They follow predictable patterns that any capable code-oriented language model can handle.

On a usage-based plan, these mundane tasks add up. You're paying premium prices for work that doesn't need premium intelligence.

## Solution

MLX Task Router sits between Claude Code and the Anthropic API as a transparent proxy. It inspects each request, classifies it, and routes it to the appropriate backend:

- **Mundane CLI tasks** (git, gh, npm, docker, make, etc.) go to a **free local model** running on your Mac's Apple Silicon via MLX.
- **Complex tasks** (explain code, refactor, debug, implement features, etc.) go to the **real Anthropic API** where Claude's full reasoning capability is needed.

You change nothing about how you use Claude Code. The routing is automatic and invisible.

## How It Works

### Architecture

```
+---------------+     HTTP      +--------------------------------+
|               |  (port 8888)  |       MLX Task Router          |
|  Claude Code  |-------------->|                                |
|               |<--------------|   +----------+                 |
+---------------+   Anthropic   |   |  Router  |                 |
                    API format  |   +----+-----+                 |
                                |        |                       |
                                |        +-- CLI task --> MLX    |
                                |        |               Model   |
                                |        |                       |
                                |        +-- Complex --> Proxy --+--> api.anthropic.com
                                +--------------------------------+
```

### Routing Logic

The router uses **confidence scoring** instead of binary pattern matching. Each signal contributes a weighted score; the request routes locally only when the total score meets the threshold (default 0.5).

**Hard overrides (bypass scoring):**

| Priority | Rule | Route |
|----------|------|-------|
| 1 | Message starts with `@cloud` | Forward to Anthropic |
| 2 | Message starts with `@local` | Handle locally |
| 3 | Local model not loaded or unhealthy | Forward to Anthropic |
| 4 | Estimated context exceeds `MAX_LOCAL_CONTEXT_TOKENS` | Forward to Anthropic |

**Confidence scoring (if no override applies):**

| Signal | Score | Example |
|--------|-------|---------|
| Executable detected as first word | +0.6 | `git status`, `docker ps` |
| Executable detected elsewhere | +0.3 | "check the npm logs" |
| CLI action phrase | +0.5 | "commit and push", "run the tests" |
| Short message (<80 chars) | +0.1 | Most CLI commands |
| Complexity pattern detected | -0.6 | "explain", "refactor", "debug" |
| Question mark present | -0.1 | "how does this work?" |
| Long message (>200 chars) | -0.1 | Detailed requests |
| Feedback penalty | -0.0 to -0.4 | Triggers that previously caused fallbacks |

**Executable detection** works by checking if words in the message exist in your system's `$PATH` using `shutil.which()`. No whitelist needed — any CLI tool installed on your machine is automatically recognized. Common English words that happen to be executables (like `time`, `sort`, `less`) are filtered out.

**Feedback loop**: the router tracks which triggers (e.g., `exec:git`) succeed or fail. If a trigger's failure rate exceeds 30% after 2+ attempts, a score penalty is applied automatically. This data persists across restarts in `~/.config/mlx-task-router/feedback.json`.

The default is always to forward to Anthropic. Local routing requires a positive score above the threshold, ensuring you never get a degraded experience on tasks that matter.

### Why Claude Code Stays in Control

This is a critical design point. Claude Code is the **orchestrator** — it executes all tools (Bash, Read, Write, Edit, etc.) on your machine. The API (whether Anthropic or a local model) only decides *which tools to call and with what arguments*. It returns structured `tool_use` blocks, and Claude Code runs them.

This means:
- The local model **never executes commands directly** on your machine
- Claude Code's **permission system** still applies to every tool call
- Claude Code **sees all results** and maintains full conversation context
- If the local model makes a bad tool call, Claude Code catches it through its normal validation

The local model is a cheaper brain making decisions; Claude Code is the hands doing the work.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Hardware** | Apple Silicon Mac (M1, M2, M3, M4 or later) |
| **RAM** | 16GB minimum; 64GB+ recommended for larger models |
| **macOS** | macOS 13 (Ventura) or later |
| **Python** | 3.11 or later |
| **Package manager** | [uv](https://docs.astral.sh/uv/) (recommended) or pip |
| **Claude Code** | Installed and working ([docs](https://docs.anthropic.com/en/docs/claude-code)) |
| **Anthropic API key** | For forwarding complex requests ([get one](https://console.anthropic.com/settings/keys)) |
| **Disk space** | 5–40GB depending on model (downloaded on first run, cached in `~/.cache/huggingface/`) |

## Installation

### Development Install

For active development on the router itself:

```bash
git clone https://github.com/sealmindset/mlx-task-router.git
cd mlx-task-router
uv venv
uv pip install -e .
source .venv/bin/activate
```

### Global Install (Recommended)

Installs the `mlx-router` command globally so it's available from any directory:

```bash
# Using uv (recommended)
uv tool install ~/Documents/GitHub/mlx-task-router

# Or using pipx
pipx install ~/Documents/GitHub/mlx-task-router
```

After installation, verify:
```bash
mlx-router --version
```

## Configuration

### Step 1: Initialize Config

```bash
mlx-router init
```

This creates `~/.config/mlx-task-router/.env` with default settings.

### Step 2: Set Your API Key

Edit the config file with your Anthropic API key:

```bash
nano ~/.config/mlx-task-router/.env
```

At minimum, set:
```bash
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
```

### Step 3: Understand Where Keys Live

The router and Claude Code each need specific environment variables. Here's what goes where:

| Variable | Where to Set | Purpose |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | `~/.config/mlx-task-router/.env` | Used by the router to forward complex requests to Anthropic's API. This is your real API key. |
| `ANTHROPIC_API_KEY` | Shell environment (for Claude Code) | Claude Code requires this to start. When using the router, it can be any non-empty value (e.g., `sk-placeholder`) since the router injects the real key. Or use your real key — the router will prefer its own if configured. |
| `ANTHROPIC_BASE_URL` | Shell environment (for Claude Code) | Set to `http://localhost:8888` to redirect Claude Code's requests to the router. |

**Config file search order:**
1. `~/.config/mlx-task-router/.env` (recommended — works globally)
2. `.env` in the current working directory (useful for development)
3. Shell environment variables (always take precedence over .env files)

## Usage

### Starting the Router

```bash
mlx-router serve
```

On first run, the default model (Qwen3-Coder-30B-A3B-Instruct-4bit, ~17GB) is downloaded from Hugging Face. This happens once; subsequent starts load from the local cache at `~/.cache/huggingface/hub/`.

You'll see:
```
MLX Task Router v0.1.0
  Listening on 0.0.0.0:8888
  Default gear: sport
  Upstream API: https://api.anthropic.com

Configure Claude Code:
  export ANTHROPIC_BASE_URL=http://localhost:8888

[gear] Loading sport: mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
[gear] sport loaded in 12.3s
```

### Starting Claude Code

In a **separate terminal**:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8888
claude
```

That's it. Claude Code now sends all requests through the router. Use it exactly as you normally would — the routing is transparent.

### Making It Permanent

To avoid setting the environment variable every time you open a terminal, add it to your shell profile:

```bash
echo '' >> ~/.zshrc
echo '# MLX Task Router — route Claude Code through local proxy' >> ~/.zshrc
echo 'export ANTHROPIC_BASE_URL=http://localhost:8888' >> ~/.zshrc
source ~/.zshrc
```

Or for bash users:
```bash
echo '' >> ~/.bashrc
echo '# MLX Task Router — route Claude Code through local proxy' >> ~/.bashrc
echo 'export ANTHROPIC_BASE_URL=http://localhost:8888' >> ~/.bashrc
source ~/.bashrc
```

After this, every new terminal session and every reboot will have `ANTHROPIC_BASE_URL` set automatically. Claude Code will always route through the proxy.

**Verify it's set:**
```bash
echo $ANTHROPIC_BASE_URL
# Should print: http://localhost:8888
```

**To temporarily bypass the router** (talk directly to Anthropic):
```bash
unset ANTHROPIC_BASE_URL
claude
```

**Note:** When the router isn't running, Claude Code requests will fail with a connection error. If you've installed the [launchd service](#running-as-a-background-service-launchd), the router starts automatically on login so this is rarely an issue. If you haven't, start the router before using Claude Code.

### Running as a Background Service (launchd)

Instead of manually starting the router in a terminal, you can run it as a persistent macOS service that starts automatically on login and restarts on crash.

**Install the launchd agent:**

Create the file `~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sealmindset.mlx-task-router</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/.local/bin/mlx-router</string>
        <string>serve</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/.config/mlx-task-router/mlx-router.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/.config/mlx-task-router/mlx-router.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/YOUR_USERNAME/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
```

Replace `YOUR_USERNAME` with your macOS username (run `whoami` to check). If you installed `mlx-router` via a different method, update the path in `ProgramArguments` accordingly (run `which mlx-router` to find it).

**Load and start the service:**

```bash
launchctl load ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
```

**Service management commands:**

| Action | Command |
|--------|---------|
| Start service | `launchctl load ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist` |
| Stop service | `launchctl unload ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist` |
| Check if running | `launchctl list \| grep mlx-task-router` |
| View logs | `tail -f ~/.config/mlx-task-router/mlx-router.log` |
| Health check | `curl -s http://localhost:8888/health` |

**Behavior:**
- **Starts on login** — the router is ready before you open a terminal
- **Auto-restarts on crash** — if the process exits unexpectedly, launchd restarts it after a 10-second throttle
- **Logs to file** — stdout and stderr go to `~/.config/mlx-task-router/mlx-router.log` instead of a terminal
- **Survives terminal closure** — unlike a background `&` process, launchd-managed services persist across terminal sessions

**To disable auto-start** (but keep the plist for manual use):

```bash
launchctl unload ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
```

**To re-enable:**

```bash
launchctl load ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
```

**To remove completely:**

```bash
launchctl unload ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
rm ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
```

### Routing Overrides

You can force routing on any individual message by prefixing it inside Claude Code:

| Prefix | Effect | Example |
|--------|--------|---------|
| `@cloud` | Always forward to Anthropic | `@cloud explain this function` |
| `@local` | Always handle locally | `@local run the full test suite` |

The prefix is stripped before the request is processed — the model never sees it.

## Gear Shifting

Switch between model profiles at runtime without restarting the server. Think of it like gear ratios in a sports car — shift up for power, down for speed.

### Built-in Gears

| Gear | Model | Size | Active Params | Best For |
|------|-------|------|---------------|----------|
| **eco** | `Qwen2.5-Coder-7B-Instruct-4bit` | ~4GB | 7B | Fast, simple code tasks (git status, npm install) |
| **sport** | `Qwen3-Coder-30B-A3B-Instruct-4bit` | ~17GB | ~3B (MoE) | Balanced — default for most CLI work |
| **track** | `Qwen3-Coder-Next-4bit` | ~45GB | ~3B (MoE) | Complex local tasks needing strong code understanding |

**sport** is the default. It uses a Mixture-of-Experts architecture — 30B total parameters but only ~3B active per token, giving you large-model knowledge with small-model speed.

### Switching Gears at Runtime

```bash
# See what's available and what's active
curl -s http://localhost:8888/gears | python3 -m json.tool

# Shift to track for heavy lifting
curl -s -X POST http://localhost:8888/gear/track

# Shift back to sport for everyday use
curl -s -X POST http://localhost:8888/gear/sport

# Check current gear
curl -s http://localhost:8888/gear
```

During a gear switch:
1. The current model is unloaded from memory
2. The new model is loaded (10–60 seconds depending on size)
3. Any requests arriving during the switch are **automatically forwarded to Anthropic** — zero downtime

### Custom Models

Override any gear's model via environment variable:

```bash
# In ~/.config/mlx-task-router/.env
GEAR_ECO_MODEL=mlx-community/Qwen2.5-Coder-7B-Instruct-4bit
GEAR_SPORT_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
GEAR_TRACK_MODEL=mlx-community/Codestral-22B-v0.1-4bit
```

Any model from the [MLX Community on Hugging Face](https://huggingface.co/mlx-community) works. Choose models that support function calling / tool use for best results.

### Memory Considerations

Approximate memory usage by model size (4-bit quantization):

| Model Size | RAM Usage | Recommended Mac RAM |
|------------|-----------|-------------------|
| 4B–8B | 3–5GB | 16GB+ |
| 14B–22B | 8–13GB | 32GB+ |
| 30B–32B | 17–19GB | 64GB+ |
| 70B+ | 38–42GB | 128GB |

Leave at least 8–10GB free for macOS, Claude Code, and other applications. On a 128GB M4 Max, you can comfortably run any model up to 70B while using other MLX services simultaneously.

## Cost Tracking

Every request is tracked with token counts and estimated cost savings. Stats persist to disk and survive restarts.

**Check your savings:**
```bash
curl -s http://localhost:8888/stats | python3 -m json.tool
```

**Example output:**
```json
{
    "requests_total": 847,
    "requests_local": 312,
    "requests_forwarded": 535,
    "tokens_local_input": 156000,
    "tokens_local_output": 23400,
    "tokens_forwarded_input": 1240000,
    "tokens_forwarded_output": 384000,
    "cost_saved_usd": 0.819,
    "started_at": "2025-04-19T12:00:00Z",
    "last_reset": "2025-04-19T12:00:00Z",
    "local_percentage": 36.8,
    "cost_saved_display": "$0.8190",
    "pricing_tier": "sonnet"
}
```

**How cost savings are calculated:**

Each locally-routed request would have cost money if sent to Anthropic. The router calculates what you *would have paid* based on the token count and Anthropic's pricing:

| Model Tier | Input (per MTok) | Output (per MTok) |
|------------|-----------------|-------------------|
| Sonnet (default) | $3.00 | $15.00 |
| Opus | $15.00 | $75.00 |
| Haiku | $0.25 | $1.25 |

The tier is auto-detected from the model name in the request (Claude Code sends the model it thinks it's talking to).

**Quick summary** is also shown on the root endpoint:
```bash
curl -s http://localhost:8888/
```

**Reset counters:**
```bash
curl -s -X POST http://localhost:8888/stats/reset
```

Stats are persisted to `~/.config/mlx-task-router/stats.json` every 30 seconds.

## Response Caching

Locally-routed requests are cached to avoid regenerating identical responses. If the same message is sent within the TTL window, the cached response is returned instantly.

```bash
# Check cache stats
curl -s http://localhost:8888/cache | python3 -m json.tool

# Clear the cache
curl -s -X POST http://localhost:8888/cache/clear
```

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_TTL` | `60` | Cache time-to-live in seconds |
| `CACHE_MAX_ENTRIES` | `100` | Maximum cached responses |

Cache keys are based on the latest user message text and the list of available tool names. Only locally-routed responses are cached — forwarded responses are never cached.

## Health Watchdog

A background watchdog monitors the local model's health every 30 seconds. If the model becomes unresponsive, the watchdog:

1. Marks the model as **unhealthy** after 3 consecutive failures
2. **All requests forward to Anthropic** automatically (zero downtime)
3. Attempts **auto-recovery** by reloading the current gear
4. If recovery succeeds, resumes local routing

```bash
# Check watchdog status
curl -s http://localhost:8888/watchdog | python3 -m json.tool

# Health endpoint now shows model health
curl -s http://localhost:8888/health
# Returns "degraded" status if model is unhealthy
```

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHDOG_INTERVAL` | `30` | Seconds between health checks |
| `WATCHDOG_MAX_FAILURES` | `3` | Consecutive failures before marking unhealthy |

## Routing Feedback

The router learns from its own mistakes. Every locally-routed request tracks whether the trigger (e.g., `exec:git`) succeeded or triggered a fallback to the cloud.

```bash
# View trigger reliability stats
curl -s http://localhost:8888/feedback | python3 -m json.tool

# Reset feedback data
curl -s -X POST http://localhost:8888/feedback/reset
```

**Example output:**
```json
{
  "exec:git": {
    "attempts": 45,
    "failures": 1,
    "failure_rate": "2%",
    "penalty": 0.0
  },
  "exec:quota": {
    "attempts": 3,
    "failures": 2,
    "failure_rate": "67%",
    "penalty": -0.27
  }
}
```

Penalties kick in after 2+ attempts with >30% failure rate, scaling up to -0.4 at 100% failure. Data persists in `~/.config/mlx-task-router/feedback.json`.

## Fallback to Cloud

If the local model throws an exception during generation, the router automatically retries the request via the Anthropic API. This is transparent — Claude Code never sees the failure.

The fallback is logged:
```
[fallback] Local generation failed: <error>
[fallback] Retrying via Anthropic API
[feedback] Recorded failure for trigger 'exec:git'
```

For streaming requests, the response is fully buffered before sending to the client, so failures are caught before any partial response is sent.

## CLI Reference

```
mlx-router [command] [options]

Commands:
  serve     Start the proxy server (default if no command given)
  init      Create config directory (~/.config/mlx-task-router/.env)
  gears     List available gear profiles

Options for 'serve':
  --host TEXT     Bind address (default: 0.0.0.0)
  --port INT      Bind port (default: 8888)
  --gear TEXT     Initial gear: eco, sport, or track (default: sport)
  --version       Show version and exit
  --help          Show help and exit
```

**Examples:**

```bash
# Start with defaults
mlx-router

# Start on a different port with eco gear
mlx-router serve --port 9000 --gear eco

# Initialize config for first-time setup
mlx-router init

# Check what gears are available
mlx-router gears
```

## API Endpoints

### Core (Claude Code compatible)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/messages` | Messages endpoint — automatically routed based on content |
| `POST` | `/v1/messages/count_tokens` | Token counting (uses local tokenizer if model loaded, otherwise forwards) |

### Gear Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/gears` | List all gear profiles with active status |
| `GET` | `/gear` | Current active gear and loading state |
| `POST` | `/gear/{name}` | Switch to a different gear (eco, sport, track) |

### Cost Tracking

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats` | Full statistics — requests, tokens, cost saved, local percentage |
| `POST` | `/stats/reset` | Reset all counters to zero |

### Caching

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/cache` | Cache stats — hits, misses, hit rate, entries, TTL |
| `POST` | `/cache/clear` | Flush all cached responses |

### Routing Feedback

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/feedback` | Per-trigger reliability stats and penalties |
| `POST` | `/feedback/reset` | Clear all feedback data |

### Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check — model status, model health, current gear, loading state |
| `GET` | `/watchdog` | Watchdog status — healthy, recovering, failures, last error |
| `GET` | `/` | Server info — version, model status, active gear, cost saved |

## Configuration Reference

All settings are configured via `~/.config/mlx-task-router/.env` or as environment variables. Environment variables always take precedence over `.env` file values.

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address. Use `127.0.0.1` to restrict to localhost only. |
| `PORT` | `8888` | Server port. Must match the port in `ANTHROPIC_BASE_URL`. |

### API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key for forwarding complex requests. |
| `ANTHROPIC_API_URL` | `https://api.anthropic.com` | Upstream API URL. Change if using a custom gateway or proxy chain. |

### Model Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_GEAR` | `sport` | Gear profile to load on startup (`eco`, `sport`, or `track`). |
| `GEAR_ECO_MODEL` | `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` | Model for the eco gear. |
| `GEAR_SPORT_MODEL` | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` | Model for the sport gear. |
| `GEAR_TRACK_MODEL` | `mlx-community/Qwen3-Coder-Next-4bit` | Model for the track gear. |

### Routing Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_LOCAL_CONTEXT_TOKENS` | `32000` | Requests with estimated context above this are forwarded to Anthropic. Prevents slow local inference on large conversations. |
| `ROUTING_THRESHOLD` | `0.5` | Minimum confidence score to route locally. Lower = more local routing, higher = more conservative. |
| `LOG_ROUTING` | `true` | Print routing decisions to stdout for debugging. |

### Cache Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_TTL` | `60` | Seconds before cached responses expire. |
| `CACHE_MAX_ENTRIES` | `100` | Maximum number of cached responses. Oldest evicted first. |

### Watchdog Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHDOG_INTERVAL` | `30` | Seconds between model health checks. |
| `WATCHDOG_MAX_FAILURES` | `3` | Consecutive failures before marking model unhealthy. |

## Architecture Deep Dive

### Request Flow

1. Claude Code sends an HTTP request to `localhost:8888/v1/messages` in the standard Anthropic Messages API format, including system prompts, conversation history, tool definitions, and the latest user message.

2. The **router** extracts the latest user message text and classifies the request:
   - Override prefix detection (`@cloud`, `@local`)
   - Watchdog health check (is the local model responsive?)
   - Context length estimation (character count / 4 as a token approximation)
   - Confidence scoring: executable detection via `$PATH`, action phrase matching, complexity signals, message length, and feedback penalties

3. **If routed locally:**
   - The system prompt is replaced with a concise, task-focused prompt (the original Claude Code system prompt can be 10K+ tokens — too large for efficient local inference)
   - Tool definitions are converted from Anthropic format (`input_schema`) to OpenAI format (`parameters`) for model compatibility
   - Conversation messages are converted (Anthropic `tool_use`/`tool_result` blocks → OpenAI `tool_calls`/`tool` messages)
   - The model's chat template is applied with tool definitions
   - MLX generates the response
   - `<think>` blocks are stripped (Qwen3's built-in thinking, disabled by default but stripped as a safeguard)
   - `<tool_call>` blocks are parsed and converted to Anthropic `tool_use` content blocks
   - The response is formatted as a valid Anthropic Messages API response

4. **If forwarded:**
   - The original request is forwarded to `api.anthropic.com` (or configured upstream) with proper authentication headers
   - For streaming requests, SSE events are passed through byte-for-byte
   - For non-streaming requests, the JSON response is forwarded as-is

### Tool Use Format Translation

The router translates between two tool-calling conventions:

**Anthropic format** (what Claude Code speaks):
```json
{
  "content": [
    {"type": "text", "text": "I'll check the status."},
    {"type": "tool_use", "id": "toolu_abc123", "name": "Bash",
     "input": {"command": "git status"}}
  ],
  "stop_reason": "tool_use"
}
```

**Qwen/OpenAI format** (what local models produce):
```
I'll check the status.
<tool_call>
{"name": "Bash", "arguments": {"command": "git status"}}
</tool_call>
```

The `tool_format.py` module handles bidirectional conversion between these formats, including multi-tool calls and tool result messages.

### Streaming Support

Both routing paths support streaming via Server-Sent Events (SSE), which is Claude Code's default mode:

- **Forwarded requests:** Raw bytes are streamed through from the upstream API with zero processing — the proxy is transparent.
- **Local requests:** The model generates the full response (buffered), then emits SSE events in the exact Anthropic format: `message_start` → `content_block_start` → `content_block_delta` (text or `input_json_delta`) → `content_block_stop` → `message_delta` → `message_stop`.

Buffering local responses before streaming is a deliberate design choice — it enables reliable parsing of tool calls, which can appear anywhere in the model's output. Real-time streaming of local generation is a planned optimization.

### Project Structure

```
mlx-task-router/
├── pyproject.toml                    # Package metadata, dependencies, CLI entry point
├── .env.example                      # Example configuration
├── .gitignore                        # Excludes .env, __pycache__, dist, etc.
├── LICENSE                           # Apache License 2.0
├── README.md                         # This file
├── TODO.md                           # Planned enhancements
└── src/
    └── mlx_task_router/
        ├── __init__.py               # Package version
        ├── __main__.py               # python -m mlx_task_router support
        ├── cache.py                  # Response cache for local requests
        ├── cli.py                    # CLI entry point (mlx-router command)
        ├── config.py                 # Configuration loading, gear profiles
        ├── feedback.py               # Routing feedback loop (trigger reliability)
        ├── local.py                  # MLX model manager, generation, gear switching
        ├── models.py                 # Pydantic models (Anthropic API format)
        ├── proxy.py                  # Async HTTP passthrough to Anthropic
        ├── router.py                 # Confidence-scored request classification
        ├── server.py                 # FastAPI application, endpoint handlers
        ├── stats.py                  # Cost tracking and token statistics
        ├── tool_format.py            # Anthropic ↔ OpenAI tool format conversion
        └── watchdog.py               # Model health monitoring and auto-recovery

Runtime files (created after setup):
~/.config/mlx-task-router/
├── .env                              # Your configuration (API key, gear, port)
├── feedback.json                     # Routing feedback data (auto-managed)
├── stats.json                        # Persistent cost/token statistics
└── mlx-router.log                    # Service logs (when running via launchd)

~/Library/LaunchAgents/
└── com.sealmindset.mlx-task-router.plist  # macOS service definition (optional)
```

## Security Considerations

### API Key Protection

- Your Anthropic API key is stored in `~/.config/mlx-task-router/.env` with standard file permissions. Ensure this file is not world-readable: `chmod 600 ~/.config/mlx-task-router/.env`.
- The `.gitignore` excludes `.env` files to prevent accidental commits.
- The router can use keys from the incoming request headers (sent by Claude Code) as a fallback, but its own configured key takes precedence.

### Network Exposure

- By default, the server binds to `0.0.0.0` (all interfaces). On a shared network, set `HOST=127.0.0.1` in your config to restrict to localhost only.
- The router accepts any `x-api-key` header value — it does not authenticate incoming requests. This is by design (it's a local development tool), but don't expose it to the public internet.
- When forwarding to Anthropic, only a whitelist of headers is passed through (`content-type`, `anthropic-version`, `anthropic-beta`, `x-api-key`).

### Model Safety

- The local model **never executes commands** — it only generates `tool_use` responses. Claude Code's permission system governs actual tool execution.
- The simplified local system prompt does not include Claude Code's full safety instructions. For highly sensitive operations, use the `@cloud` prefix to route to the real API.
- Model outputs are parsed with strict JSON validation. Malformed tool calls are silently dropped.

### Data Privacy

- Requests routed locally never leave your machine. Your code, prompts, and conversation history stay entirely on your Mac.
- Requests routed to Anthropic follow Anthropic's standard data handling policies.
- The router does not log message content — only routing decisions (route type, reason, gear name).

## Troubleshooting

### Router won't start

**"No module named 'mlx_task_router'"**
The package isn't installed. Run `uv tool install .` or `uv pip install -e .` from the project directory.

**"Address already in use"**
Another process is using port 8888. Either stop it (`lsof -i :8888`) or change the port: `mlx-router serve --port 9000`.

### Model download fails

**"Rate limited" or slow downloads**
Set a Hugging Face token for faster downloads:
```bash
export HF_TOKEN=hf_your_token_here
mlx-router serve
```
Get a free token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

**Download interrupted**
Re-run `mlx-router serve`. The Hugging Face cache handles partial downloads gracefully.

### Claude Code can't connect

**"Connection refused"**
The router isn't running. Start it in a separate terminal first.

**"Unexpected response format"**
Check that `ANTHROPIC_BASE_URL` is set to `http://localhost:8888` (not `https`).

### Local model returns bad tool calls

The model generates text instead of structured tool calls, or calls non-existent tools.

- Try switching to a more capable gear: `curl -X POST http://localhost:8888/gear/track`
- Use `@cloud` for that specific request to fall back to Anthropic
- Some models handle function calling better than others. The default Qwen3 models are chosen for strong tool-use performance.

### Forwarded requests fail with 401

Your Anthropic API key is missing or invalid. Check `~/.config/mlx-task-router/.env` and verify the key at [console.anthropic.com](https://console.anthropic.com).

### High memory usage

Check which gear is active: `curl http://localhost:8888/gear`. Switch to a smaller gear: `curl -X POST http://localhost:8888/gear/eco`. Monitor memory with `Activity Monitor` or `htop`.

### Requests are slow

Local inference speed depends on model size and available memory bandwidth. If local requests are too slow:
- Switch to `eco` gear for faster inference
- Increase `MAX_LOCAL_CONTEXT_TOKENS` threshold to send more requests to Anthropic
- Ensure no other heavy processes are competing for memory bandwidth

### launchd service won't start

**Check service status:**
```bash
launchctl list | grep mlx-task-router
```
The first column is the PID (or `-` if not running), the second is the last exit code (0 = success).

**Check logs:**
```bash
tail -50 ~/.config/mlx-task-router/mlx-router.log
```

**Common issues:**
- **Exit code 127:** The `mlx-router` binary path in the plist is wrong. Run `which mlx-router` and update `ProgramArguments`.
- **Exit code 1:** Configuration error or missing dependencies. Check the log for details.
- **Port conflict:** Another process is using port 8888. The service will crash and retry every 10 seconds. Stop the conflicting process or change the port in your `.env`.

**Reload after editing the plist:**
```bash
launchctl unload ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
launchctl load ~/Library/LaunchAgents/com.sealmindset.mlx-task-router.plist
```

## Limitations

- **Apple Silicon only.** MLX requires Apple Silicon (M1+). This project does not work on Intel Macs or Linux/Windows.
- **Tool-use reliability varies by model.** Local models may occasionally produce malformed tool calls or choose suboptimal tools. The default gears are selected for strong function-calling performance, but they are not Claude.
- **No real-time streaming for local generation.** Local responses are buffered before being streamed as SSE events. You won't see token-by-token output from the local model — the full response arrives at once, then streams to Claude Code. This is planned for a future release.
- **Simplified system prompt for local routing.** The local model receives a short task-focused prompt instead of Claude Code's full system prompt. This improves local performance but means the model doesn't have all of Claude Code's behavioral guidelines.
- **Routing is heuristic.** The router uses confidence scoring based on `$PATH` executable detection and regex patterns, not semantic understanding. Edge cases exist — the feedback loop helps mitigate these over time.
- **Single model at a time.** Only one gear (model) is loaded in memory. Gear switches require unloading and reloading, which takes 10–60 seconds.
- **No conversation state tracking.** The router doesn't track which model handled which turn. Each request is classified independently. This works because Claude Code sends the full conversation in every request.

## Alternatives Considered

| Alternative | Why We Didn't Use It |
|-------------|---------------------|
| **LM Studio** | Serves an OpenAI-compatible API, not Anthropic format. Claude Code can't talk to it directly. Also lacks smart routing — it's all-local or nothing. |
| **Ollama** | Same issue — OpenAI API format only. No routing intelligence. |
| **Claude Code with cheaper models** | Claude Code already supports model selection, but even the cheapest Anthropic model costs more than free local inference for trivial tasks. |
| **Shell scripts / git hooks** | Handles specific workflows but not general-purpose. Can't handle "stage these files, write a commit message based on the diff, commit, and push" as a single natural-language request. |
| **Custom Claude Code hooks** | Hooks run pre/post tool execution but can't reroute the API call itself. |

## Contributing

Contributions are welcome. Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Submit a pull request

Areas where help is especially welcome:
- Real-time streaming for local generation
- Support for additional model families and their tool-calling formats
- Benchmarking local model performance on common Claude Code workflows
- A TUI or web dashboard for gear management and routing statistics
- Per-session routing analytics

## License

Copyright 2025 sealmindset

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

This project uses open-source ML models from the [MLX Community](https://huggingface.co/mlx-community), each with their own licenses. The default Qwen models are licensed under Apache 2.0 by Alibaba Group.
