"""
Vkbro-MLX Agent Cache Proxy v2.8.1 — Dashboard HTML 生成
========================================================
从原 proxy.py 的 dashboard() 路由函数中提取 HTML 生成逻辑。
"""

try:
    from .config import PRE_FREEZE_DELAY
    from .proxy_state import ProxyState
except ImportError:
    from config import PRE_FREEZE_DELAY  # type: ignore[no-redef]
    from proxy_state import ProxyState  # type: ignore[no-redef]


def render_dashboard(state: ProxyState, omlx_data: dict | None = None) -> str:
    """生成完整的 Dashboard HTML 页面。

    Args:
        state: ProxyState 单例。
        omlx_data: OMLX 管理 stats JSON，None 表示 OMLX 未连接。

    Returns:
        完整 HTML 字符串。
    """
    s = state.snapshot()
    wa = (sum(s["hit_rate_window"]) / len(s["hit_rate_window"])
          if s["hit_rate_window"] else 0)
    sc = {"HEALTHY": "#34c759", "DEGRADED": "#ff9500", "CRITICAL": "#ff3b30"}
    state_label = {"HEALTHY": "健康", "DEGRADED": "一般", "CRITICAL": "需关注"}

    omlx_hot_blocks: str = "?"
    omlx_hot_gb: str = "?"
    omlx_ssd_gb: str = "?"
    proxy_conv_blocks: str = "?"
    proxy_eph_blocks: int = s.get("_last_ephemeral_tokens", 0) // 2048
    proxy_frozen: int = s["frozen_token_count"] // 2048 if s["frozen_token_count"] else 0

    omlx_html: str = ""
    proxy_map_html: str = ""

    if omlx_data is not None:
        try:
            runtime_models = omlx_data["runtime_cache"]["models"]
            active_ids = {
                am["id"]
                for am in omlx_data.get("active_models", {}).get("models", [])
            }
            m_found = next(
                (m for m in runtime_models if m["id"] in active_ids), None
            )
            if m_found is None and runtime_models:
                m_found = runtime_models[0]

            # OMLX overview stats
            for m in runtime_models:
                omlx_hot_blocks_val = m.get("hot_cache_entries", 0)
                block_size = m.get("block_size", 2048)
                omlx_hot_gb_val = round(omlx_hot_blocks_val * block_size / 1024 ** 3, 1)
                omlx_ssd_gb_val = round(
                    m.get("total_size_bytes", 0) / 1024 ** 3 - omlx_hot_gb_val, 1
                )
                omlx_hot_blocks = str(omlx_hot_blocks_val)
                omlx_hot_gb = str(omlx_hot_gb_val)
                omlx_ssd_gb = str(omlx_ssd_gb_val)
                break

            proxy_frozen = s["frozen_token_count"] // 2048 if s["frozen_token_count"] else 0
            proxy_total_blocks = (
                omlx_hot_blocks_val
                if isinstance(omlx_hot_blocks_val, int) and omlx_hot_blocks_val > 0
                else max(4, proxy_frozen)
            )
            proxy_conv = max(
                0, proxy_total_blocks - proxy_frozen - proxy_eph_blocks
            )
            proxy_conv_blocks = str(proxy_total_blocks)
            proxy_eph = proxy_eph_blocks if isinstance(proxy_eph_blocks, int) else 0
            proxy_total = max(1, proxy_frozen + proxy_conv + proxy_eph)

            # Proxy block map
            proxy_map_parts: list[str] = []
            for i in range(proxy_total):
                if i < proxy_frozen:
                    bg, tip = "#34c759", f"块{i}: 锚定块(锁死)"
                elif i >= proxy_total - proxy_eph:
                    bg, tip = "#8e8e93", f"块{i}: 瞬态块(工具调用)"
                else:
                    bg, tip = "#ff9500", f"块{i}: 对话块"
                proxy_map_parts.append(
                    f'<span class="blk" style="background:{bg}" title="{tip}"></span>'
                )
            proxy_blocks_map = "".join(proxy_map_parts)
            proxy_map_html = f'''<div class="card-section">
  <div class="section-title">Proxy 块地图 <span class="hint">锚定={proxy_frozen}块 · 对话={proxy_conv}块 · 瞬态={proxy_eph}块 · 每块≈2K tokens</span></div>
  <div class="blocks-container">{proxy_blocks_map}</div>
  <div class="legend">
    <span class="legend-item"><span class="dot" style="background:#34c759"></span> 锚定</span>
    <span class="legend-hint">system+工具，永久不变</span>
    <span class="legend-item"><span class="dot" style="background:#ff9500"></span> 对话</span>
    <span class="legend-hint">聊天+工具结果，KV缓存复用</span>
    <span class="legend-item"><span class="dot" style="background:#8e8e93"></span> 瞬态</span>
    <span class="legend-hint">当前轮工具调用，每轮重建</span>
  </div>
</div>
'''

            # OMLX KV cache block map
            if m_found:
                hot = m_found.get("hot_cache_entries", 0)
                bs = m_found.get("block_size", 2048)
                ssd_files = m.get("num_files", 0)
                ssd_size = m.get("total_size_bytes", 0)
                rates = m.get("cache_rates", {}).get("windows", {}).get("15m", {})
                hit_15m = rates.get("prefix_hit_rate", 0)
                hits_15m = rates.get("prefix_hits", 0)
                misses_15m = rates.get("prefix_misses", 0)

                frozen_blocks = s["frozen_token_count"] // bs if s["frozen_token_count"] else 0
                ephemeral_blocks = s["_last_ephemeral_tokens"] // bs
                stable_hot = s["_stable_hot_blocks"] or hot
                reloading_count = max(0, hot - stable_hot)

                blocks_parts: list[str] = []
                for i in range(min(hot, 120)):
                    if i < frozen_blocks:
                        bg, zone = "#34c759", "锚定"
                    elif i >= hot - ephemeral_blocks:
                        bg, zone = "#8e8e93", "瞬态"
                    elif i >= stable_hot:
                        bg, zone = "#ff9500", "对话块"
                    else:
                        bg, zone = "#ff9500", "对话块"
                    loading = i >= stable_hot and i < hot - ephemeral_blocks
                    extra = ' style="opacity:0.25"' if loading else ""
                    tip = f"块{i}: {zone}{'（加载中·新对话尚未缓存）' if loading else ''}"
                    blocks_parts.append(
                        '<span'
                        + extra
                        + ' class="blk" style="background:'
                        + bg
                        + '" title="'
                        + tip
                        + '"></span>'
                    )
                blocks_map = "".join(blocks_parts)

                omlx_html = f"""

<div class="card-section">
  <div class="section-title">KV 缓存块地图 <span class="hint">每个色块={bs} tokens · 共{hot}块 · 块的大小和位置代表在对话历史中的顺序</span></div>
  <div class="blocks-container">{blocks_map}</div>
  <div class="legend">
    <span class="legend-item"><span class="dot" style="background:#34c759"></span> 锚定块</span>
    <span class="legend-hint">系统指令，永久锁定不变</span>
    <span class="legend-item"><span class="dot" style="background:#ff9500"></span> 对话块</span>
    <span class="legend-hint">你的聊天内容，缓存命中可直接复用</span>
    <span class="legend-item"><span class="dot" style="background:#ff9500;opacity:0.25"></span> 对话块(加载中)</span>
    <span class="legend-hint">新对话刚写入，下一轮即可复用</span>
    <span class="legend-item"><span class="dot" style="background:#8e8e93"></span> 瞬态块</span>
    <span class="legend-hint">工具调用结果，每轮重新生成</span>
  </div>
  <div class="stat-row">
    <div class="stat"><div class="stat-num">{hot}</div><div class="stat-label">热缓存块</div><div class="stat-desc">已在内存</div></div>
    <div class="stat"><div class="stat-num" style="color:#ff9500">{reloading_count}</div><div class="stat-label">加载中</div><div class="stat-desc">新建对话块</div></div>
    <div class="stat"><div class="stat-num">{ssd_files}</div><div class="stat-label">SSD 文件</div><div class="stat-desc">{ssd_size/1024**3:.1f}GB 持久化</div></div>
    <div class="stat"><div class="stat-num" style="color:{'#34c759' if hit_15m>0.5 else '#ff9500'}">{hit_15m:.0%}</div><div class="stat-label">15分钟命中率</div><div class="stat-desc">{hits_15m}命中 / {misses_15m}未命中</div></div>
  </div>
</div>"""
        except Exception as e:
            omlx_html = (
                f'<div class="card-section"><div class="section-title">OMLX 状态解析失败</div>'
                f'<div style="color:#ff3b30;font-size:14px">{e}</div></div>'
            )
    else:
        omlx_html = (
            '<div class="card-section"><div class="section-title">OMLX 未连接</div>'
            '<div style="color:#ff3b30;font-size:14px">无法获取 OMLX 状态</div></div>'
        )

    # 请求历史
    history_html = ""
    if s.get("request_history"):
        rows = "".join(
            f'<tr><td>{r["turn"]}</td><td>{r["time"]}</td>'
            f'<td style="text-align:right">{r["prompt"]:,}</td>'
            f'<td style="text-align:right;color:#34c759">{r["cached"]:,}</td>'
            f'<td style="text-align:right;color:#ff9500">{r["net"]:,}</td></tr>'
            for r in reversed(s["request_history"][-10:])
        )
        history_html = f"""
<div class="card-section">
  <div class="section-title">请求历史 <span class="hint">最近10轮 · 净预填=本轮新计算的token · 越小越快</span></div>
  <table>
    <tr><th>轮</th><th>时间</th><th style="text-align:right">总量</th><th style="text-align:right">缓存命中</th><th style="text-align:right">净预填</th></tr>
    {rows}
  </table>
</div>"""

    return _DASHBOARD_TEMPLATE.format(
        cache_color=sc[s["cache_state"]],
        state_label=state_label[s["cache_state"]],
        wa_percent=f"{wa:.0%}",
        net_prefill=f"{s['last_prompt_tokens'] - s['last_cached_tokens']:,}",
        total_turns=s["total_turns"],
        frozen_hash=(s["frozen_prefix_hash"] or "预热中")[:12],
        frozen_status="已锁定" if s["frozen_prefix_hash"] else f"需{PRE_FREEZE_DELAY}轮预热",
        frozen_blocks=s["frozen_token_count"] // 2048,
        window_avg=f"{wa:.0%}",
        net_val=f"{s['last_prompt_tokens'] - s['last_cached_tokens']:,}",
        cut_status="已截断" if s.get("_cut_summary") else "未截断",
        proxy_conv_blocks=proxy_conv_blocks,
        proxy_eph_blocks=str(proxy_eph_blocks),
        omlx_hot_blocks=omlx_hot_blocks,
        omlx_hot_gb=omlx_hot_gb,
        omlx_ssd_gb=omlx_ssd_gb,
        frozen_token_count=f"{s['frozen_token_count']:,}",
        frozen_tools_count=s["frozen_tools_count"],
        proxy_map_html=proxy_map_html,
        omlx_html=omlx_html,
        history_html=(
            history_html
            or '<div class="card-section"><div class="section-title">请求历史</div>'
               '<div style="color:#aeaeb2;font-size:13px;text-align:center;padding:32px 0">暂无记录</div></div>'
        ),
    )


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Vkbro-MLX Cache Proxy v2.8.1</title>
<style>
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Helvetica Neue',sans-serif;
  background:#f5f5f7;color:#1d1d1f;display:flex;gap:0;min-height:100vh;
  -webkit-font-smoothing:antialiased;
}}
.main-col{{
  flex:1;padding:32px 40px 48px;min-width:0;
}}
.main-col-inner{{
}}
.topbar{{
  display:flex;align-items:center;justify-content:space-between;
  padding:0 0 24px;border-bottom:1px solid rgba(0,0,0,0.06);margin-bottom:24px;
  position:relative;
}}
.topbar::after{{
  content:'';position:absolute;left:0;bottom:-1px;width:200vw;height:1px;
  background:rgba(0,0,0,0.06);
}}
.topbar h1{{font-size:20px;font-weight:600;letter-spacing:-0.3px}}
.topbar .status{{display:flex;align-items:center;gap:12px;font-size:12px;color:#86868b}}
.topbar .badge{{
  display:inline-flex;align-items:center;gap:6px;
  padding:4px 12px;border-radius:20px;font-size:12px;font-weight:500;
  background:#f5f5f7;border:0.5px solid rgba(0,0,0,0.08)
}}
.card-section{{
  background:#fff;border-radius:12px;padding:28px 32px;margin-bottom:16px;
  border:0.5px solid rgba(0,0,0,0.04)
}}
.section-title{{
  font-size:14px;font-weight:600;color:#1d1d1f;margin-bottom:16px;
  display:flex;align-items:baseline;gap:10px;
}}
.hint{{font-size:11px;font-weight:400;color:#aeaeb2;letter-spacing:0}}
.overview{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}}
.ov-card{{
  background:#fff;border-radius:12px;padding:20px 22px;
  border:0.5px solid rgba(0,0,0,0.04)
}}
.ov-label{{font-size:11px;color:#86868b;text-transform:uppercase;letter-spacing:0.3px;margin-bottom:6px}}
.ov-value{{font-size:24px;font-weight:600;letter-spacing:-0.3px;line-height:1.1}}
.ov-desc{{font-size:12px;color:#aeaeb2;margin-top:6px;line-height:1.4}}
.blocks-container{{line-height:1.1;padding:8px 0 16px;word-break:break-all}}
.blk{{display:inline-block;width:14px;height:6px;border-radius:3px;margin:1.5px;vertical-align:middle;transition:opacity 0.4s}}
.legend{{font-size:11px;color:#86868b;display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;margin-bottom:18px;line-height:1.6}}
.legend-item{{white-space:nowrap;display:inline-flex;align-items:center;gap:4px;font-weight:500;color:#1d1d1f}}
.legend-hint{{color:#aeaeb2;margin-right:10px}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%}}
.stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.stat{{text-align:center;padding:14px 8px;background:#fafafa;border-radius:8px}}
.stat-num{{font-size:22px;font-weight:600;letter-spacing:-0.3px;line-height:1;margin-bottom:4px}}
.stat-label{{font-size:11px;color:#86868b;font-weight:500}}
.stat-desc{{font-size:10px;color:#aeaeb2;margin-top:3px;line-height:1.3}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;color:#aeaeb2;font-weight:500;font-size:10px;text-transform:uppercase;letter-spacing:0.3px;border-bottom:1px solid rgba(0,0,0,0.04)}}
td{{padding:8px 10px;border-bottom:1px solid rgba(0,0,0,0.02);font-variant-numeric:tabular-nums}}
tr:hover td{{background:#fafafa}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
.btn{{
  display:inline-flex;align-items:center;padding:7px 18px;border-radius:8px;
  border:0.5px solid rgba(0,0,0,0.12);font-size:12px;font-weight:500;
  cursor:pointer;text-decoration:none;font-family:inherit;
  background:linear-gradient(180deg,#fff 0%,#fafafa 100%);
  color:#1d1d1f;
  box-shadow:0 1px 2px rgba(0,0,0,0.04),0 0 0 0.5px rgba(0,0,0,0.03);
  transition:all 0.15s ease;
  transform:translateY(0);
}}
.btn:hover{{
  opacity:1;
  transform:translateY(-1px);
  box-shadow:0 2px 6px rgba(0,0,0,0.08),0 0 0 0.5px rgba(0,0,0,0.06);
}}
.btn:active{{
  transform:translateY(0);
  box-shadow:0 0 0 0.5px rgba(0,0,0,0.06);
}}
.btn.primary{{border-color:#007aff;color:#007aff}}
.btn.warning{{border-color:#ff9500;color:#ff9500}}
.btn.danger{{border-color:#ff3b30;color:#ff3b30}}
.btn.ghost{{border-color:rgba(0,0,0,0.06);color:#86868b}}
.footnote{{font-size:11px;color:#86868b;margin-top:16px;line-height:1.5}}
.fn-item{{padding:6px 0;border-bottom:0.5px solid rgba(0,0,0,0.03)}}
.fn-item:last-child{{border-bottom:none}}
@media (max-width:800px){{
  body{{padding:16px}}
  .overview,.stat-row{{grid-template-columns:repeat(2,1fr)}}
}}
@media (max-width:480px){{
  .overview,.stat-row{{grid-template-columns:1fr}}
}}
  .log-panel{{display:none}}
}}
@media (max-width:1100px){{
  .log-panel{{display:none}}
}}

</style>
</head>
<body>
<div class="main-col">
<div class="main-col-inner">

<div class="topbar">
  <h1>Vkbro-MLX Cache Proxy v2.8.1</h1>
  <div class="status">
    <span class="badge"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{cache_color}"></span> {state_label}</span>
    自动刷新 · 每4秒
  </div>
</div>

<div class="overview">
  <div class="ov-card">
    <div class="ov-label">缓存效率</div>
    <div class="ov-value">{wa_percent}</div>
    <div class="ov-desc">OMLX 命中率（近5轮）· 锚定{frozen_token_count}t · {frozen_tools_count}工具</div>
  </div>
  <div class="ov-card">
    <div class="ov-label">净预填</div>
    <div class="ov-value" style="color:#ff9500">{net_prefill}</div>
    <div class="ov-desc">本轮重算 tokens · 越小越快 · {total_turns}轮</div>
  </div>
  <div class="ov-card">
    <div class="ov-label">锚点</div>
    <div class="ov-value" style="font-size:14px;font-family:monospace">{frozen_hash}</div>
    <div class="ov-desc">{frozen_status} · {frozen_blocks}块</div>
  </div>
  <div class="ov-card">
    <div class="ov-label">{state_label}</div>
    <div class="ov-value" style="color:{cache_color}">{window_avg}</div>
    <div class="ov-desc">窗均{window_avg} · net={net_val} · {cut_status}</div>
  </div>
</div>

<div style="display:flex;gap:12px;margin-bottom:16px">
  <div class="ov-card" style="flex:1">
    <div class="ov-label">Proxy → OMLX</div>
    <div class="ov-value" style="font-size:18px">对话{proxy_conv_blocks}块 · 瞬态{proxy_eph_blocks}块</div>
    <div class="ov-desc">Proxy 总块数(用OMLX实际数据) · 每块≈2K tokens</div>
  </div>
  <div class="ov-card" style="flex:1">
    <div class="ov-label">OMLX 缓存</div>
    <div class="ov-value" style="font-size:18px">{omlx_hot_blocks}块 · {omlx_hot_gb}GB</div>
    <div class="ov-desc">热缓存{omlx_hot_gb}GB(内存) · SSD {omlx_ssd_gb}GB</div>
  </div>
</div>

{proxy_map_html}{omlx_html}

<div class="card-section">
  <div class="section-title">控制</div>
  <div class="actions">
    <a href="/compress" class="btn primary" onclick="fetch('/compress',{{method:'POST'}});setTimeout(()=>location.reload(),400);return false">压缩瞬态</a>
    <a href="/uncompress" class="btn ghost" onclick="fetch('/uncompress',{{method:'POST'}});setTimeout(()=>location.reload(),400);return false">解除压缩</a>
    <a href="/reset-anchor" class="btn warning" onclick="if(confirm('重置后所有缓存重建，确定？')){{fetch('/reset-anchor',{{method:'POST'}});setTimeout(()=>location.reload(),400)}};return false">重置锚点</a>
    <a href="/checkpoint" class="btn warning" onclick="if(confirm('冻结老对话，只保留最近5轮？')){{fetch('/checkpoint',{{method:'POST'}}).then(r=>r.json()).then(d=>alert(d.cut_blocks+'块，约'+d.cut_tokens.toLocaleString()+'tokens'));setTimeout(()=>location.reload(),500)}};return false">冻结老对话</a>
    <a href="/restart-omlx" class="btn danger" onclick="if(confirm('重启 OMLX 清 KV 缓存？锚点需重新冻结')){{fetch('/restart-omlx',{{method:'POST'}});setTimeout(()=>location.reload(),800)}};return false">重启 OMLX</a>
    <a href="/shutdown" class="btn danger" onclick="if(confirm('关闭代理？')){{fetch('/shutdown',{{method:'POST'}});setTimeout(()=>location.reload(),800)}};return false">关闭</a>
  </div>
  <div class="footnote">
    <div class="fn-item"><strong>压缩瞬态</strong> — 把工具调用产生的结果合并成一条摘要。当工具调用很多时（比如连续查了5个文件），点此可减少瞬态块占用，释放KV缓存空间。</div>
    <div class="fn-item"><strong>解除压缩</strong> — 撤销压缩操作，工具结果恢复独立存储。如果你发现压缩后模型回答质量下降，点此恢复。</div>
    <div class="fn-item"><strong>重置锚点</strong> — 清空所有缓存，从头开始预热。当你切换了模型、或者系统指令发生重大变化时使用。注意：下一轮会慢很多（冷启动）。</div>
    <div class="fn-item"><strong>冻结老对话</strong> — 真砍旧对话：只保留最近5轮，被砍掉的用户消息提取为摘要永久放在对话块开头。砍完建议点「重启 OMLX」清 KV 缓存。</div>
    <div class="fn-item"><strong>重启 OMLX</strong> — 重启 OMLX 服务清空 KV 缓存。冻结老对话后使用，否则 OMLX 缓存残留会触发压缩。</div>
    <div class="fn-item"><strong>关闭</strong> — 停止Cache Proxy服务。OMLX本身不受影响，Hermes会直接连OMLX（失去缓存优化）。</div>
  </div>
</div>

{history_html}


</div>
</div>


</body></html>"""
