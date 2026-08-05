# BytePlus 授信管理系统

多账号的**原授信额度 · 授信余额 · 已用 · 消费**总览。纯 Python 标准库实现，
唯一外部依赖是读 xlsx 的 `openpyxl`。

金额统一按**美金 USD**显示（所有账号都是美金授信）。

## 线上地址

<https://kuromicloud.top/> —— 阿里云 ECS（马来西亚·吉隆坡）+ nginx + Let's Encrypt。
部署与运维见 [deploy/DEPLOY.md](deploy/DEPLOY.md)。

架构：

```
浏览器 ──HTTPS──> nginx (443)  ──HTTP──> 应用 (127.0.0.1:8787) ──> BytePlus OpenAPI
                     │                        │
              证书 acme.sh 续期        cred.xlsx / cache/history.json
```

应用只监听回环，公网到不了它 —— 唯一入口是 nginx。

## 本机启动

双击 `run.bat`，或

```bash
python app/server.py --open
```

没有 `config.json` 时默认只监听 `127.0.0.1` 且不启用登录。

## 依赖

```bash
pip install openpyxl
```

## 配置

复制 `config.example.json` 为 `config.json` 后按需修改：

| 字段 | 默认 | 说明 |
|---|---|---|
| `host` | `127.0.0.1` | 监听地址。部署到 ECS 改成 `0.0.0.0` |
| `port` | `8787` | 端口 |
| `username` / `password` | 空 | 登录账号（明文）。两个都留空 = 不启用验证 |
| `cred_file` | `cred.xlsx` | 账号凭据表 |
| `cache_file` | `cache/history.json` | 历史账期缓存，可安全删除 |
| `history_months` | `36` | 推算原授信额度时最多往前扫多少个月 |
| `empty_months_stop` | `6` | 连续多少个月无消费就认为扫到头 |
| `warm_cache_on_start` | `true` | 启动时预热缓存（关掉则首次打开页面很慢） |

也可用环境变量覆盖：`BP_HOST` / `BP_PORT` / `BP_USERNAME` / `BP_PASSWORD`。

没有 `config.json` 也能跑 —— 默认只监听本机、不启用登录。

## 账号配置

`cred.xlsx`，第一行表头（列顺序无关、大小写不敏感）：

| EMAIL | UID | AK | SK |
|-------|-----|----|----|
| a@example.com | 3001315439 | AKAP… | （密钥） |

加/改账号后**点页面「刷新」即生效，不用重启** —— 每次请求都会重读表格。

## 数据口径

| 页面字段 | 来源 |
|---|---|
| 授信余额 | `GetQuotaAcctInfo` (`bill`, 2020-01-01) 的 `AvailableBalance` |
| 当期消费 | `ListBillOverviewByProd` (`billing`, 2022-01-01) 的 `PosttaxAmount` 求和 |
| 已用 | 所有账期消费之和（累计消费） |
| **原授信额度** | **授信余额 + 已用**（推算，见下） |

### 原授信额度为什么要推算

BytePlus **没有任何接口返回"原授信额度"**。已穷举探测过 22 个候选 Action
（`ListQuotaAcctInfo` / `GetCreditAcctInfo` / `ListQuotaAdjustRecord` …），
这个命名空间下只有两个接口真实存在：`GetQuotaAcctInfo` 和 `QueryBalanceAcct`，
两者都只给**当前余额**，不给最初授信了多少。

但实测确认了这个恒等式：

```
原授信额度 = 当前授信余额 + 累计消费
```

真实数据核对（2026-08）：

| 账号 | 授信余额 | 累计消费 | 合计 |
|---|---|---|---|
| bytep169 | 188.71 | 11.29 | **200.00** |
| Edward638 | 999.92 | 0.08 | **1000.00** |
| bytep168 | 1462.64 | 75.36 | **1538.00** |

并且观察到余额是按消费扣减的：当月消费从 11.14 涨到 11.29 时，
余额同步从 188.86 掉到 188.71（差额正好 0.15）。所以「已用」就等于累计消费。

**精度说明**：额度扣减比账单接口略有延迟，推算值可能在几分钱内波动
（实测同一账号在 `1538.00` / `1538.02` 之间跳）。真实授信通常是整数。

