#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BytePlus 账单/余额查询脚本
==========================

用 BytePlus OpenAPI 查询账户余额、信控额度(生态Quota)、消费概览、账单明细。
使用 Volcengine/BytePlus 的 HMAC-SHA256 (Signature V4) 签名，纯标准库实现。

用法示例:
    python byteplus_billing.py summary               # 一键汇总(推荐): 信控额度+余额+本月消费
    python byteplus_billing.py quota                 # 信控额度(生态Quota, 对应控制台"信控额度")
    python byteplus_billing.py balance               # 现金/预付余额(QueryBalanceAcct)
    python byteplus_billing.py overview --period 2026-07     # 按产品消费概览
    python byteplus_billing.py detail  --period 2026-06 --limit 20   # 账单明细
    python byteplus_billing.py raw --action GetQuotaAcctInfo --version 2020-01-01 --service bill

凭据来源(优先级从高到低):
    1. 命令行 --ak / --sk
    2. 环境变量 BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY
    (脚本内不再保留默认密钥 —— 源码会进版本库)

注意: 不同接口用不同 service —
    - 现金余额 QueryBalanceAcct      -> service=billing, version=2022-01-01
    - 信控额度 GetQuotaAcctInfo      -> service=bill,    version=2020-01-01
    - 账单接口 ListBill*             -> service=billing, version=2022-01-01
每个业务函数已内置正确的 service/version，raw 子命令可用 --service/--version 覆盖。
"""

import argparse
import datetime
import hashlib
import hmac
import io
import json
import os
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# 这里**不要**填真实密钥。源码会进版本库，密钥跟着进去就等于公开了 ——
# 之前正是因为这两行硬编码了一把在用的 AK/SK，GitHub 推送保护把整个仓库拦下,
# 而且那把密钥在被拦之前就已经上传到对方服务器、只能作废重建。
# 用 --ak/--sk 或环境变量 BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY 传入。
# ---------------------------------------------------------------------------
DEFAULT_AK = ""
DEFAULT_SK = ""

# ---------------------------------------------------------------------------
# 端点配置。BytePlus 国际站:
#   host   = open.byteplusapi.com
#   region = ap-singapore-1
# service 按接口不同(见文件头说明)，因此 service 作为每次调用的参数传入。
# ---------------------------------------------------------------------------
HOST = os.environ.get("BYTEPLUS_HOST", "open.byteplusapi.com")
REGION = os.environ.get("BYTEPLUS_REGION", "ap-singapore-1")


# ===========================================================================
# 签名实现 (Volcengine / BytePlus Signature V4)
# ===========================================================================
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_request(ak, sk, service, method, query, body, action, version):
    """构造并签名一个 BytePlus OpenAPI 请求。返回 (url, headers, body_bytes)。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    body_bytes = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    payload_hash = _sha256_hex(body_bytes)

    # ---- 规范化 query string(含公共参数 Action/Version) ----
    q = dict(query or {})
    q["Action"] = action
    q["Version"] = version
    canonical_query = "&".join(
        "{}={}".format(quote(str(k), safe="-_.~"), quote(str(q[k]), safe="-_.~"))
        for k in sorted(q)
    )

    content_type = "application/x-www-form-urlencoded; charset=utf-8"

    # ---- 规范化 headers ----
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:{}\nhost:{}\nx-content-sha256:{}\nx-date:{}\n"
    ).format(content_type, HOST, payload_hash, x_date)

    canonical_request = "\n".join([
        method, "/", canonical_query, canonical_headers,
        signed_headers, payload_hash,
    ])

    # ---- StringToSign ----
    credential_scope = "{}/{}/{}/request".format(short_date, REGION, service)
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, credential_scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])

    # ---- 派生签名密钥(kSecret 直接是 SK, 不加前缀) ----
    k_date = _hmac(sk.encode("utf-8"), short_date)
    k_region = _hmac(k_date, REGION)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        "HMAC-SHA256 Credential={ak}/{scope}, "
        "SignedHeaders={sh}, Signature={sig}"
    ).format(ak=ak, scope=credential_scope, sh=signed_headers, sig=signature)

    headers = {
        "Host": HOST,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Content-Type": content_type,
        "Authorization": authorization,
    }
    url = "https://{}/?{}".format(HOST, canonical_query)
    return url, headers, body_bytes


