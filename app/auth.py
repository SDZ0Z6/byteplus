#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP Basic 认证 —— 无 session、无 cookie，浏览器自带登录弹窗。

按需求: "简单的用户名和密码验证，不需要 session"。Basic Auth 正好是这个语义,
每个请求自带凭据，服务端不存任何状态。

⚠ Basic Auth 的凭据只是 base64 编码，**不是加密**。在裸 HTTP 下等于明文传输,
公网上会被中间人直接抓到。这是已知的取舍(按需求选择了裸 HTTP)。

这里额外做了失败节流: 同一 IP 连续失败若干次后短暂锁定，
避免公网上被脚本无限撞密码。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import threading
import time
from typing import Dict, Optional, Tuple

# 失败节流参数
MAX_FAILURES = 8            # 窗口内允许的失败次数
FAILURE_WINDOW = 300.0      # 统计窗口(秒)
LOCKOUT_SECONDS = 300.0     # 超限后锁定时长(秒)


class LoginThrottle:
    """按来源 IP 统计认证失败次数，超限则临时拒绝。"""

    def __init__(self, max_failures: int = MAX_FAILURES,
                 window: float = FAILURE_WINDOW,
                 lockout: float = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._lock = threading.Lock()
        self._failures: Dict[str, list] = {}
        self._locked_until: Dict[str, float] = {}

    def locked_for(self, ip: str) -> float:
        """还需锁定多少秒；0 表示未锁定。"""
        with self._lock:
            until = self._locked_until.get(ip, 0.0)
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._locked_until.pop(ip, None)
                return 0.0
            return remaining

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._failures.get(ip, []) if now - t < self.window]
            hits.append(now)
            self._failures[ip] = hits
            if len(hits) >= self.max_failures:
                self._locked_until[ip] = now + self.lockout
                self._failures[ip] = []

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)


def parse_basic(header: Optional[str]) -> Optional[Tuple[str, str]]:
    """解析 Authorization: Basic xxx，返回 (user, password)。失败返回 None。"""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        raw = base64.b64decode(parts[1].strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if ":" not in text:
        return None
    user, _, pwd = text.partition(":")
    return user, pwd


def verify(header: Optional[str], username: str, password: str) -> bool:
    """校验 Basic 头。用 compare_digest 做定时安全比较。"""
    creds = parse_basic(header)
    if creds is None:
        return False
    user, pwd = creds
    # 两个字段都要比，且都用 compare_digest —— 避免通过响应时间猜用户名
    ok_user = hmac.compare_digest(user.encode("utf-8"), username.encode("utf-8"))
    ok_pass = hmac.compare_digest(pwd.encode("utf-8"), password.encode("utf-8"))
    return ok_user and ok_pass
