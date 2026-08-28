# CLAUDE.md

给新会话的交接说明。**架构和数据口径看 [README.md](README.md)，踩坑和事故看
[deploy/DEPLOY-NOTES.md](deploy/DEPLOY-NOTES.md)** —— 这份只放那两处没有、
且从代码里不便宜推导出来的东西。

## 这是什么

多 BytePlus 账号的授信看板：**原授信额度 / 授信余额 / 已用 / 当期消费**，
点「明细 ›」进 `/detail.html` 看日消费柱状图 + 资源级明细（按日期分组）。

纯 Python 标准库 + `openpyxl`，**没有 Web 框架**。线上
<https://kuromicloud.top/>（阿里云 ECS 马来西亚 + nginx + Let's Encrypt）。

```bash
python app/server.py --open     # 本机跑；无 config.json 时只监听回环、不启用登录
```

## 已验证的 API 事实 —— 别重新推导

全部是拿真实账号实测出来的，几乎每一条都曾经踩错过。

**消费金额取 `PosttaxAmount`。** 同一条账单里：`OriginalBillAmount` 1.976589（抹零前）、
`RoundAmount` 0.296589（**抹零差额，不是消费额**）、`PosttaxAmount` 1.68（实际应付，
已核对 `== PaidAmount + UnpaidAmount == RealValue`）。曾误用 `RoundBillAmount`，
金额少算 90% 以上。字段优先级见 `app/api.py` 的 `SPEND_FIELDS`。

**`GroupTerm` 的语义和 `script/` 里 CLI 的 help 文本相反 —— help 是错的。**

| | `GroupTerm=0` | `GroupTerm=2` |
|---|---|---|
| 粒度 | 每资源一行 | 每**产品**一行 |
| `InstanceName`/`InstanceNo`/`Region`/`ConfigName`/`Price` | 有 | **全空**（96 字段里 49 个空）|

要资源级就用 `0`。`2` 的粒度等同 `ListBillOverviewByProd`，对账用不上。

**`GroupPeriod=1` 才让 `ExpenseDate` 有值**（`=0` 时该字段是空的）。日维度靠它。

**翻页**：`ListBillOverviewByProd` 的 `Total` 实测返回 `-1`（拿不到总数，只能靠
"某页不满即最后一页"）；`ListBillDetail` 加 `NeedRecordNum=1` 能拿到真实 `Total`。

**「原授信额度」没有对应接口。** 穷举探测过 22 个候选 Action，这个命名空间下只有
`GetQuotaAcctInfo` 和 `QueryBalanceAcct` 真实存在，都只给当前余额。用恒等式推算：

```
原授信额度 = 授信余额 + 累计消费（所有账期）
```

实测精确成立：bytep169 `188.71 + 11.29 = 200.00`、Edward638 `999.92 + 0.08 = 1000.00`。
余额是按消费实时扣减的，所以「已用」就等于累计消费。额度扣减比账单接口略滞后，
推算值可能在几分钱内波动。

**限流很紧**：错误码 `AccountFlowLimitExceeded`。并发 12 时 24 个请求 19 个被拒；
加全局节流（4 req/s）+ 指数退避后 0 失败。**所有调用必须走 `api.call()`**，别绕过。

**不同接口用不同 service**：`GetQuotaAcctInfo` → `bill` / `2020-01-01`；
其余（`QueryBalanceAcct` / `ListBill*`）→ `billing` / `2022-01-01`。

**币种**：quota 接口**不返回币种字段**；balance 返回 `CNY`；overview 返回 `USD`。
按需求统一按 USD 展示（现金余额已整体移除）。

## 不要破坏的不变量

1. **SK 绝不进 HTTP 响应。** `Account.sk` 标了 `repr=False`（防 traceback 泄漏），
   页面只显示脱敏 AK。改动涉及响应体时要重新验证。