def call(ak, sk, action, version, service, method="GET", query=None, body=""):
    """发起一次已签名的 API 调用，返回解析后的 JSON dict。"""
    url, headers, body_bytes = sign_request(
        ak, sk, service, method, query, body, action, version
    )
    req = Request(url, data=(body_bytes if method != "GET" else None),
                  headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print("HTTP {} 错误, 响应体:\n{}".format(e.code, raw), file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print("网络错误: {}".format(e.reason), file=sys.stderr)
        sys.exit(1)
    return json.loads(raw)


def _result_or_die(data, title=""):
    """检查 ResponseMetadata.Error，返回 Result 部分；出错则打印并退出。"""
    meta = data.get("ResponseMetadata", {})
    err = meta.get("Error") or (data.get("Error") if "Code" in data else None)
    if err:
        print("[{}] 调用失败:".format(title), file=sys.stderr)
        print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    return data.get("Result", data)


# ===========================================================================
# 业务封装
# ===========================================================================
def get_quota(ak, sk):
    """信控额度 / 生态 Quota(对应控制台"信控额度")。"""
    data = call(ak, sk, "GetQuotaAcctInfo", "2020-01-01", "bill")
    return _result_or_die(data, "信控额度")


def get_balance(ak, sk):
    """现金/预付余额(QueryBalanceAcct)。"""
    data = call(ak, sk, "QueryBalanceAcct", "2022-01-01", "billing")
    return _result_or_die(data, "账户余额")


def get_overview(ak, sk, period, limit=100, offset=0):
    """按产品的消费概览(ListBillOverviewByProd)。"""
    query = {"BillPeriod": period, "Limit": limit, "Offset": offset}
    data = call(ak, sk, "ListBillOverviewByProd", "2022-01-01", "billing", query=query)
    return _result_or_die(data, "消费概览")


def get_detail(ak, sk, period, limit=20, offset=0, product=None,
               group_term=0, group_period=0):
    """账单明细(ListBillDetail)。"""
    query = {
        "BillPeriod": period, "Limit": limit, "Offset": offset,
        "NeedRecordNum": 1, "GroupTerm": group_term, "GroupPeriod": group_period,
    }
    if product:
        query["Product"] = product
    data = call(ak, sk, "ListBillDetail", "2022-01-01", "billing", query=query)
    return _result_or_die(data, "账单明细")


# ===========================================================================
# CLI 命令
# ===========================================================================
def cmd_quota(ak, sk, args):
    _print(get_quota(ak, sk), "信控额度 (生态 Quota)")


def cmd_balance(ak, sk, args):
    _print(get_balance(ak, sk), "账户余额 (现金/预付)")


def cmd_overview(ak, sk, args):
    _print(get_overview(ak, sk, args.period, args.limit, args.offset),
           "消费概览 (按产品) - {}".format(args.period))


def cmd_detail(ak, sk, args):
    _print(get_detail(ak, sk, args.period, args.limit, args.offset,
                      args.product, args.group_term, args.group_period),
           "账单明细 - {}".format(args.period))


def cmd_summary(ak, sk, args):
    """一键汇总: 信控额度 + 现金余额 + 本月消费。对齐控制台"账户总览"。"""
    period = args.period
    quota = get_quota(ak, sk)
    bal = get_balance(ak, sk)
    ov = get_overview(ak, sk, period)

    month_total = "0"
    for item in (ov.get("List") or []):
        # 不同字段命名兜底
        month_total = item.get("BillAmount") or item.get("PayableAmount") or month_total

    print("========== BytePlus 账户汇总 ==========")
    print("AccountID      : {}".format(quota.get("AccountID")))
    print("信控额度(可用) : {} (总额度 {})".format(
        quota.get("AvailableBalance"), quota.get("Balance")))
    print("  冻结         : {}".format(quota.get("FreezeBalance")))
    print("现金余额       : {} {} (可用 {})".format(
        bal.get("CashBalance"), bal.get("Currency"), bal.get("AvailableBalance")))
    print("  未支付/欠费  : {}".format(bal.get("ArrearsBalance")))
    print("本月消费({}) : 见下方概览 List(空=无消费)".format(period))
    print("=======================================")
    if args.json:
        print(json.dumps(
            {"quota": quota, "balance": bal, "overview": ov},
            ensure_ascii=False, indent=2))


def cmd_raw(ak, sk, args):
    query = {}
    for p in (args.param or []):
        if "=" not in p:
            print("参数格式错误，应为 key=value: {}".format(p), file=sys.stderr)
            sys.exit(1)
        k, v = p.split("=", 1)
        query[k] = v
    data = call(ak, sk, args.action, args.version, args.service, query=query)
    _print(_result_or_die(data, args.action), args.action)


def _print(result, title=""):
    if title:
        print("===== {} =====".format(title))
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ===========================================================================
# CLI 骨架
# ===========================================================================
def build_parser():
    p = argparse.ArgumentParser(description="BytePlus 账单/余额查询")
    p.add_argument("--ak", help="Access Key Id")
    p.add_argument("--sk", help="Secret Access Key")
    sub = p.add_subparsers(dest="cmd", required=True)
    this_month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")

    sp = sub.add_parser("summary", help="一键汇总(信控额度+余额+本月消费)")
    sp.add_argument("--period", default=this_month, help="账期 YYYY-MM，默认本月")
    sp.add_argument("--json", action="store_true", help="附带完整 JSON")
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("quota", help="信控额度(生态Quota)")
    sp.set_defaults(func=cmd_quota)

    sp = sub.add_parser("balance", help="现金/预付余额")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("overview", help="按产品消费概览")
    sp.add_argument("--period", default=this_month)
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_overview)

    sp = sub.add_parser("detail", help="账单明细")
    sp.add_argument("--period", default=this_month)
    sp.add_argument("--product", help="按产品过滤，如 ECS")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--group-term", type=int, default=0, dest="group_term",
                    help="0=按明细 1=按天 2=按实例")
    sp.add_argument("--group-period", type=int, default=0, dest="group_period",
                    help="0=按账期 1=按天")
    sp.set_defaults(func=cmd_detail)

    sp = sub.add_parser("raw", help="调用任意 Action")
    sp.add_argument("--action", required=True)
    sp.add_argument("--version", default="2022-01-01")
    sp.add_argument("--service", default="billing", help="billing 或 bill")
    sp.add_argument("--param", action="append", help="key=value，可多次")
    sp.set_defaults(func=cmd_raw)

    return p


def _force_utf8_stdio():
    """Windows 控制台默认 cp1252/gbk，无法打印中文，这里强制 UTF-8。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8"))


def main():
    _force_utf8_stdio()
    args = build_parser().parse_args()
    ak = args.ak or os.environ.get("BYTEPLUS_ACCESS_KEY") or DEFAULT_AK
    sk = args.sk or os.environ.get("BYTEPLUS_SECRET_KEY") or DEFAULT_SK
    if not ak or not sk:
        print("缺少凭据: 用 --ak/--sk 或环境变量 BYTEPLUS_ACCESS_KEY/SECRET_KEY", file=sys.stderr)
        sys.exit(1)
    args.func(ak, sk, args)


if __name__ == "__main__":
    main()
