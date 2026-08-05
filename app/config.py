#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置加载: config.json + 环境变量覆盖。

配置来源优先级(高到低):
    1. 环境变量 BP_HOST / BP_PORT / BP_USERNAME / BP_PASSWORD
    2. 项目根目录 config.json
    3. 本文件的 DEFAULTS

密码按需求以**明文**存放(不加密)。因此 config.json 必须限制权限:
    Linux:   chmod 600 config.json
    Windows: icacls config.json /inheritance:r /grant:r "%USERNAME%:(R,W)"
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

DEFAULTS: Dict[str, Any] = {
    # 监听地址。默认只开本机；部署到 ECS 要改成 "0.0.0.0"
    "host": "127.0.0.1",
    "port": 8787,
    # 登录账号(明文)。留空 = 不启用验证，此时只允许监听回环地址
    "username": "",
    "password": "",
    # 账号凭据表
    "cred_file": "cred.xlsx",
    # 「原授信额度」= 授信余额 + 累计消费，需要往前扫历史账期
    "history_months": 36,       # 最多往前扫多少个月
    "empty_months_stop": 6,     # 连续多少个月无消费就认为扫到头
    "cache_file": "cache/history.json",
    # 启动时预热历史缓存(建议开; 关掉则首次打开页面会很慢)
    "warm_cache_on_start": True,
}


class ConfigError(Exception):
    pass


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load(path: str | None = None) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    path = path or CONFIG_PATH

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError("config.json 不是合法 JSON: {}".format(exc)) from None
        except OSError as exc:
            raise ConfigError("读不到 config.json: {}".format(exc)) from None
        if not isinstance(data, dict):
            raise ConfigError("config.json 顶层必须是一个对象 {...}")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ConfigError(
                "config.json 有无法识别的字段: {}。可用字段: {}".format(
                    ", ".join(sorted(unknown)), ", ".join(sorted(DEFAULTS))))
        cfg.update(data)

    # 环境变量覆盖(部署时比改文件方便)
    env_map = {"BP_HOST": "host", "BP_PORT": "port",
               "BP_USERNAME": "username", "BP_PASSWORD": "password"}
    for env_key, cfg_key in env_map.items():
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]

    # ---- 归一化 + 校验 ----
    try:
        cfg["port"] = int(cfg["port"])
    except (TypeError, ValueError):
        raise ConfigError("port 必须是数字: {!r}".format(cfg["port"])) from None
    if not (1 <= cfg["port"] <= 65535):
        raise ConfigError("port 超出范围: {}".format(cfg["port"]))

    for key in ("history_months", "empty_months_stop"):
        try:
            cfg[key] = int(cfg[key])
        except (TypeError, ValueError):
            raise ConfigError("{} 必须是数字".format(key)) from None
        if cfg[key] < 1:
            raise ConfigError("{} 必须 >= 1".format(key))

    cfg["warm_cache_on_start"] = _as_bool(cfg["warm_cache_on_start"])
    cfg["host"] = str(cfg["host"]).strip()
    cfg["username"] = str(cfg["username"] or "")
    cfg["password"] = str(cfg["password"] or "")

    # 绝对化路径
    for key in ("cred_file", "cache_file"):
        p = str(cfg[key])
        cfg[key] = p if os.path.isabs(p) else os.path.join(PROJECT_ROOT, p)

    return cfg


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "")


def auth_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("username") and cfg.get("password"))
