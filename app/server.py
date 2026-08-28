#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BytePlus 账单管理系统 —— Web 服务(纯标准库)。

展示: 原授信额度 / 授信余额 / 已用 / 当期消费。全部按美金计。
认证: HTTP Basic(无 session)。配置见 config.json。

用法:
    python app/server.py                    # 用 config.json
    python app/server.py --port 8080
    python app/server.py --host 0.0.0.0     # 部署到 ECS(必须先配好用户名密码)
    python app/server.py --no-warm          # 跳过启动预热(首次打开页面会很慢)
"""

from __future__ import annotations

import argparse
import datetime
import io
import ipaddress
import json
import os
import re
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# 允许 `python app/server.py` 直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import api, auth, billing, config          # noqa: E402
from app.creds import Account, CredError, load_accounts  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "index.html")
LOGIN_PATH = os.path.join(HERE, "login.html")
BILL_PATH = os.path.join(HERE, "bill.html")
COMMON_JS_PATH = os.path.join(HERE, "common.js")
APP_CSS_PATH = os.path.join(HERE, "app.css")

# 图片目录。**这是本应用唯一按文件名取文件的入口** —— 其它路由全是白名单
# 常量，天然没有穿越问题；这里必须自己防。规则见 _serve_static()。
STATIC_DIR = os.path.join(HERE, "static")
STATIC_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
}
# 只允许"一层普通文件名": 首字符必须是字母数字，随后只准字母数字和 . _ -
# 于是 /、\、%、: 全部落空 —— 连 ..%2f 这种编码形态都进不来(见下方注释)。
STATIC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 侧边栏和登录页都用它做 favicon，省得再放一份 .ico
FAVICON_NAME = "Kuromi_Icon_50px_20260828.png"

# 按需求: 所有账号均以美金授信/结算
CURRENCY = "USD"

# 并发上限。api.py 里有全局节流(4 req/s)，所以开更多线程也快不了，
# 只会让突发请求排队更深。
MAX_WORKERS = 4

# 只有来自这些地址的请求，其转发头(X-Real-IP / X-Forwarded-For)才被采信。
# nginx 反代跑在本机，所以是回环地址；直连的客户端一律按真实 peer 处理。
TRUSTED_PROXY_IPS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# 运行时状态(main() 里填)
CFG: Dict[str, Any] = {}
HISTORY: Optional[billing.History] = None
THROTTLE = auth.LoginThrottle()


def _is_ip(value: str) -> bool:
    """校验是否是合法 IP —— 转发头的值不能直接当限流键/日志内容用。"""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def current_period() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


# ===========================================================================
# 采集
# ===========================================================================
def collect(accounts: List[Account], selected_period: str) -> Dict[str, Any]:
    started = time.monotonic()
    cur = current_period()

    def one(acct: Account) -> Dict[str, Any]:
        try:
            return billing.build_account(
                acct, selected_period, cur, HISTORY,
                CFG["history_months"], CFG["empty_months_stop"])
        except Exception as exc:  # 兜底: 单个账号不该拖垮整页
            return {
                "email": acct.email, "uid": acct.uid,
                "ak_masked": acct.ak_masked, "ok": False,
                "quota": None,
                "quota_error": {"message": "{}: {}".format(
                    type(exc).__name__, exc), "code": None,
                    "http_status": None, "request_id": None,
                    "rate_limit": False},
                "current_spend": None, "current_spend_error": None,
                "selected_spend": None, "selected_spend_error": None,
                "granted": None, "used": None, "granted_partial": False,
                "missing_periods": [], "scanned_back_to": None, "notes": [],
            }

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(accounts))),
                            thread_name_prefix="bp") as pool:
        rows = list(pool.map(one, accounts))

    if HISTORY is not None:
        HISTORY.save()

    totals = {"granted": Decimal("0"), "balance": Decimal("0"),
              "used": Decimal("0"), "selected_spend": Decimal("0")}
    partial = False
    for row in rows:
        for key, value in (
            ("granted", row.get("granted")),
            ("balance", (row.get("quota") or {}).get("balance")),
            ("used", row.get("used")),
            ("selected_spend", (row.get("selected_spend") or {}).get("total")),
        ):
            d = api.to_decimal(value)
            if d is not None:
                totals[key] += d
        # 币种一致性检查: 需求说全是美金，真出现别的币种要让人看见
        got = (row.get("current_spend") or {}).get("currency")
        if got and got != CURRENCY:
            row.setdefault("notes", []).append(
                "接口返回币种是 {}，不是 {}".format(got, CURRENCY))
        if row.get("granted_partial"):
            partial = True

    return {
        "period": selected_period,
        "current_period": cur,
        "currency": CURRENCY,
        "fetched_at": utc_now_iso(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "accounts": rows,
        "totals": {k: str(v) for k, v in totals.items()},
        "totals_partial": partial,
        "account_count": len(rows),
        "error_count": sum(1 for r in rows if not r.get("ok")),
    }


def warm_cache(accounts: List[Account]) -> None:
    """启动时预热历史缓存。

    「原授信额度」要累加所有历史账期，而 API 限流很紧。已结账的月份不会再变,
    所以只需扫一次并落盘；之后每次刷新只查当月。
    """
    cur = current_period()
    print(" 预热历史账期缓存(用于推算原授信额度)…")
    t0 = time.monotonic()
    total_fetched = 0
    for acct in accounts:
        result = billing.scan_history(
            acct, cur, HISTORY, CFG["history_months"],
            CFG["empty_months_stop"], log=lambda m: print(m))
        total_fetched += result["fetched"]
        state = "完整" if result["complete"] else "不完整"
        print("   {:<24} 扫到 {}  {}{}".format(
            acct.email or acct.uid, result["scanned_back_to"], state,
            "  缺失: " + ",".join(result["missing"]) if result["missing"] else ""))
    HISTORY.save()
    print(" 预热完成: 新查 {} 个账期，耗时 {:.1f}s".format(
        total_fetched, time.monotonic() - t0))


# ===========================================================================
# HTTP
# ===========================================================================
class Server(ThreadingHTTPServer):
    """端口复用必须按平台区分 —— 两边的 SO_REUSEADDR 语义根本不一样。

    Windows: 允许两个**存活**进程绑同一端口。那样起第二个实例不报错，请求会
             随机落到旧进程上 —— 改完代码重启却拿到旧数据，极难排查。必须关。
    Linux  : 只允许绑过 TIME_WAIT 残留；遇到存活的监听者照样 EADDRINUSE，
             所以开着并不会掩盖"已有实例在跑"。必须开 —— 关掉的话服务重启时
             旧连接的 TIME_WAIT 会让新进程绑不上，systemd 只能反复重试，
             白停机几十秒(实测重启计数一次涨到 12)。
    """
    allow_reuse_address = (os.name != "nt")
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "BytePlusBilling/2.0"
    protocol_version = "HTTP/1.1"

    # 业务数据必须 no-store，绝不能落盘。
    NO_STORE = "no-store, no-cache, must-revalidate"
    # 但**页面文档不能带 no-store** —— Chrome 会因此判定该页不进 bfcache，
    # 于是从明细页按返回键时整页重新执行、把所有账号的接口重查一遍。
    # 页面外壳本身不含任何业务数据，用 no-cache(每次 revalidate)就够，
    # 既不会用到过期的 HTML，又保住了 bfcache。
    REVALIDATE = "no-cache, must-revalidate"

    # ---- 输出 ----
    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None,
              cache: Optional[str] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache or self.NO_STORE)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any,
              extra: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False,
                          default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    # ---- 客户端 IP ----
    def client_ip(self) -> str:
        """真实客户端 IP(限流和日志都用它)。

        上 nginx 反代之后 self.client_address 会永远是 127.0.0.1，限流就从
        「按 IP」退化成「全局」—— 任何人连错 8 次会把所有人一起锁 5 分钟。
        所以要读转发头，但**只有对端是本机 nginx 时才信**；直连的请求一律用
        真实 peer，否则谁都能自己塞一个头绕过限流。
        """
        peer = self.client_address[0] if self.client_address else "?"
        if peer not in TRUSTED_PROXY_IPS:
            return peer

        headers = getattr(self, "headers", None)
        if headers is None:      # 请求行都没解析成功时不会有 headers
            return peer

        # X-Real-IP: nginx 用 $remote_addr 覆写，客户端伪造不了，优先用
        candidate = (headers.get("X-Real-IP") or "").strip()
        if not candidate:
            # 退路 X-Forwarded-For：必须取**最后**一段。
            # $proxy_add_x_forwarded_for 是把真实 peer 追加到客户端原有值末尾，
            # 所以只有最后一段可信；取第一段等于直接采信客户端伪造的内容。
            xff = headers.get("X-Forwarded-For") or ""
            candidate = xff.rsplit(",", 1)[-1].strip()

        if candidate and _is_ip(candidate):
            return candidate
        return peer

    # ---- 认证 ----
    def _authorized(self) -> bool:
        if not config.auth_enabled(CFG):
            return True

        ip = self.client_ip()
        locked = THROTTLE.locked_for(ip)
        if locked > 0:
            self._json(429, {"error": "尝试次数过多，请 {} 秒后再试".format(
                int(locked) + 1)}, {"Retry-After": str(int(locked) + 1)})
            return False

        if auth.verify(self.headers.get("Authorization"),
                       CFG["username"], CFG["password"]):
            THROTTLE.record_success(ip)
            return True

        if self.headers.get("Authorization"):
            THROTTLE.record_failure(ip)
            self.log_message("认证失败 from %s", ip)
        # 故意**不发** WWW-Authenticate: 一旦发了，浏览器会抢先弹它自己的原生
        # 登录框，login.html 就没机会显示了。curl -u 是抢先发凭据的，不依赖
        # 这个挑战头，所以脚本/监控照样能用。
        self._json(401, {"error": "需要用户名和密码"})
        return False

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # ---- 免认证: 健康检查 + 页面外壳。都不含任何业务数据，
        #      数据一律走 /api/accounts，那里才校验凭据。----
        if path == "/api/health":
            return self._json(200, {"ok": True, "time": utc_now_iso()})
        if path == "/login.html":
            return self._serve_asset(LOGIN_PATH)
        if path in ("/", "/index.html"):
            return self._serve_asset(INDEX_PATH)
        if path == "/bill.html":
            return self._serve_asset(BILL_PATH)
        if path == "/common.js":
            return self._serve_asset(
                COMMON_JS_PATH, "application/javascript; charset=utf-8")
        if path == "/app.css":
            return self._serve_asset(APP_CSS_PATH, "text/css; charset=utf-8")
        # 图片也必须免认证 —— 登录页的品牌图/图标是在登录**之前**加载的，
        # 放到认证之后就只会得到一片 401 占位框。图片本身不含业务数据。
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/favicon.ico":
            return self._serve_static(FAVICON_NAME)

        # ---- 以下需要认证 ----
        if not self._authorized():
            return

        if path == "/api/verify":
            # 供 login.html 校验用户名密码；不返回任何业务数据
            return self._json(200, {"ok": True,
                                    "username": CFG.get("username") or None,
                                    "auth_enabled": config.auth_enabled(CFG)})
        if path == "/api/accounts":
            return self._api_accounts(parse_qs(parsed.query))
        if path == "/api/account-list":
            return self._api_account_list()
        if path == "/api/detail":
            return self._api_detail(parse_qs(parsed.query))
        return self._json(404, {"error": "未找到: {}".format(parsed.path)})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _serve_asset(self, file_path: str,
                     content_type: str = "text/html; charset=utf-8") -> None:
        try:
            with open(file_path, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            return self._json(500, {"error": "读不到 {}: {}".format(
                os.path.basename(file_path), exc)})
        self._send(200, body, content_type, cache=self.REVALIDATE)

    def _serve_static(self, name: str) -> None:
        """/static/<文件名> —— 图片。**路径穿越防护全在这里**。

        以前所有路由都是白名单常量，所以 /.git/config、/cred.xlsx、
        /../config.json 天然全是 404/401。加了这个按文件名取文件的入口之后，
        那份"免费的安全"就没了，得自己挡住三类东西:

        1. 分隔符与上跳: STATIC_NAME_RE 只放过"一层普通文件名"，
           /、\\、.. 一律不匹配。
        2. 百分号编码: **故意不做 unquote**。BaseHTTPRequestHandler 不解码
           self.path，所以 /static/%2e%2e%2fconfig.json 到这里仍是带 % 的原文，
           而 % 不在正则字符集里 -> 直接落空。先解码再校验反而给自己开了个
           绕过口(经典的双重解码问题)。因此文件名只准 ASCII。
        3. 符号链接: 正则看不出 foo.png 是不是指向目录外的链接，所以再用
           realpath 核一遍最终位置仍在 static/ 里(纵深防御)。

        扩展名也是白名单: 就算有人把 cred.xlsx 拷进 static/，没有对应的
        Content-Type 也照样 404，不会被当文件发出去。
        """
        if not STATIC_NAME_RE.match(name):
            return self._json(404, {"error": "未找到"})
        ctype = STATIC_TYPES.get(os.path.splitext(name)[1].lower())
        if ctype is None:
            return self._json(404, {"error": "未找到"})

        base = os.path.normcase(os.path.realpath(STATIC_DIR))
        real = os.path.normcase(os.path.realpath(os.path.join(STATIC_DIR, name)))
        if real != base and not real.startswith(base + os.sep):
            self.log_message("拦下越界静态请求: %s", name)
            return self._json(404, {"error": "未找到"})
        if not os.path.isfile(real):
            return self._json(404, {"error": "未找到"})

        try:
            with open(real, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._json(404, {"error": "未找到"})
        # 图片不是业务数据，可以放心让浏览器缓存。不加 immutable ——
        # 换图时同名覆盖还能在一天内自然生效。
        #
        # CSP 是给 .svg 兜底的: SVG 是能带 <script> 的文档，直接访问
        # /static/x.svg 就是一次同源脚本执行机会。default-src 'none' + sandbox
        # 让它即使被直接打开也跑不了脚本；通过 <img> 加载时浏览器本就不执行
        # 脚本，所以这条头不影响正常使用。
        self._send(200, body, ctype,
                   extra={"Content-Security-Policy": "default-src 'none'; sandbox"},
                   cache="public, max-age=86400")

    def _api_account_list(self) -> None:
        """账号下拉用。只读 cred.xlsx，**不打任何 BytePlus 接口** ——
        账单页一进来就要把下拉框填上，不该为此先等一轮 API。

        只回 uid / email / 脱敏 AK。AK 原文和 SK 都不出现在响应里。
        """
        try:
            accounts = load_accounts(CFG["cred_file"])
        except CredError as exc:
            return self._json(500, {"error": str(exc), "kind": "cred"})
        return self._json(200, {
            "accounts": [{"uid": a.uid, "email": a.email,
                          "ak_masked": a.ak_masked} for a in accounts],
            "current_period": current_period(),
        })

    def _api_accounts(self, qs: Dict[str, List[str]]) -> None:
        period = (qs.get("period") or [current_period()])[0].strip()
        if not billing.PERIOD_RE.match(period):
            return self._json(400, {
                "error": "账期格式不对: '{}'，应为 YYYY-MM".format(period)})
        if period > current_period():
            return self._json(400, {
                "error": "账期 {} 还没到".format(period)})

        # 每次请求都重读 cred.xlsx —— 加了新账号不用重启
        try:
            accounts = load_accounts(CFG["cred_file"])
        except CredError as exc:
            return self._json(500, {"error": str(exc), "kind": "cred"})

        try:
            payload = collect(accounts, period)
        except Exception as exc:  # pragma: no cover
            return self._json(500, {"error": "采集失败: {}: {}".format(
                type(exc).__name__, exc)})
        return self._json(200, payload)

    def _api_detail(self, qs: Dict[str, List[str]]) -> None:
        """某账号某账期的账单明细 + 该账号的授信信息(账单页顶部那四张卡)。

        授信部分直接复用 billing.build_account —— 和总览页同一个函数，
        所以「原授信额度 / 授信余额 / 已用」的口径必然一致。自己在这里
        再算一套迟早会和总览页对不上。

        代价是多一次 quota 调用 + 一次当月概览(历史账期走缓存)，实测总耗时
        比只取明细多几百毫秒。明细本身的概览对账**不复用**这份数据: 那道
        ✓/⚠ 校验必须来自一次独立的实时概览查询，拿缓存值去对账等于自己
        跟自己对，校验就废了。
        """
        uid = (qs.get("uid") or [""])[0].strip()
        period = (qs.get("period") or [current_period()])[0].strip()

        if not uid:
            return self._json(400, {"error": "缺少参数 uid"})
        if not billing.PERIOD_RE.match(period):
            return self._json(400, {
                "error": "账期格式不对: '{}'，应为 YYYY-MM".format(period)})
        if period > current_period():
            return self._json(400, {"error": "账期 {} 还没到".format(period)})

        try:
            accounts = load_accounts(CFG["cred_file"])
        except CredError as exc:
            return self._json(500, {"error": str(exc), "kind": "cred"})

        acct = next((a for a in accounts if a.uid == uid), None)
        if acct is None:
            # 不回显 uid 以外的信息，也不列出有哪些账号
            return self._json(404, {"error": "找不到 UID 为 {} 的账号".format(uid)})

        started = time.monotonic()
        try:
            payload = billing.build_detail(acct, period)
        except api.BytePlusError as exc:
            return self._json(502, {"error": exc.message, "detail": exc.as_dict()})
        except Exception as exc:  # pragma: no cover
            return self._json(500, {"error": "取明细失败: {}: {}".format(
                type(exc).__name__, exc)})

        # 授信信息。单项失败只让那几张卡显示"查询失败"，明细照常展示 ——
        # 页面主体是明细，不该被额度查询失败拖成整页报错。
        payload["account"] = None
        payload["account_error"] = None
        try:
            payload["account"] = billing.build_account(
                acct, period, current_period(), HISTORY,
                CFG["history_months"], CFG["empty_months_stop"])
            if HISTORY is not None:
                HISTORY.save()      # scan_history 可能写入了新的已结账月份
        except Exception as exc:    # build_account 内部已按项兜错，这里是保险
            payload["account_error"] = {
                "message": "{}: {}".format(type(exc).__name__, exc),
                "code": None, "http_status": None,
                "request_id": None, "rate_limit": False,
            }

        payload["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        payload["fetched_at"] = utc_now_iso()
        return self._json(200, payload)

    # ---- 日志: 绝不打印 Authorization 头 ----
    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            ip = self.client_ip()
        except Exception:        # 日志绝不能因为取 IP 失败而抛异常
            ip = "-"
        sys.stderr.write("[{}] {} {}\n".format(
            datetime.datetime.now().strftime("%H:%M:%S"), ip, fmt % args))


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 cp1252/gbk，无法打印中文，这里强制 UTF-8。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            try:
                setattr(sys, name, io.TextIOWrapper(stream.buffer,
                                                    encoding="utf-8"))
            except Exception:
                pass


def main() -> None:
    global CFG, HISTORY
    _force_utf8_stdio()

    p = argparse.ArgumentParser(description="BytePlus 账单管理系统")
    p.add_argument("--host", help="监听地址(覆盖 config.json)")
    p.add_argument("--port", type=int, help="端口(覆盖 config.json)")
    p.add_argument("--no-warm", action="store_true", help="跳过启动预热")
    p.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = p.parse_args()

    try:
        CFG = config.load()
    except config.ConfigError as exc:
        print("配置有问题: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    if args.host:
        CFG["host"] = args.host
    if args.port:
        CFG["port"] = args.port

    # 没配密码就不许监听公网 —— 那等于把所有账号的账单裸奔在互联网上。
    # (这不是限制你的选择，而是防止漏配密码时静默暴露。)
    if not config.is_loopback(CFG["host"]) and not config.auth_enabled(CFG):
        print("拒绝启动: 监听 {} 属于对外暴露，但没有配置用户名/密码。\n"
              "请在 config.json 里设置 username / password，"
              "或用环境变量 BP_USERNAME / BP_PASSWORD。"
              .format(CFG["host"]), file=sys.stderr)
        sys.exit(2)

    try:
        accounts = load_accounts(CFG["cred_file"])
    except CredError as exc:
        print("凭据表有问题: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    HISTORY = billing.History(CFG["cache_file"])

    # 先占端口再干别的 —— 否则预热几十秒后才发现端口被占
    try:
        httpd = Server((CFG["host"], CFG["port"]), Handler)
    except OSError as exc:
        print("端口 {} 起不来: {}".format(CFG["port"], exc), file=sys.stderr)
        print("可能已有实例在跑，或换端口: --port 8888", file=sys.stderr)
        sys.exit(1)

    shown_host = "127.0.0.1" if config.is_loopback(CFG["host"]) else CFG["host"]
    url = "http://{}:{}/".format(shown_host, CFG["port"])

    print("=" * 62)
    print(" BytePlus 账单管理系统")
    print("=" * 62)
    print(" 账号数   : {}".format(len(accounts)))
    for a in accounts:
        print("   - {:<24} UID={:<12} AK={}".format(
            a.email or "(无邮箱)", a.uid, a.ak_masked))
    print(" 监听     : {}:{}".format(CFG["host"], CFG["port"]))
    print(" 地址     : {}".format(url))
    print(" 认证     : {}".format(
        "已启用 (用户名 {})".format(CFG["username"])
        if config.auth_enabled(CFG) else "未启用 —— 仅本机可访问"))
    print(" 币种     : {} (按需求统一美金)".format(CURRENCY))
    print(" 缓存     : {}".format(CFG["cache_file"]))
    print("=" * 62)
    sys.stdout.flush()

    if CFG["warm_cache_on_start"] and not args.no_warm:
        try:
            warm_cache(accounts)
        except KeyboardInterrupt:
            print("\n预热被中断，继续启动(首次打开页面会较慢)。")
        except Exception as exc:
            print(" 预热出错(不影响启动): {}: {}".format(type(exc).__name__, exc))
        sys.stdout.flush()

    print(" 就绪。停止: Ctrl+C")
    sys.stdout.flush()

    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        if HISTORY is not None:
            HISTORY.save()


if __name__ == "__main__":
    main()
