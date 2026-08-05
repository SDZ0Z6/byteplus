#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BytePlus OpenAPI 客户端(独立实现，不依赖 script/ 下的任何代码)。

包含:
  * Volcengine/BytePlus Signature V4 签名(纯标准库)
  * 全局请求节流 + 限流退避重试
  * 信控额度 / 账单概览 两个接口封装
  * 金额字段解析

限流很重要: 实测并发 12 打过去，24 个请求里 19 个被拒，错误码
AccountFlowLimitExceeded。所以这里做了全局节流(默认 4 req/s)+ 指数退避重试，
所有调用都必须经过 call()，不要绕过。

不同接口用不同 service:
    信控额度 GetQuotaAcctInfo  -> service=bill,    version=2020-01-01
    账单概览 ListBillOverview* -> service=billing, version=2022-01-01
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

HOST = os.environ.get("BYTEPLUS_HOST", "open.byteplusapi.com")
REGION = os.environ.get("BYTEPLUS_REGION", "ap-singapore-1")

DEFAULT_TIMEOUT = 25

# 限流相关
RATE_LIMIT_CODES = {"AccountFlowLimitExceeded", "FlowLimitExceeded",
                    "Throttling", "RequestLimitExceeded"}
# 全局最小请求间隔(秒)。4 req/s 实测稳定；再快就开始被拒。
MIN_REQUEST_INTERVAL = float(os.environ.get("BYTEPLUS_MIN_INTERVAL", "0.25"))
# 撞限流后的退避序列(秒)
RETRY_BACKOFF = (1.0, 2.0, 4.0, 8.0)


class BytePlusError(Exception):
    """API 调用失败。message 里绝不包含 SK。"""

    def __init__(self, message: str, code: Optional[str] = None,
                 http_status: Optional[int] = None,
                 request_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.request_id = request_id

    @property
    def is_rate_limit(self) -> bool:
        return self.code in RATE_LIMIT_CODES

    def as_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "code": self.code,
                "http_status": self.http_status, "request_id": self.request_id,
                "rate_limit": self.is_rate_limit}


