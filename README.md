# vkbro-proxy — MLX KV Cache Proxy

A lightweight proxy layer that sits between any OpenAI-compatible agent and [MLX](https://github.com/ml-explore/mlx)-based inference servers (e.g. [OMLX](https://omlx.ai)). Maximizes KV cache hit rates through a three-block message architecture, dramatically reducing redundant computation during local LLM inference.

```
Your Agent → proxy(:8000) → MLX Server(:8001)
```

Designed for [Hermes Agent](https://github.com/NousResearch/Hermes), but **works with any agent that speaks OpenAI-compatible `/v1/chat/completions`**.

## Core Idea: Three-Block Architecture

| Block | Content | Behavior |
|---|---|---|
| 🔒 **Anchor** | system prompt + tool definitions | Permanently locked, SHA256 fingerprint verified |
| 💬 **Dialogue** | chat history + tool results | Stably accumulated, KV cache reused across turns |
| ⚡ **Ephemeral** | current-turn tool calls + orphaned systems | Rebuilt every turn, never pollutes the cache |

By isolating volatile content (tool loading, skill injection) from stable content (system prompts, conversation), the proxy keeps the KV cache prefix consistent — meaning most tokens skip recomputation.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Customize config
cp .env.example .env
# Edit .env to match your setup

# 3. Start your MLX inference server on localhost:8001
# 4. Start the proxy
python proxy.py

# 5. Point your agent to http://localhost:8000
```

Dashboard: http://localhost:8000

## Configuration

All parameters overrideable via `.env`. See `config.py` for defaults:

| Variable | Default | Description |
|---|---|---|
| `PROXY_PORT` | 8000 | Proxy listen port |
| `OMLX_URL` | `http://localhost:8001/v1/chat/completions` | MLX server endpoint |
| `OMLX_API_KEY` | `SK-OMLX-VKBRO` | MLX server API key |
| `PRE_FREEZE_DELAY` | 2 | Warm-up turns before anchor freeze |
| `AUTO_CHECKPOINT_BLOCKS` | 40 | Auto-compress when dialogue exceeds N blocks |
| `HIT_RATE_HEALTHY` | 0.7 | Cache health threshold |

## API

| Route | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI-compatible (proxy-optimized) |
| `/v1/models` | GET | Models proxied from MLX server |
| `/health` | GET | Cache health status |
| `/compress` | POST | Manually compress ephemeral blocks |
| `/uncompress` | POST | Undo compression |
| `/reset-anchor` | POST | Reset frozen anchor |
| `/checkpoint` | POST | Freeze old dialogue into summary |
| `/restart-omlx` | POST | Restart MLX server |
| `/shutdown` | POST | Shut down proxy |
| `/logs` | GET | Recent log entries |

## Custom Agent Integration

The proxy is **agent-agnostic** at the protocol level. Any system that sends OpenAI-compatible chat completion requests can use it. Here's how to adapt for your agent:

### 1. Generic OpenAI-compatible agent
If your agent already targets an OpenAI-compatible endpoint, just change the base URL:

```python
# Before: agent → MLX directly
client = OpenAI(base_url="http://localhost:8001/v1")

# After: agent → proxy → MLX
client = OpenAI(base_url="http://localhost:8000/v1")
```

### 2. Custom agent with tool calling
The proxy's three-block architecture is especially effective when your agent uses tool calling, since it isolates tool definitions (anchor block) and tool results (dialogue block) from transient tool invocations (ephemeral block):

```python
# Your agent sends tools normally — the proxy handles the rest
response = client.chat.completions.create(
    model="your-mlx-model",
    messages=[...],
    tools=[...],  # Automatically locked into anchor block
)
```

### 3. Skill injection support
If your agent injects skills/instructions mid-session (e.g. `skill_view` calls), the proxy automatically detects and isolates them in the ephemeral block, preventing cache invalidation.

### 4. Adapting the proxy to non-OMLX servers
By default the proxy targets OMLX, but you can point it at any OpenAI-compatible MLX or llama.cpp server:

```bash
# .env
OMLX_URL=http://localhost:8080/v1/chat/completions
OMLX_API_KEY=sk-your-key
```

The `model` auto-detection logic (route in `server.py`) can be customized in `_get_active_model()` to match your server's model discovery API.

### Architecture for extension
The codebase is modular — 6 files with clear boundaries:

| Module | Responsibility | Extend here if... |
|---|---|---|
| `config.py` | All constants + `.env` | You want new config options |
| `proxy_state.py` | Thread-safe singleton state | You need new state fields |
| `core.py` | Anchor, rebuild, health, checkpoint | You want different caching strategy |
| `server.py` | FastAPI routes + SSE streaming | You need new API endpoints |
| `dashboard.py` | HTML dashboard generation | You want custom UI |
| `proxy.py` | Entry point (~15 lines) | Rarely needs changes |

## Requirements

- Python ≥ 3.11
- FastAPI + uvicorn + httpx
- python-dotenv
- An MLX-based inference server (OMLX, MLX-LM server, etc.)

## Tests

```bash
pytest tests/ -v
# 68 tests, 0 failures
```

## License

MIT — see [LICENSE](LICENSE)

---

**Built for Hermes. Works with anything. Extend it for your agent.**
