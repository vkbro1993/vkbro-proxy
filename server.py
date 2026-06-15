"""
Vkbro-MLX Agent Cache Proxy v2.7.0 — FastAPI 路由 + SSE 代理
===========================================================
所有路由处理、上游模型发现、SSE 流式代理。
create_app() 工厂函数返回配置好的 FastAPI 实例。
"""

import json
import os
import signal
import subprocess
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse

try:
    from .config import (
        OMLX_URL,
        OMLX_API_KEY,
        OMLX_HEADERS,
        PROXY_HOST,
        PROXY_PORT,
        HTTP_TIMEOUT,
        MAX_KEEPALIVE,
        ADMIN_TIMEOUT,
    )
    from .proxy_state import ProxyState
    from .core import (
        _build_tools_message,
        _poll_mlx_status,
        extract_cached_tokens,
        parse_usage_from_sse,
        rebuild_messages,
        update_cache_health,
        validate_or_freeze_anchor,
    )
    from .dashboard import render_dashboard
except ImportError:
    from config import (  # type: ignore[no-redef]
        OMLX_URL,
        OMLX_API_KEY,
        OMLX_HEADERS,
        PROXY_HOST,
        PROXY_PORT,
        HTTP_TIMEOUT,
        MAX_KEEPALIVE,
        ADMIN_TIMEOUT,
    )
    from proxy_state import ProxyState  # type: ignore[no-redef]
    from core import (  # type: ignore[no-redef]
        _build_tools_message,
        _poll_mlx_status,
        extract_cached_tokens,
        parse_usage_from_sse,
        rebuild_messages,
        update_cache_health,
        validate_or_freeze_anchor,
    )
    from dashboard import render_dashboard  # type: ignore[no-redef]


# ── OMLX 连接辅助 ──────────────────────────────────────────────────

OMLX_ADMIN_URL = "http://localhost:8001/admin/api/stats"
OMLX_MODELS_URL = "http://localhost:8001/v1/models"


async def _get_omlx_models(client: httpx.AsyncClient) -> list[dict]:
    """获取 OMLX 模型列表。"""
    try:
        r = await client.get(OMLX_MODELS_URL, headers=OMLX_HEADERS)
        return r.json().get("data", [])
    except Exception:
        return []


async def _get_active_model(client: httpx.AsyncClient, state: ProxyState) -> str:
    """获取当前活跃的模型名称。"""
    models = await _get_omlx_models(client)
    valid = [m["id"] for m in models if m["id"] != "MarkItDown"]
    if valid:
        state._last_model_cache["model"] = valid[0]
        state._last_model_cache["time"] = time.time()
        return valid[0]
    return state._last_model_cache["model"]


async def _resolve_model(
    client: httpx.AsyncClient, state: ProxyState, model_name: str
) -> str:
    """解析模型名：如果传入 "omlx"，则动态发现实际模型。"""
    if model_name == "omlx":
        try:
            r = await client.get(OMLX_ADMIN_URL, headers=OMLX_HEADERS)
            stats = r.json()
            active = stats.get("active_models", {}).get("models", [])
            if active:
                return active[0]["id"]
            else:
                r2 = await client.get(OMLX_MODELS_URL, headers=OMLX_HEADERS)
                all_models = r2.json().get("data", [])
                valid = [
                    m["id"]
                    for m in all_models
                    if m["id"] != "MarkItDown" and "Embedding" not in m["id"]
                ]
                return valid[0] if valid else "Qwen3.6-35B-A3B-MLX-6bit"
        except Exception:
            return "Qwen3.6-35B-A3B-MLX-6bit"
    return model_name


async def _get_omlx_stats(client: httpx.AsyncClient) -> Optional[dict]:
    """获取 OMLX 管理统计信息，失败返回 None。"""
    try:
        r = await client.get(OMLX_ADMIN_URL, headers=OMLX_HEADERS)
        return r.json()
    except Exception:
        return None


