"""RemoteTrigger — 远程熔断触发服务 (E240/E250)
==================================================

独立 Flask 实例 (端口 8999)，提供 HTTP 接口远程激活/解除熔断。

端点:
    POST /api/emergency-stop   → 激活熔断 (需 token 认证)
    POST /api/emergency-resume → 解除熔断 (需 token 认证)
    GET  /api/circuit-status   → 查询状态 (只读)

安全约束:
    - 独立线程，不拖累主交易循环
    - 只监听 127.0.0.1 (localhost)，不暴露外网
    - stop/resume 需 POST + token 认证 (E250 P0-4)
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger("quant_framework.risk.remote")

_DEFAULT_PORT: int = 8999

# E250 P0-4: 从环境变量读取 token，未设置则生成随机 token
_EMERGENCY_TOKEN: str = os.environ.get(
    "EMERGENCY_TOKEN",
    secrets.token_hex(16),
)


def _check_auth(request) -> bool:
    """验证 token — 支持 header 或 query param。"""
    if not _EMERGENCY_TOKEN:
        return False
    token: str = request.headers.get("X-Auth-Token", "") or request.args.get("token", "")
    return secrets.compare_digest(token, _EMERGENCY_TOKEN)


def create_app():
    """创建熔断触发 Flask 应用。"""
    from flask import Flask, jsonify, request
    from quant_framework.risk.circuit_breaker import CircuitBreaker

    app = Flask(__name__)

    @app.route("/api/emergency-stop", methods=["POST"])
    def emergency_stop():
        """激活熔断 — POST + token 认证。"""
        if not _check_auth(request):
            return jsonify({"error": "unauthorized"}), 401
        CircuitBreaker.set_triggered(True)
        logger.info("[INFO] 熔断器被远程触发")
        return jsonify({"status": "熔断已激活"})

    @app.route("/api/emergency-resume", methods=["POST"])
    def emergency_resume():
        """解除熔断 — POST + token 认证。"""
        if not _check_auth(request):
            return jsonify({"error": "unauthorized"}), 401
        CircuitBreaker.set_triggered(False)
        logger.info("[INFO] 熔断器被远程解除")
        return jsonify({"status": "熔断已解除"})

    @app.route("/api/circuit-status", methods=["GET"])
    def circuit_status():
        """查询熔断状态。"""
        triggered: bool = CircuitBreaker.is_triggered()
        return jsonify({"triggered": triggered})

    return app


def run(port: int = _DEFAULT_PORT) -> None:
    """启动远程触发服务。"""
    app = create_app()
    logger.info(f"远程熔断服务启动: http://127.0.0.1:{port}")
    logger.info(f"紧急熔断Token: {_EMERGENCY_TOKEN}")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