class _Throttle:
    """全局节流器: 保证任意两次请求之间至少隔 min_interval 秒。

    所有账号共享一个实例 —— BytePlus 的限流是按账号(甚至按主体)算的，
    每个账号各自限速没用，必须全局排队。
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.min_interval
        if wait > 0:
            time.sleep(wait)


_throttle = _Throttle(MIN_REQUEST_INTERVAL)


# ===========================================================================
# 签名
# ===========================================================================
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(ak: str, sk: str, service: str, method: str, query: Optional[dict],
          body: bytes, action: str, version: str) -> Tuple[str, Dict[str, str]]:
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(body)

    q = dict(query or {})
    q["Action"] = action
    q["Version"] = version
    canonical_query = "&".join(
        "{}={}".format(quote(str(k), safe="-_.~"), quote(str(q[k]), safe="-_.~"))
        for k in sorted(q))

    content_type = "application/x-www-form-urlencoded; charset=utf-8"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:{}\nhost:{}\nx-content-sha256:{}\nx-date:{}\n"
    ).format(content_type, HOST, payload_hash, x_date)

    canonical_request = "\n".join([
        method, "/", canonical_query, canonical_headers,
        signed_headers, payload_hash])

    credential_scope = "{}/{}/{}/request".format(short_date, REGION, service)
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, credential_scope,
        _sha256_hex(canonical_request.encode("utf-8"))])

    # kSecret 直接是 SK，不加前缀
    k_signing = _hmac(_hmac(_hmac(_hmac(sk.encode("utf-8"), short_date),
                                  REGION), service), "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    headers = {
        "Host": HOST,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Content-Type": content_type,
        "Authorization": (
            "HMAC-SHA256 Credential={}/{}, SignedHeaders={}, Signature={}"
        ).format(ak, credential_scope, signed_headers, signature),
    }
    return "https://{}/?{}".format(HOST, canonical_query), headers


# ===========================================================================
# 调用
# ===========================================================================
def call(ak: str, sk: str, action: str, version: str, service: str,
         query: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT,
         max_attempts: int = len(RETRY_BACKOFF) + 1) -> dict:
    """发起一次已签名的 GET 调用。

    自动处理:
      * 全局节流(避免主动触发限流)
      * 撞限流 / 网络抖动时指数退避重试
    业务错误(鉴权失败、参数错)不重试，直接抛。
    """
    last_error: Optional[BytePlusError] = None

    for attempt in range(max_attempts):
        _throttle.acquire()
        # 每次重试都重新签名: X-Date 有时效，复用旧签名会被拒
        url, headers = _sign(ak, sk, service, "GET", query, b"", action, version)
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            _raise_if_error(data)
            return data
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                raise BytePlusError(
                    "HTTP {}: {}".format(exc.code, body[:300].strip() or "(空)"),
                    http_status=exc.code) from None
            try:
                _raise_if_error(data, http_status=exc.code)
            except BytePlusError as err:
                last_error = err
                if not err.is_rate_limit or attempt == max_attempts - 1:
                    raise
            else:
                raise BytePlusError("HTTP {} 但响应体无错误详情".format(exc.code),
                                    http_status=exc.code) from None
        except BytePlusError as err:
            last_error = err
            if not err.is_rate_limit or attempt == max_attempts - 1:
                raise
        except json.JSONDecodeError:
            raise BytePlusError("响应不是合法 JSON") from None
        except (URLError, TimeoutError, OSError) as exc:
            last_error = BytePlusError(
                "网络错误: {}".format(getattr(exc, "reason", exc)))
            if attempt == max_attempts - 1:
                raise last_error from None

        time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

    raise last_error or BytePlusError("未知错误")


def _raise_if_error(data: dict, http_status: Optional[int] = None) -> None:
    meta = data.get("ResponseMetadata") or {}
    err = meta.get("Error")
    if not err and isinstance(data.get("Error"), dict):
        err = data["Error"]
    if not err:
        return
    code = err.get("Code") or err.get("CodeN")
    raise BytePlusError(
        str(err.get("Message") or err.get("MessageCN") or "调用失败"),
        code=str(code) if code is not None else None,
        http_status=http_status, request_id=meta.get("RequestId"))


def _result(data: dict) -> dict:
    res = data.get("Result")
    return res if isinstance(res, dict) else data


# ===========================================================================
# 接口封装
# ===========================================================================
def get_quota(ak: str, sk: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """信控额度(授信)。返回 AvailableBalance / Balance / FreezeBalance 等。

    注意: 该接口**不返回币种字段**，也**没有"原授信额度"字段** ——
    原授信额度需要用 授信余额 + 累计消费 推算(见 billing.py)。
    """
    return _result(call(ak, sk, "GetQuotaAcctInfo", "2020-01-01", "bill",
                        timeout=timeout))


def get_overview(ak: str, sk: str, period: str, page_size: int = 100,
                 timeout: int = DEFAULT_TIMEOUT, max_pages: int = 20) -> dict:
    """按产品的消费概览。period 格式 YYYY-MM。

    该接口的 Total 实测返回 -1(拿不到总数)，只能靠"某页不满即最后一页"翻页。
    """
    items: List[dict] = []
    result: dict = {}
    offset = 0
    truncated = False

    for page in range(max_pages):
        result = _result(call(ak, sk, "ListBillOverviewByProd", "2022-01-01",
                              "billing",
                              query={"BillPeriod": period, "Limit": page_size,
                                     "Offset": offset},
                              timeout=timeout))
        batch = result.get("List") or []
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        if page == max_pages - 1:
            truncated = True

    out = dict(result)
    out["List"] = items
    if truncated:
        out["_truncated"] = True
    return out


# ===========================================================================
# 金额解析
# ===========================================================================
# 消费金额字段优先级。字段语义(用真实账号返回值核对过):
#   OriginalBillAmount  原始金额(折扣前、抹零前)      例 1.976589
#   RoundBillAmount     抹零差额 = 原始 - 实际应付     例 0.296589  ← 不是消费额!
#   PosttaxAmount       含税应付 = 实际账单金额        例 1.68
#                       已验证 == PaidAmount + UnpaidAmount == RealValue
# 别把 RoundBillAmount 放进来 —— 会让金额少算 90% 以上。
# PayableAmount 是火山引擎标准字段名，本站点未返回，保留首位以兼容其他账号。
SPEND_FIELDS = ("PayableAmount", "PosttaxAmount", "RealValue",
                "PretaxAmount", "OriginalBillAmount")


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def sum_spend(items: Optional[List[dict]]) -> Tuple[Optional[Decimal],
                                                    Optional[str], int,
                                                    Optional[str]]:
    """汇总消费概览。返回 (总额, 币种, 产品数, 实际使用的字段名)。

    空列表 = 本账期无消费 -> 返回 0(而不是 None; None 表示"取不到")。
    只对同一个字段求和 —— 混着不同字段加会算错。
    """
    if not items:
        return Decimal("0"), None, 0, None

    field = next((f for f in SPEND_FIELDS
                  if any(to_decimal(it.get(f)) is not None for it in items)),
                 None)
    if field is None:
        return None, None, len(items), None

    total = Decimal("0")
    for it in items:
        d = to_decimal(it.get(field))
        if d is not None:
            total += d

    currency = next((it.get("Currency") for it in items if it.get("Currency")),
                    None)
    return total, currency, len(items), field


def pick(d: Optional[dict], *names: str) -> Optional[str]:
    """取第一个存在的字段，返回字符串(保留精度，不转 float)。"""
    if not d:
        return None
    for n in names:
        if d.get(n) not in (None, ""):
            return str(d[n])
    return None
