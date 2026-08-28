# CLAUDE.md

给新会话的交接说明。**架构和数据口径看 [README.md](README.md)，踩坑和事故看
[deploy/DEPLOY-NOTES.md](deploy/DEPLOY-NOTES.md)** —— 这份只放那两处没有、
且从代码里不便宜推导出来的东西。

## 这是什么

多 BytePlus 账号的授信看板：**原授信额度 / 授信余额 / 已用 / 当期消费**，
侧边栏「账单」或总览页点账号名，进 `/bill.html`：账号/账期筛选 + 授信卡 +
日消费趋势图 + 扁平可排序明细表。`/accounts.html` 是账号管理：列出全部账号、
**停用(软删除)**、**新增账号(先实调接口验证密钥才写 cred.xlsx)**。
界面固定 **Light Mode**，主色 `#1664ff`。

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
4. **白名单路由 + 一个受控的静态目录**。页面和资源：`/`、`/login.html`、
   `/bill.html`、`/accounts.html`、`/common.js`、`/app.css`；写操作是
   `POST /api/account/add` 和 `POST /api/account/status`（**先读完请求体再校验
   凭据** —— 反过来会让 401 响应和未读的请求体在 keep-alive 连接上错位）；
   图片走 `/static/<文件名>` —— 这是
   **唯一按文件名取文件的入口**，防护全在 `_serve_static()`：文件名正则只放过
   一层普通文件名、**故意不做 unquote**（`%2e%2e` 因此进不来；先解码再校验等于
   自己开一个双重解码绕过口）、realpath 复核最终位置、扩展名白名单。
   动过这里就必须重测：`/static/../config.json`、`/static/..%5cconfig.json`、
   `/static/%2e%2e%2fconfig.json`、`/static/sub/x.png`、`/static/zz.txt`
   应全部 404；`/cred.xlsx`、`/.git/config`、`/config.json` 应 401。
5. **页面文档不能带 `Cache-Control: no-store`** —— Chrome 会因此禁用 bfcache，
   从明细页返回总览会整页重查。页面用 `REVALIDATE`，业务数据用 `NO_STORE`。
6. **明细合计必须等于概览合计**，页脚显式标 ✓/⚠。这是这个功能的信任锚点，
   `/api/detail` 会额外查一次概览来自对账（明细页拿不到总览那份数据）。
7. **`allow_reuse_address` 必须按平台取值** `(os.name != "nt")`。两个平台的
   `SO_REUSEADDR` 语义相反，写死任一个都会出问题（细节见 DEPLOY-NOTES）。
8. **`app/` 不依赖 `script/`**。`script/byteplus_billing.py` 是独立的命令行工具。
9. **写 `cred.xlsx` 只能走 `creds._write_workbook()`**，别在别处 `wb.save()`。
   它负责：进程内写锁 → 备份到 `cache/` 并验证备份可解析 → 内存里拼完整字节
   → 一次写入 + fsync → **回读解析验证** → 失败从备份回滚。
   这个文件写坏等于所有账号一起丢，安全网不是可选的。
10. **写入前必须清掉表格超链接**（`_clean_sheet`）。Excel 把邮箱自动变成
   `mailto:` 链接，openpyxl 可写模式会把「有超链接、没有值」的单元格用超链接
   目标当值创建出来 —— 在表里删过行之后就会凭空多出一行 `mailto:xxx`，
   `load_accounts` 判定「有内容但缺 UID/AK/SK」→ 报错 → 写入回滚 → 此后再也
   加不了账号。**踩到过**，删掉这段清理就会复现。
11. **线上 systemd 只放开 `cache/` 和 `cred.xlsx` 两个可写路径**
   (`ProtectSystem=strict` + `ReadWritePaths`)。所以：备份只能写 `cache/`、
   写入只能原地覆盖(目录不可写，用不了临时文件+os.replace)。改动写入逻辑前
   先看 `deploy/byteplus-billing.service`，别在本机能过、线上 EROFS。

## 已定下的决策 —— 别再提被否过的方案

用户已明确选择，除非他主动提起，不要重新建议：

| 议题 | 决定 |
|---|---|
| `cred.xlsx` 加密 | **不加密**，保持明文 xlsx（提过加密方案，被否） |
| 现金余额 | **移除**（授信下发模式用不到，`QueryBalanceAcct` 已不再调用） |
| CSV 导出 | 暂不做 |
| 明细展现 | **独立页面** `/bill.html`（原 `/detail.html` 已删除），不是遮罩弹层（做过弹层版，被要求改） |
| 配色主题 | **只做 Light Mode**，白底 + `#1664ff`；`prefers-color-scheme` 分支已全部删掉，别"顺手加回暗色" |
| 明细表结构 | **扁平表 + 日期列 + 全列可排序**（按日期分组折叠那版已被替换） |
| 账单页账号筛选 | **单账号下拉**，默认第一个（「全部账号」聚合要 ×N 打接口，限流风险，已否） |
| 退出登录位置 | **侧边栏底部**（原来在工具条右侧） |
| 停用状态存哪 | **`cred.xlsx` 的 `STATUS` 列**（提过单独存 json，被否 —— 账号数据只该有一份真相） |
| cred.xlsx 谁是真相 | **服务器那份**。网页新增的账号只在服务器上，不能再从本地覆盖上去；要同步就从服务器往下拷 |
| 新增账号是否验证 | **先实调 `GetQuotaAcctInfo` + 比对 `AccountID` 是否等于填入的 UID，不过就不保存** |
| 管理页的「状态」列 | 只表示**启用/停用**，管理页完全不打 BytePlus 接口（提过顺带显示接口健康，被否 —— 会变得和总览一样慢） |
| 管理页的 AK | **脱敏**，和其它页一致 |
| 停用后的可见性 | **一律拒绝**：总览不算、下拉不列、`/api/detail` 带该 uid 直接 403 |
| 停用的确认方式 | **两段式按钮**（点两次），不用 `window.confirm()` |
| 账号的改 / 硬删 | **不做**。轮换密钥仍是手工编辑 xlsx；停用可撤回，够用了 |
| 页面说明文字 | **不放**。总览页/账单页底部那两段口径说明已按要求删除，口径只写在 README，别再加回页面（表格下方的合计条 ✓/⚠ 不属于说明文字，是不变量 6，不能删） |
| hover 反馈 | **一律主色高亮，不压暗**。注意 `button:hover:not(:disabled)` 特异度 (0,2,1) 会压过 `.someclass:hover` (0,2,0)，图标按钮的 hover 选择器必须再带一层类前缀 |
| 明细表筛选 | 日期区间 + 产品 + 地域 + 关键字 + 只看有消费，全部**纯前端**筛选（不重新打接口）；产品/地域选项从当前数据动态生成 |
| 消费列 | 纯展示**不可点**；下钻入口是**账号名**（行尾「明细 ›」按钮已按要求删除） |
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
