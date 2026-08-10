#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务聚合: 原授信额度 / 授信余额 / 已用 / 当期消费。

「原授信额度」推算
------------------
BytePlus 的 GetQuotaAcctInfo **没有**"原授信额度"字段，只有当前余额。
但实测确认了这个恒等式:

    原授信额度 = 当前授信余额 + 累计消费(所有账期)

证据(2026-08 真实数据):
    bytep169:  188.71 + 11.29 = 200.00      整数
    Edward638: 999.92 +  0.08 = 1000.00     整数
    bytep168: 1462.66 + 75.34 = 1538.00
并且观察到余额是**实时**按消费扣减的: 当月消费从 11.14 涨到 11.29 时,
余额同步从 188.86 掉到 188.71(差额正好 0.15)。

因此「已用」= 累计消费，无需额外接口。

缓存
----
累计消费要逐月查历史账期，而 API 限流很紧(见 api.py)。
关键性质: **已结账的月份金额不再变化**，所以可以永久缓存，
每次刷新只需要重新查"当月"这一个账期。缓存文件删掉也没关系，会重新扫。

取数失败的账期不会被当成 0 —— 那样会少算累计消费、从而低估原授信额度。
失败的账期会记进 missing_periods，页面上明确标注"数据不完整"。
"""

from __future__ import annotations

import calendar
import datetime
import json
import os
import re
import threading
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from app import api
from app.creds import Account

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def prev_period(period: str) -> str:
    """"2026-01" -> "2025-12" """
    year, month = int(period[:4]), int(period[5:7])
    return "{:04d}-{:02d}".format(year - 1, 12) if month == 1 \
        else "{:04d}-{:02d}".format(year, month - 1)


# ===========================================================================
# 历史缓存
# ===========================================================================
class History:
    """已结账月份的消费额缓存(uid -> period -> 金额字符串)。"""

    VERSION = 1

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"version": self.VERSION, "accounts": {}}
        self._dirty = False
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return  # 缓存坏了就当没有，重新扫
        if isinstance(data, dict) and data.get("version") == self.VERSION \
                and isinstance(data.get("accounts"), dict):
            self._data = data

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            payload = json.dumps(self._data, ensure_ascii=False, indent=1)
            self._dirty = False
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)   # 原子替换，避免写一半被读到
        except OSError:
            pass  # 缓存写不进去不该让整个请求失败

    def entry(self, uid: str) -> Dict[str, Any]:
        with self._lock:
            accounts = self._data.setdefault("accounts", {})
            return accounts.setdefault(
                uid, {"periods": {}, "scanned_back_to": None, "complete": False})

    def get(self, uid: str, period: str) -> Optional[Decimal]:
        value = self.entry(uid)["periods"].get(period)
        return api.to_decimal(value) if value is not None else None

    def put(self, uid: str, period: str, amount: Decimal) -> None:
        with self._lock:
            self._data["accounts"][uid]["periods"][period] = str(amount)
            self._dirty = True

    def set_meta(self, uid: str, scanned_back_to: str, complete: bool) -> None:
        with self._lock:
            e = self._data["accounts"][uid]
            e["scanned_back_to"] = scanned_back_to
            e["complete"] = complete
            self._dirty = True


# ===========================================================================
# 取数
# ===========================================================================
def fetch_period_spend(acct: Account, period: str) -> Tuple[Optional[Decimal],
                                                            Optional[str],
                                                            int,
                                                            Optional[str],
                                                            bool]:
    """查一个账期的消费。返回 (金额, 币种, 产品数, 字段名, 是否被翻页截断)。"""
    ov = api.get_overview(acct.ak, acct.sk, period)
    total, currency, products, field = api.sum_spend(ov.get("List") or [])
    return total, currency, products, field, bool(ov.get("_truncated"))


def scan_history(acct: Account, current_period: str, history: History,
                 max_months: int, empty_stop: int,
                 log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """确保 acct 的已结账月份都在缓存里，返回扫描结果。

    从上一个月往前走，遇到连续 empty_stop 个零消费月就认为到头了。
    已在缓存里的月份不会再请求 API。
    """
    uid = acct.uid
    entry = history.entry(uid)
    periods = entry["periods"]

    missing: List[str] = []
    consecutive_empty = 0
    scanned = 0
    period = prev_period(current_period)
    fetched = 0

    while scanned < max_months:
        if period in periods:
            amount = api.to_decimal(periods[period])
        else:
            try:
                amount, _cur, _n, _f, _t = fetch_period_spend(acct, period)
                fetched += 1
            except api.BytePlusError as exc:
                missing.append(period)
                amount = None
                if log:
                    log("  {} {} 取数失败: {}".format(
                        acct.email, period, exc.message[:70]))
            if amount is not None:
                history.put(uid, period, amount)

        scanned += 1
        if amount is None:
            pass                      # 失败: 不知道是不是 0，不计入连续空月
        elif amount == 0:
            consecutive_empty += 1
        else:
            consecutive_empty = 0

        if consecutive_empty >= empty_stop:
            break
        period = prev_period(period)

    complete = consecutive_empty >= empty_stop and not missing
    history.set_meta(uid, period, complete)
    if fetched and log:
        log("  {} 新查了 {} 个账期，扫到 {}".format(acct.email, fetched, period))

    return {"missing": missing, "scanned_back_to": period,
            "complete": complete, "fetched": fetched}


def closed_cumulative(uid: str, history: History,
                      current_period: str) -> Decimal:
    """缓存里所有已结账月份的消费之和(缓存中只会有已结账月份)。"""
    total = Decimal("0")
    for period, value in history.entry(uid)["periods"].items():
        if period >= current_period:
            continue  # 当月永不入缓存，这里只是防御
        d = api.to_decimal(value)
        if d is not None:
            total += d
    return total


# ===========================================================================
# 组装单个账号
# ===========================================================================
def build_account(acct: Account, selected_period: str, current_period: str,
                  history: History, max_months: int,
                  empty_stop: int) -> Dict[str, Any]:
    """拉取并组装一个账号的展示数据。任何单项失败都只影响该项。"""
    row: Dict[str, Any] = {
        "email": acct.email,
        "uid": acct.uid,
        "ak_masked": acct.ak_masked,
        "quota": None, "quota_error": None,
        "current_spend": None, "current_spend_error": None,
        "selected_spend": None, "selected_spend_error": None,
        "granted": None, "used": None,
        "granted_partial": False, "missing_periods": [],
        "scanned_back_to": None,
        "notes": [],
    }

    # ---- 授信余额 ----
    balance: Optional[Decimal] = None
    frozen = Decimal("0")
    try:
        q = api.get_quota(acct.ak, acct.sk)
        balance = api.to_decimal(api.pick(q, "AvailableBalance"))
        frozen = api.to_decimal(api.pick(q, "FreezeBalance")) or Decimal("0")
        row["quota"] = {
            "balance": str(balance) if balance is not None else None,
            "total": api.pick(q, "Balance"),
            "frozen": str(frozen),
            "account_id": api.pick(q, "AccountID", "AccountId"),
        }
        if frozen != 0:
            # 恒等式是在 frozen=0 的账号上验证的，非零时不敢保证口径
            row["notes"].append(
                "有冻结额度 {}，原授信额度的推算口径未在该情况下验证".format(frozen))
    except api.BytePlusError as exc:
        row["quota_error"] = exc.as_dict()

    # ---- 当月消费(实时，绝不缓存) ----
    current_spend: Optional[Decimal] = None
    try:
        total, currency, products, field, truncated = fetch_period_spend(
            acct, current_period)
        current_spend = total
        row["current_spend"] = {
            "period": current_period,
            "total": str(total) if total is not None else None,
            "currency": currency, "products": products,
            "field": field, "truncated": truncated,
        }
    except api.BytePlusError as exc:
        row["current_spend_error"] = exc.as_dict()

    # ---- 所选账期消费(等于当月就直接复用，省一次调用) ----
    if selected_period == current_period:
        row["selected_spend"] = row["current_spend"]
        row["selected_spend_error"] = row["current_spend_error"]
    else:
        cached = history.get(acct.uid, selected_period)
        if cached is not None:
            row["selected_spend"] = {
                "period": selected_period, "total": str(cached),
                "currency": None, "products": None,
                "field": None, "truncated": False, "from_cache": True,
            }
        else:
            try:
                total, currency, products, field, truncated = \
                    fetch_period_spend(acct, selected_period)
                row["selected_spend"] = {
                    "period": selected_period,
                    "total": str(total) if total is not None else None,
                    "currency": currency, "products": products,
                    "field": field, "truncated": truncated,
                }
                if total is not None and selected_period < current_period:
                    history.put(acct.uid, selected_period, total)
            except api.BytePlusError as exc:
                row["selected_spend_error"] = exc.as_dict()

    # ---- 历史累计 -> 已用 / 原授信额度 ----
    scan = scan_history(acct, current_period, history, max_months, empty_stop)
    row["missing_periods"] = scan["missing"]
    row["scanned_back_to"] = scan["scanned_back_to"]

    if current_spend is not None:
        used = closed_cumulative(acct.uid, history, current_period) \
            + current_spend
        row["used"] = str(used)
        if balance is not None:
            row["granted"] = str(balance + used)
            # 有账期取数失败 -> 累计消费偏小 -> 原授信额度被低估
            row["granted_partial"] = bool(scan["missing"])
            if scan["missing"]:
                row["notes"].append(
                    "{} 个账期取数失败({})，累计消费偏小，原授信额度是下限值".format(
                        len(scan["missing"]), ", ".join(scan["missing"][:4])))
    row["ok"] = not (row["quota_error"] or row["current_spend_error"]
                     or row["selected_spend_error"])
    return row


# ===========================================================================
# 账单明细(下钻面板用)
# ===========================================================================
def _text(item: Dict[str, Any], *names: str) -> Optional[str]:
    """取第一个有内容的字段。API 用 "-" 和 "" 表示"无"，都当空处理。"""
    for n in names:
        v = item.get(n)
        if v is None:
            continue
        s = str(v).strip()
        if s and s != "-":
            return s
    return None


def _detail_row(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    """从 96 个字段里挑出展示需要的，金额一律走 Decimal 保精度。

    注意明细接口的抹零字段叫 RoundAmount(不是概览的 RoundBillAmount)，
    而且可能是科学计数法(实测 "2.5e-05") —— 必须用 Decimal，float 会丢精度。
    """
    instance_id = _text(item, "InstanceNo", "ResourceID")
    # 明细行没有稳定主键: BillDetailId 返回 "-"、BillID 是空串。
    # 这里合成一个，供前端做排序/展开状态用。
    key = "|".join([
        instance_id or "",
        _text(item, "ConfigurationCode") or "",
        _text(item, "ElementCode") or "",
        str(index),
    ])
    payable = api.to_decimal(api.pick(item, *api.SPEND_FIELDS))
    original = api.to_decimal(_text(item, "OriginalBillAmount"))
    rounded = api.to_decimal(_text(item, "RoundAmount", "RoundBillAmount"))

    billing_mode = _text(item, "BillingMode")
    return {
        "key": key,
        "date": _text(item, "ExpenseDate", "ExpenseBeginTime"),
        "product": _text(item, "ProductZh", "ProductName", "Product") or "(未命名)",
        "instance_name": _text(item, "InstanceName"),
        "instance_id": instance_id,
        "region": _text(item, "Region", "RegionCode"),
        "spec": _text(item, "ConfigName", "ConfigurationCode"),
        "billing_mode": billing_mode,
        "price": _text(item, "Price"),
        "price_unit": _text(item, "PriceUnit"),
        "usage": _text(item, "UseDuration"),
        "usage_unit": _text(item, "UseDurationUnit"),
        "original": str(original) if original is not None else None,
        "rounded": str(rounded) if rounded is not None else None,
        "payable": str(payable) if payable is not None else None,
        "project": _text(item, "ProjectDisplayName", "Project"),
        "currency": _text(item, "Currency"),
    }


def _month_days(period: str,
                today: Optional[datetime.date] = None) -> Tuple[List[str], bool, str]:
    """列出该账期要显示的日期。返回 (日期列表, 是否当月, 截止日)。

    当月只列到今天 —— 列满 31 天会让人以为"后面 21 天消费为零"。
    """
    year, month = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(year, month)[1]
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    is_current = (year, month) == (today.year, today.month)
    if is_current:
        last = min(last, today.day)
    days = ["{}-{:02d}".format(period, d) for d in range(1, last + 1)]
    return days, is_current, (days[-1] if days else period + "-01")


def build_detail(acct: Account, period: str, group_term: int = 0,
                 with_overview: bool = True) -> Dict[str, Any]:
    """拉取并组装一个账号某账期的**日维度**明细。

    用 GroupPeriod=1 让接口按天拆(ExpenseDate 才会有值)，GroupTerm=0 保持
    资源级 —— 实测这个组合下 InstanceName/InstanceNo/Region/ConfigName/Price
    全部保留，而 GroupTerm=2 会把它们清空。

    日合计不区分计费方式 —— 每行都带 billing_mode，表格里那一列已经说明了。
    注意包月/预付资源会把整笔费用记在**购买或续费当天**，所以某一天可能明显
    偏高(实测 08-04 的 52.16 里有 51.15 是包月入账)，那是入账日不是用量日。

    with_overview=True 时会额外查一次概览(约 +0.25 秒)，把它的合计一起返回，
    让明细页能**自己**完成对账 —— 它是独立页面，拿不到总览页那份数据。
    """
    raw = api.get_detail(acct.ak, acct.sk, period, group_term=group_term,
                         group_period=1)
    items = raw.get("List") or []
    rows = [_detail_row(it, i) for i, it in enumerate(items)]

    day_list, is_current, through = _month_days(period)
    # 接口返回的日期若超出上面这个范围(理论上不该有)，也补进去，别丢数据
    extra = sorted({r["date"] for r in rows if r["date"]} - set(day_list))
    all_days = sorted(set(day_list) | set(extra))

    days: Dict[str, Dict[str, Any]] = {
        d: {"date": d, "rows": [],
            "payable": Decimal("0"), "original": Decimal("0")}
        for d in all_days
    }
    undated: List[Dict[str, Any]] = []

    for row in rows:
        bucket = days.get(row["date"]) if row["date"] else None
        if bucket is None:
            undated.append(row)      # 没有日期的行不能凭空塞进某一天
            continue
        bucket["rows"].append(row)
        bucket["payable"] += api.to_decimal(row["payable"]) or Decimal("0")
        bucket["original"] += api.to_decimal(row["original"]) or Decimal("0")

    for bucket in days.values():
        # 同一天内金额大的排前面
        bucket["rows"].sort(key=lambda r: api.to_decimal(r["payable"]) or Decimal("0"),
                            reverse=True)

    groups = [days[d] for d in all_days]
    total_payable = sum((g["payable"] for g in groups), Decimal("0"))
    total_original = sum((g["original"] for g in groups), Decimal("0"))
    for row in undated:               # 无日期的行仍要计入合计，否则对不上账
        total_payable += api.to_decimal(row["payable"]) or Decimal("0")
        total_original += api.to_decimal(row["original"]) or Decimal("0")

    max_payable = max((g["payable"] for g in groups), default=Decimal("0"))
    currency = next((r["currency"] for r in rows if r["currency"]), None)

    # 概览合计 —— 供明细页自对账。取不到就把错误带上，页面上标"无法核对"，
    # 绝不能静默当作"一致"。
    overview_total: Optional[str] = None
    overview_error: Optional[Dict[str, Any]] = None
    if with_overview:
        try:
            ov = api.get_overview(acct.ak, acct.sk, period)
            ov_total, ov_cur, _n, _f = api.sum_spend(ov.get("List") or [])
            if ov_total is not None:
                overview_total = str(ov_total)
            if currency is None:
                currency = ov_cur
        except api.BytePlusError as exc:
            overview_error = exc.as_dict()

    return {
        "email": acct.email,
        "uid": acct.uid,
        "period": period,
        "overview_total": overview_total,
        "overview_error": overview_error,
        "group_term": group_term,
        "currency": currency,
        "row_count": len(rows),
        "reported_total": raw.get("_total"),
        "truncated": bool(raw.get("_truncated")),
        # 日维度: 每天一个条目，无消费的日子也在(值为 0)，否则柱状图日期会断
        "days": [{"date": g["date"],
                  "payable": str(g["payable"]),
                  "original": str(g["original"]),
                  "rows": g["rows"]} for g in groups],
        "undated_rows": undated,
        "max_payable": str(max_payable),
        "is_current_month": is_current,
        "through": through,
        "total_payable": str(total_payable),
        "total_original": str(total_original),
    }