**取数失败不当 0**：某个账期查不到时，该账号的原授信额度会标成 `≥ 下限值`
并提示"数据不完整"，而不是把失败的月份算成 0（那样会低估授信额度）。

### 消费额为什么取 `PosttaxAmount`

同一条账单里字段差别很大，取错会算出完全不同的金额：

| 字段 | 值 | 含义 |
|---|---|---|
| `OriginalBillAmount` | 1.976589 | 原始金额（抹零前） |
| `RoundBillAmount` | 0.296589 | **抹零差额** = 原始 − 应付。不是消费额！ |
| `PosttaxAmount` | 1.68 | 含税应付 = 实际账单金额 |

已核对 `PosttaxAmount == PaidAmount + UnpaidAmount == RealValue`。
字段优先级见 `app/api.py` 的 `SPEND_FIELDS`。

## 账单明细（下钻面板）

主表每行有「明细 ›」链接，当期消费金额本身也可点，**在当前页签打开** `detail.html`，
看该账号该账期的**资源级**账单：产品 / 实例名 / 实例ID / 地域 / 规格 / 计费方式 /
单价 / 用量 / 原始金额 / 抹零 / 应付金额 / 项目，按产品分两级并带小计。

* 真实 URL：`/detail.html?uid=<uid>&period=<YYYY-MM>` —— 可收藏、可分享、
  浏览器返回键天然回到总览，标签页标题是账号邮箱
* **按需加载** —— 不点开就不查
* 页面上换账期会同步改写 URL，刷新后还是同一个月
* 页脚把明细合计与**概览合计**对账，显式标 ✓ 或 ⚠
* **按返回键回总览不会重新查询**（见下）

### 对账数据为什么由服务端给

明细页是独立页面，拿不到总览页那份数据。所以 `/api/detail` 会额外查一次概览
（约 +0.25 秒）并把合计一起返回，页面据此自我核对。另外两条路都不行：前端自己调
`/api/accounts` 要拉全部账号（N×2 次调用，太贵）；用 URL 传期望值不可信、能被改。

取概览失败时会显示「⚠ 概览取数失败，无法核对」，**不会静默当成一致**。

### 返回总览为什么不重查

改成真实页面跳转后，按返回键本会让总览页整个重新执行、把所有账号的接口再查一遍
（3 个账号约 1.4 秒）。两层处理：

**第一层 —— 保住浏览器 bfcache。** 原先 `_send()` 给**所有**响应都加了
`Cache-Control: no-store`，而 `no-store` 出现在**页面文档**上会让 Chrome 判定该页
不可进 bfcache。现在拆开：业务数据仍是 `no-store`（`NO_STORE`），页面外壳和
`common.js` 改成 `no-cache, must-revalidate`（`REVALIDATE`）—— 照样每次revalidate，
不会用到过期 HTML，但保住了 bfcache。bfcache 生效时脚本根本不重跑，连滚动位置都在。

**第二层 —— 自己存快照。** bfcache 并不可靠：附加了调试器、内存压力、超时都会让它
失效（本地验证时就因为自动化用 CDP 连着浏览器而始终不生效）。所以总览数据每次取完
会存进 `sessionStorage`，**只在返回/前进导航时**（`performance` 的
`navigation.type === 'back_forward'`）拿来直接渲染，不打接口；快照超过 10 分钟作废。

其余情况一律照常查：首次打开、刷新页面、点「刷新」按钮、换账期。
返回时时间戳会标成「返回时的数据，点刷新可更新」，不会让人误以为是刚查的。

### 登录跳转与开放重定向

凭据在 `sessionStorage`（按标签页存）。同页签从总览跳明细，凭据还在；但收藏夹、
新标签页、发给同事打开时没有凭据，会被送去登录页，登录后**再跳回原来那个明细页** ——
靠 `?returnTo=` 实现。

这个参数**必须校验**，否则 `/login.html?returnTo=https://evil.com` 就把登录页变成
开放重定向，用户输完密码直接被送去钓鱼站。`login.html` 的 `safeReturnTo()` 只接受
以单个 `/` 开头的相对路径，挡掉绝对 URL、`//evil.com`（协议相对）、含 `:` 的、
以及绕回登录页自己。

### GroupTerm 的真实语义

`ListBillDetail` 的 `GroupTerm` 参数，`script/byteplus_billing.py` 的 help 文本写的是
`2=按实例`，**实测是错的**：

