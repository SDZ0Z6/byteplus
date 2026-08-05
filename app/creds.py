#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 cred.xlsx 读取 BytePlus 账号凭据。

表结构(第一行为表头，列顺序无关、大小写不敏感):
    EMAIL | UID | AK | SK

安全约定
--------
本模块读取的是**明文**凭据表。SK 是最高敏感级别的数据，因此:
  * Account.sk 标记了 repr=False —— SK 不会出现在 repr()/f-string/traceback 里；
  * 调用方(server.py)必须保证 SK 永不进入任何 HTTP 响应、日志或异常消息；
  * 对外展示只用 Account.ak_masked（AK 是标识符而非密钥，脱敏后可显示）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

# 项目根目录 = 本文件所在 app/ 的上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CRED_PATH = os.path.join(PROJECT_ROOT, "cred.xlsx")

_REQUIRED_COLUMNS = ("EMAIL", "UID", "AK", "SK")


class CredError(Exception):
    """凭据表读取/校验失败。"""


@dataclass(frozen=True)
class Account:
    email: str
    uid: str
    ak: str
    # repr=False: 防止 SK 通过 repr()/日志/traceback 意外泄漏
    sk: str = field(repr=False)
    row: int = 0

    @property
    def ak_masked(self) -> str:
        """AK 脱敏显示(AK 是访问标识符，不是密钥)。"""
        if len(self.ak) <= 12:
            return self.ak[:3] + "***"
        return "{}...{}".format(self.ak[:6], self.ak[-4:])

    @property
    def label(self) -> str:
        return "{} ({})".format(self.email, self.uid) if self.email else str(self.uid)

    def __str__(self) -> str:  # 绝不包含 SK
        return self.label


def _cell(value) -> str:
    """把单元格转成规整的字符串。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        # Excel 常把 UID 这类整数读成 3001315439.0
        return str(int(value))
    return str(value).strip()


def load_accounts(path: Optional[str] = None,
                  sheet: Optional[str] = None) -> List[Account]:
    """读取凭据表，返回 Account 列表。失败抛 CredError。"""
    path = path or DEFAULT_CRED_PATH
    if not os.path.exists(path):
        raise CredError("凭据文件不存在: {}".format(path))

    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise CredError("缺少依赖 openpyxl，请运行: pip install openpyxl") from exc

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise CredError("无法打开 {}: {}".format(os.path.basename(path), exc)) from exc

    try:
        if sheet is not None:
            if sheet not in wb.sheetnames:
                raise CredError("找不到工作表 '{}'，实际有: {}".format(sheet, wb.sheetnames))
            ws = wb[sheet]
        else:
            ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()

    # ---- 定位表头(跳过顶部空行) ----
    header_idx = None
    for i, row in enumerate(rows):
        if any(_cell(c) for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise CredError("凭据表 '{}' 是空的".format(ws.title))

    header = [_cell(c).upper() for c in rows[header_idx]]
    col = {}
    for name in _REQUIRED_COLUMNS:
        if name not in header:
            raise CredError(
                "凭据表缺少列 '{}'。实际表头: {}".format(name, header or "(空)"))
        col[name] = header.index(name)

    # ---- 逐行解析 ----
    accounts: List[Account] = []
    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(_cell(c) for c in row):
            continue  # 跳过空行

        def get(name: str) -> str:
            i = col[name]
            return _cell(row[i]) if i < len(row) else ""

        email, uid, ak, sk = get("EMAIL"), get("UID"), get("AK"), get("SK")
        missing = [n for n, v in (("UID", uid), ("AK", ak), ("SK", sk)) if not v]
        if missing:
            raise CredError(
                "cred.xlsx 第 {} 行缺少 {} —— 请补全或删除该行".format(
                    offset, "/".join(missing)))

        accounts.append(Account(email=email, uid=uid, ak=ak, sk=sk, row=offset))

    if not accounts:
        raise CredError("凭据表里没有有效账号(只有表头?)")

    # UID 重复会让前端行无法区分，提前拦住
    seen = {}
    for a in accounts:
        if a.uid in seen:
            raise CredError(
                "UID {} 在第 {} 行和第 {} 行重复了".format(a.uid, seen[a.uid], a.row))
        seen[a.uid] = a.row

    return accounts


def find_account(accounts: List[Account], selector: str) -> Optional[Account]:
    """按邮箱 / UID / 序号(1 开始) 查找账号。"""
    s = str(selector).strip().lower()
    for i, a in enumerate(accounts, start=1):
        if s in (a.email.lower(), a.uid.lower(), str(i)):
            return a
    return None


if __name__ == "__main__":  # 自检: 只打印脱敏信息
    for i, acct in enumerate(load_accounts(), start=1):
        print("{}. {:24} UID={:12} AK={}".format(
            i, acct.email, acct.uid, acct.ak_masked))