2. **仓库里绝不放真实密钥。** 已因此被 GitHub 推送保护拦过一次，密钥只能作废重建。
3. **应用只监听 `127.0.0.1`**，公网唯一入口是 nginx。`--host` 传非回环地址时，
   若未配用户名密码会拒绝启动。
4. **没有静态目录服务**，只白名单 4 个路径（`/`、`/login.html`、`/detail.html`、
   `/common.js`）。因此 `/.git/config`、`/cred.xlsx`、`/../config.json` 全部 401。
   **要加图片/静态资源必须自己写路径穿越防护**，并实测 `/static/../config.json` 被挡。
5. **页面文档不能带 `Cache-Control: no-store`** —— Chrome 会因此禁用 bfcache，
   从明细页返回总览会整页重查。页面用 `REVALIDATE`，业务数据用 `NO_STORE`。
6. **明细合计必须等于概览合计**，页脚显式标 ✓/⚠。这是这个功能的信任锚点，
   `/api/detail` 会额外查一次概览来自对账（明细页拿不到总览那份数据）。
7. **`allow_reuse_address` 必须按平台取值** `(os.name != "nt")`。两个平台的
   `SO_REUSEADDR` 语义相反，写死任一个都会出问题（细节见 DEPLOY-NOTES）。
8. **`app/` 不依赖 `script/`**。`script/byteplus_billing.py` 是独立的命令行工具。

## 已定下的决策 —— 别再提被否过的方案

用户已明确选择，除非他主动提起，不要重新建议：

| 议题 | 决定 |
|---|---|
| `cred.xlsx` 加密 | **不加密**，保持明文 xlsx（提过加密方案，被否） |
| 现金余额 | **移除**（授信下发模式用不到，`QueryBalanceAcct` 已不再调用） |
| CSV 导出 | 暂不做 |
| 明细展现 | **独立页面** `/detail.html`，不是遮罩弹层（做过弹层版，被要求改） |
| 消费列 | 纯展示**不可点**，下钻入口只有行尾「明细 ›」按钮 |
| 日消费图 | **当天应付合计**，不拆按量/包月（拆过一版，被要求合并 —— 明细表的「计费方式」列已逐行说明） |
| 部署方式 | **`git pull` + `systemctl restart` 两条命令**，不要部署脚本（明确拒绝过） |
| 原授信额度来源 | 用「余额 + 累计消费」推算，不在表里手填 |

## 部署

代码在私有仓库 `github.com/SDZ0Z6/byteplus`，分支 **`main`**。用户自己 push。

```bash
# 服务器
cd /opt/byteplus-billing && git pull && systemctl restart byteplus-billing
systemctl is-active byteplus-billing && curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/health
```

**不会冲突**：服务器只做 checkout 从不编辑代码；两边真正不同的三样
（`cred.xlsx` / `config.json` / `cache/`）全在 `.gitignore` 里，`git pull` 不碰。

**重启后必须看 `is-active` 和 `/api/health`** —— 有过「命令没报错、服务其实在
崩溃循环」的经历。

## ⚠ 未完成

**轮换 `bytep169@sapsh.com`（UID 3001315456）的 AK/SK。** 那把密钥曾硬编码在
`script/byteplus_billing.py`，首次 push 时被 GitHub 拦下，但对象在被拒前已上传到
GitHub 服务器。历史已用 `git-filter-repo` 清干净（43 个对象扫描 0 命中），
但密钥本身需要在 BytePlus 控制台作废重建。顺序：先建新的 → 更新 `cred.xlsx`
→ 确认面板正常 → 再停旧的。

## 工作方式

- **动手前先把决定架构的问题一次问清**（用户明确要求过），不要边做边问。
- 结论要拿实测支撑。这个项目里几乎每个"想当然"都错过一次：字段语义、
  `GroupTerm` 标签、平台差异、视口为 0 的假回归。**验证过再说结论。**
- 用户会自己 push、自己在控制台操作密钥和安全组；服务器上的读写操作会先征求同意。