| | `GroupTerm=0` | `GroupTerm=2` |
|---|---|---|
| 粒度 | **每个资源一行** | 每个**产品**一行 |
| bytep168 行数 | 7 | 4 |
| `InstanceName` / `InstanceNo` / `Region` / `ConfigName` / `Price` | 有 | **全空** |
| 空/占位字段 | 少 | 49 / 96 |

`GroupTerm=2` 的粒度等同 `ListBillOverviewByProd`，对账用不上。**所以面板用
`GroupTerm=0`**，产品级汇总在前端按 `ProductZh` 分组算出来，一次调用拿两种粒度。

### 金额精度

明细里抹零差额常在 `1e-5` 量级（实测 `0.000025`），所以**原始金额和抹零两列用
最多 6 位小数**显示（`amt6()`），只显示 2 位会变成 `0.00`，那正好把这两列要解释的
东西抹掉了。应付金额始终是分位精度，用 2 位。

举例：某块云盘原始 `0.38421`、抹零 `0.38421`、应付 `0.00` —— 显示成 6 位才看得出
「不是漏算，是整笔被抹零了」。

> 明细行没有稳定主键：`BillDetailId` 返回 `-`、`BillID` 是空串。前端用
> `InstanceNo + ConfigurationCode + ElementCode + 序号` 合成键。

## 接口限流与缓存

BytePlus 的 OpenAPI 限流很紧 —— 实测并发 12 打过去，24 个请求里 19 个被拒
（错误码 `AccountFlowLimitExceeded`）。因此：

* `app/api.py` 里有**全局节流**（默认 4 req/s，可用 `BYTEPLUS_MIN_INTERVAL` 调）
  加撞限流后的指数退避重试。加节流后同样 24 个请求 0 失败、5.8 秒完成。
* **已结账的月份金额不会再变**，所以永久缓存到 `cache/history.json`。
  每次刷新只重新查"当月"这一个账期。
* 启动时预热一次缓存（3 个账号约 5 秒）；之后重启直接读缓存。

删掉 `cache/` 目录是安全的，只会导致下次启动重新扫一遍。

## 认证

自带登录页 `login.html`，底层还是 HTTP Basic —— **服务端不存任何 session/cookie**，
每个请求自带 `Authorization` 头。

流程：登录页校验凭据 → 存进当前标签页的 `sessionStorage` → 跳主页；
主页每次请求带上这个头，收到 401 就自动跳回登录页。**关闭标签页即自动登出。**

| 路径 | 是否需要认证 | 说明 |
|---|---|---|
| `/`、`/login.html`、`/detail.html`、`/common.js` | 否 | 只是页面外壳和工具函数，不含任何业务数据 |
| `/api/accounts` | **是** | 总览数据 |
| `/api/detail` | **是** | 明细数据 |
| `/api/verify` | **是** | 供登录页校验凭据，不返回业务数据 |
| `/api/health` | 否 | 给监控用，不返回业务数据 |

细节：

* 密码用 `hmac.compare_digest` 定时安全比较，用户名也一样比（避免用时间猜用户名）
* 同一 IP 连续失败 8 次后锁定 5 分钟，防公网撞库
* **反代下的真实 IP**：限流和日志用 `Handler.client_ip()` 取 IP。只有当对端是
  回环地址（`TRUSTED_PROXY_IPS`，即本机 nginx）时才采信转发头，直连请求一律用
  真实 peer —— 否则谁都能塞一个 `X-Forwarded-For` 绕过限流。
  优先读 `X-Real-IP`（nginx 用 `$remote_addr` 覆写，客户端伪造不了）；
  退到 `X-Forwarded-For` 时**取最后一段**，因为 `$proxy_add_x_forwarded_for`
  是把真实 peer 追加在客户端原有值末尾，取第一段等于直接采信伪造内容。
  没有这段处理的话，上了 nginx 之后所有请求都来自 `127.0.0.1`，
  限流会从「按 IP」退化成「全局」—— 任何人连错 8 次锁掉所有人。
* 401 响应**故意不带** `WWW-Authenticate` —— 带了浏览器会抢先弹它自己的原生
  登录框，`login.html` 就没机会显示。`curl -u` 是抢先发凭据的，不依赖这个挑战头，
  所以脚本和监控照样能用：

  ```bash
  curl -s -u admin:密码 http://127.0.0.1:8787/api/accounts
  ```