# ── 应用工厂 ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(title="Vkbro-MLX Agent Cache Proxy v2.7.0")
    state = ProxyState()
    client = httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        limits=httpx.Limits(max_keepalive_connections=MAX_KEEPALIVE),
    )

    # ── /v1/chat/completions ────────────────────────────────────────

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await request.json()
        messages: list[dict] = body.get("messages", [])
        tools: list[dict] = body.get("tools", [])

        tools_msg: Optional[dict] = None
        if tools:
            if state.frozen_tools_hash and state._frozen_tools_msg:
                tools_msg = dict(state._frozen_tools_msg)
            else:
                tools_msg = _build_tools_message(tools)
            if tools_msg:
                messages = [tools_msg] + messages

        roles = [m.get("role", "?") for m in messages[:5]]
        lengths = [len(json.dumps(m, ensure_ascii=False)) for m in messages[:5]]
        state.log(f"[DEBUG] 收到{len(messages)}条消息, 角色:{roles}, 长度:{lengths}")
        validate_or_freeze_anchor(state, messages, tools, tools_msg)
        rebuilt = rebuild_messages(state, messages)
        payload = {**body, "messages": rebuilt}
        model_name = payload.get("model", "")
        payload["model"] = await _resolve_model(client, state, model_name)
        for m in payload["messages"]:
            for k in (
                "ephemeral",
                "source_round",
                "compressed_from_rounds",
                "orphaned",
                "checkpoint",
            ):
                m.pop(k, None)

        if not body.get("stream", False):
            try:
                resp = await client.post(OMLX_URL, json=payload, headers=OMLX_HEADERS)
                data = resp.json()
                u = data.get("usage", {})
                update_cache_health(
                    state, extract_cached_tokens(u), u.get("prompt_tokens", 0)
                )
                state.total_turns += 1
                return data
            except Exception as e:
                return JSONResponse(
                    status_code=502, content={"error": {"message": str(e)}}
                )

        return StreamingResponse(
            _handle_stream(state, client, payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── SSE 流 ───────────────────────────────────────────────────────

    async def _handle_stream(
        state: ProxyState,
        client: httpx.AsyncClient,
        payload: dict,
    ):
        """SSE 流式代理 — 尾缓冲解析 usage，不缓存全量响应。"""
        # 只保留最后 N 字节做 usage 解析，O(1) 内存
        TAIL_BUFFER = 8192  # 最后 8KB 足够覆盖任意 usage 块
        tail_buf: bytes = b""
        async with client.stream(
            "POST", OMLX_URL, json=payload, headers=OMLX_HEADERS
        ) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk
                tail_buf += chunk
                if len(tail_buf) > TAIL_BUFFER:
                    tail_buf = tail_buf[-TAIL_BUFFER:]
        u = parse_usage_from_sse([tail_buf])
        p, c = _poll_mlx_status(state)
        update_cache_health(
            state,
            c if c > 0 else extract_cached_tokens(u),
            u.get("prompt_tokens", p) if u else p,
        )
        state.total_turns += 1

    # ── /v1/models ───────────────────────────────────────────────────

    @app.get("/v1/models")
    async def list_models():
        try:
            r = await client.get(OMLX_MODELS_URL, headers=OMLX_HEADERS)
            return r.json()
        except Exception:
            return {"object": "list", "data": []}

    # ── /health ──────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        wa = (
            sum(state.hit_rate_window) / len(state.hit_rate_window)
            if state.hit_rate_window
            else 0
        )
        return {
            "ok": True,
            "cache_state": state.cache_state,
            "window_avg_hit_rate": round(wa, 4),
            "frozen_prefix_hash": (state.frozen_prefix_hash or "")[:12],
            "frozen_token_count": state.frozen_token_count,
            "ephemeral_count": len(state.ephemeral_buffer),
            "frozen_tools_count": state.frozen_tools_count,
            "total_turns": state.total_turns,
            "compressed": state.ephemeral_compressed,
            "last_request": {
                "prompt_tokens": state.last_prompt_tokens,
                "cached_tokens": state.last_cached_tokens,
                "net_prefill": state.last_prompt_tokens - state.last_cached_tokens,
            },
        }

    # ── /compress /uncompress ────────────────────────────────────────

    @app.post("/compress")
    async def compress():
        if state.ephemeral_compressed:
            return {"ok": True, "compressed": True}
        state.ephemeral_compressed = True
        state.ephemeral_buffer = [
            {
                "role": "system",
                "content": f"[压缩-第{state.total_turns}轮] 工具结果已压缩",
                "ephemeral": True,
            }
        ]
        return {"ok": True, "compressed": True}

    @app.post("/uncompress")
    async def uncompress():
        state.ephemeral_compressed = False
        state.ephemeral_buffer = []
        return {"ok": True, "compressed": False}

    # ── /reset-anchor ────────────────────────────────────────────────

    @app.post("/reset-anchor")
    async def reset_anchor():
        state.reset_anchor()
        return {"ok": True}

    # ── /checkpoint ──────────────────────────────────────────────────

    @app.post("/checkpoint")
    async def checkpoint():
        if state.frozen_system_msgs is None:
            return {"ok": False, "message": "锚点未冻结"}
        frozen_blocks = state.frozen_token_count // 2048
        stable_idx = state._stable_indexed_blocks
        dialogue_blocks = stable_idx - frozen_blocks
        if dialogue_blocks < 10:
            return {"ok": False, "message": f"对话块仅{dialogue_blocks}块，太少"}

        count = state.inc_checkpoint()
        cut_blocks = int(dialogue_blocks * 0.6)
        keep_blocks = dialogue_blocks - cut_blocks
        cut_tokens = cut_blocks * 2048

        state._cut_before_turn = state.total_turns
        state._cut_summary = (
            f"[检查点#{count}] 冻结前{cut_blocks}块对话(约{cut_tokens:,}tokens)，"
            f"保留最近{keep_blocks}块。"
        )
        state.log(
            f"[检查点] 手动#{count} — 真砍{cut_blocks}块, {cut_tokens:,}tokens"
        )
        return {
            "ok": True,
            "cut_blocks": cut_blocks,
            "cut_tokens": cut_tokens,
            "kept_blocks": keep_blocks,
        }

    # ── /restart-omlx ────────────────────────────────────────────────

    @app.post("/restart-omlx")
    async def restart_omlx():
        try:
            subprocess.run("pkill -9 -f omlx-server", shell=True, timeout=5)
            subprocess.run("pkill -9 -f oMLX", shell=True, timeout=5)
            time.sleep(2)
            subprocess.run("open /Applications/oMLX.app", shell=True)
            state.log("[OMLX] GUI 已重启，KV缓存清空")
            return {"ok": True, "message": "OMLX GUI 重启，缓存已清"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ── /shutdown ────────────────────────────────────────────────────

    @app.post("/shutdown")
    async def shutdown():
        os.kill(os.getpid(), signal.SIGTERM)
        return {"ok": True}

    # ── / (Dashboard) ────────────────────────────────────────────────

    @app.get("/")
    async def dashboard():
        omlx_data = await _get_omlx_stats(client)
        return HTMLResponse(render_dashboard(state, omlx_data))

    # ── /logs ────────────────────────────────────────────────────────

    @app.get("/logs")
    async def get_logs():
        return JSONResponse(content={"logs": state.get_logs(50)})

    return app
