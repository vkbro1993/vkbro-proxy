#!/usr/bin/env python3.11
"""
Vkbro-MLX Agent Cache Proxy v2.8.1.1
=================================
本地 OMLX 单模型 + Hermes 单会话独占。
三块结构：锚定块(锁死) + 对话块(稳定) + 瞬态块(孤儿/技能/工具/缓冲)

核心目标：
  1. 锁定 System 消息为 KV Cache 永久锚点
  2. 工具定义打入锚定块——tools数组转文本锁死(新! v2.6.4)
  3. 技能加载(skill_view)从对话块抽出到瞬态
  4. 工具调用结果从对话块抽出到瞬态
  5. 消费过的技能/工具每次都在瞬态，保持对话块永远稳定
  6. 对话块超 40 块自动冻结检查点

用法:
    python3.11 proxy.py
    面板: http://localhost:8000
    上游 Hermes → localhost:8000
    下游 OMLX  → localhost:8001
"""

import asyncio
import os
import socket
import sys

import uvicorn

# 支持直接运行 `python proxy.py`：将当前目录加入 path
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if _CUR_DIR not in sys.path:
    sys.path.insert(0, _CUR_DIR)

from config import PROXY_PORT, PROXY_HOST  # noqa: E402
from server import create_app  # noqa: E402


def main() -> None:
    """创建 FastAPI 应用并启动 uvicorn 服务器（带看门狗）。"""
    max_retries = 5
    retry_count = 0
    while True:
        try:
            retry_count = 0
            app = create_app()
            try:
                print(f"Vkbro Cache Proxy v2.8.1 → http://localhost:{PROXY_PORT}")
            except OSError:
                pass

            config = uvicorn.Config(app, host=PROXY_HOST, port=PROXY_PORT, log_level="warning")
            server = uvicorn.Server(config)

            async def serve() -> None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((PROXY_HOST, PROXY_PORT))
                await server.serve(sockets=[sock])

            asyncio.run(serve())
        except OSError as e:
            if "[Errno 48]" in str(e) or "Address already in use" in str(e):
                retry_count += 1
                print(f"[看门狗] 端口 {PROXY_PORT} 被占用，尝试释放... ({retry_count}/{max_retries})")
                import subprocess
                subprocess.run(f"kill $(lsof -t -i :{PROXY_PORT}) 2>/dev/null", shell=True)
                import time
                time.sleep(2)
                if retry_count >= max_retries:
                    print(f"[看门狗] 无法释放端口 {PROXY_PORT}，请检查是否有其他服务占用")
                    break
            else:
                print(f"[看门狗] Proxy 异常退出: {e}")
                import time
                time.sleep(3)
        except Exception as e:
            print(f"[看门狗] Proxy 异常退出: {e}")
            print(f"[看门狗] 3 秒后自动重启...")
            import time
            time.sleep(3)


if __name__ == "__main__":
    main()