* 没配 `username`/`password` 时（本机自用的默认情况）不需要登录，直接进主页

线上已启用 HTTPS（TLSv1.2/1.3，Let's Encrypt），所以 Basic Auth 的凭据是在
加密信道里传的。**本机以裸 HTTP 跑时不要暴露到局域网**——那种情况下 base64
等于明文。

> 为什么把凭据放 `sessionStorage` 而不是签名 token：服务端因此完全无状态，
> 且在 HTTPS 下两者抗嗅探能力相同。token 的额外收益只在明文信道里才体现，
> 而明文信道本身已经被 HTTPS 解决了。

## 安全说明

* AK/SK **不会**出现在任何 HTTP 响应里，页面只显示脱敏 AK（`AKAPOG...kMWI`）
* `Account.sk` 标了 `repr=False`，SK 不会随 traceback / 日志泄漏
* 日志不打印 `Authorization` 头
* 没配用户名密码时**拒绝**监听非回环地址（防漏配密码就暴露公网）
* 线上传输走 HTTPS（TLSv1.2/1.3）；应用只监听 `127.0.0.1`，**公网唯一入口是 nginx**
* 服务以专用用户 `byteplus` 运行，非 root；systemd 单元开了
  `ProtectSystem=strict` / `ProtectHome` / `NoNewPrivileges`，只有 `cache/` 可写
* `cred.xlsx`、`config.json`、`cache/`、`*.pem` 都在 `.gitignore` 里

⚠ **上传代码时不要用 `scp -r .`** —— SSH 私钥（`byteplus-kuromi-portal.pem`）就在项目
根目录，`.gitignore` 只管 git、不管 scp。按 [deploy/DEPLOY.md](deploy/DEPLOY.md)
第 3 步逐项列出要传的东西。

按需求，`cred.xlsx` 和 `config.json` 都是**明文不加密**的。所以文件权限是关键防线：

```bash
# Linux
chmod 600 cred.xlsx config.json
```

```bash
icacls cred.xlsx /inheritance:r /grant:r "%USERNAME%:(R,W)"
```

## 文件结构

```
Byteplus/
├── run.bat                     本机双击启动
├── config.json                 配置(需自行从 example 复制，已 gitignore)
├── config.example.json         配置模板
├── cred.xlsx                   账号凭据(明文，已 gitignore)
├── cache/history.json          历史账期缓存(可删)
├── app/
│   ├── server.py               HTTP 服务 + 路由 + 认证接入 + 启动预热
│   ├── api.py                  签名(Signature V4) + 全局节流 + 接口封装
│   ├── billing.py              原授信推算 + 历史缓存 + 单账号组装
│   ├── auth.py                 HTTP Basic + 失败节流
│   ├── config.py               config.json + 环境变量
│   ├── creds.py                读 cred.xlsx
│   ├── login.html              登录页(含 returnTo 校验)
│   ├── index.html              总览页
│   ├── detail.html             账单明细页
│   └── common.js               两个页面共用的工具函数
├── deploy/
│   ├── DEPLOY.md               阿里云 ECS 部署步骤
│   └── byteplus-billing.service systemd 单元文件
└── script/
    └── byteplus_billing.py     原有命令行工具(独立，本系统不依赖它)
```

`app/` 完全独立，不 import `script/` 下的任何东西；`script/byteplus_billing.py`
保持原样，可以单独使用。

## 当前不做的事

* 不落库、无历史趋势图（只缓存历史账期金额用于推算授信，不做趋势展示）
* 无多用户/权限体系（单个共享账号）
* 无告警、无导出
* 现金余额（`QueryBalanceAcct`）已按需求移除 —— 授信下发模式下用不到
* 账号 CRUD（在网页里增删改 `cred.xlsx`）尚未实现。原先的主要阻塞点是
  「新增/修改账号时 SK 要经浏览器传到服务器，裸 HTTP 下等于明文过公网」；
  **上了 HTTPS 之后这个阻塞点已解除**，剩下的是并发写 xlsx、双份真相
  （服务器改了就不能再 scp 覆盖）、以及 AK 变更时要清 `cache/` 里对应 UID 的
  历史缓存（否则原授信额度会按旧账号的消费算错）
