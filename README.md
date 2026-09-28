# BytePlus 授信管理系统

多账号的**原授信额度 · 授信余额 · 已用 · 消费**总览。纯 Python 标准库实现，
唯一外部依赖是读 xlsx 的 `openpyxl`。

金额统一按**美金 USD**显示（所有账号都是美金授信）。

## 线上地址

自有域名 + 阿里云 ECS（马来西亚·吉隆坡）+ nginx + Let's Encrypt。
**真实域名和服务器 IP 不写进仓库**，文档里用 `<域名>` / `<服务器IP>` 占位。
部署步骤见 [deploy/DEPLOY.md](deploy/DEPLOY.md)，踩坑与遗留项见
[deploy/DEPLOY-NOTES.md](deploy/DEPLOY-NOTES.md)。

代码托管在私有仓库 `github.com/SDZ0Z6/byteplus`，分支 `main`。发布流程：

```bash
# 本地
git push

# 服务器
cd /opt/byteplus-billing && git pull && systemctl restart byteplus-billing
systemctl is-active byteplus-billing && curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/health
```

`cred.xlsx` / `config.json` / `cache/` 都在 `.gitignore` 里 —— 它们只存在于服务器上，
`git pull` 永远不会碰。这也是本地和服务器不会冲突的原因：服务器只做 checkout，
从不编辑代码，两边真正不同的东西 git 根本不管。

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

| EMAIL | UID | AK | SK | STATUS |
|-------|-----|----|----|--------|
| a@example.com | 3001315439 | AKAP… | （密钥） | 启用 |

`STATUS` 是软删除标记，**可选**：没有这一列、或某行该格为空，都算「启用」；
只有明确写了停用值（`停用`/`disabled`/`0`/`off`/`否`…）才算停用。
方向是故意选的 —— 手工加的行不会因为忘填 STATUS 而凭空消失。
这一列由[账号管理页](#账号管理accountshtml)自动维护，老表第一次被写入时会自动补上表头。

`EMAIL` 可以为空（只用来在列表里认人），`UID`/`AK`/`SK` 必填。

加/改账号后**点页面「刷新」即生效，不用重启** —— 每次请求都会重读表格。

## 界面（Light Mode + 侧边栏）

**固定浅色单主题**，没有 `prefers-color-scheme` 分支。主色 `#1664ff`，页面底
`#f5f7fa`，面板纯白。`:root` 上的 `color-scheme: light` 不是可选项 —— 少了它，
系统处于深色模式时 Chrome 会把 `<input type=month>`、`<select>` 和滚动条按深色
渲染，于是页面自己是白底、原生控件却是黑的。

左侧栏三项（总览 / 账单 / 账号管理），底部是「退出登录」，可收起成 64px 的图标条。
收起状态存 `localStorage` 的 `bp_side`，**必须在 `<head>` 的内联脚本里读** ——
等到 `common.js` 执行时已经画过一帧展开态，切页时侧边栏会闪一下。
窄屏（< 900px）侧边栏变抽屉：汉堡按钮开，点遮罩或按 Esc 关，此时「收起」按钮
自动隐藏（抽屉模式下它没有意义）。

侧边栏由 `common.js` 的 `initShell()` 渲染，而不是两个页面各写一份 HTML ——
导航项、退出按钮的显示条件、收起逻辑都得两页一致，存两份早晚有一份漏改。

登录页是独立布局（无侧边栏）：左侧品牌 + 配图（`app/static/Seedance_Login_*.png`），
右侧表单；窄屏隐藏左侧那块图，品牌改到表单顶部 —— 手机上让人立刻能输入比看图重要。
输入框保留 1px 灰边框，focus 时边框转主色 + 一圈淡蓝光晕 —— 这条 focus 规则必须写在
登录页自己的 `<style>` 里并带 `.card` 前缀：`app.css` 的 `input:focus` 和 `.card input`
特异度打平 (0,1,1)，而本页的 `<style>` 在后面，平手时后者胜，边框会一直是灰的。

侧边栏当前项与 hover 的底色、字色相同，只靠字重 600/500 区分（按要求不加左侧标记条）。

**页面上不放说明文字。** 总览页和账单页底部原先各有一段口径说明，已按要求移除；
口径、精度、排序语义这些只写在本文档里。表格下方的合计条不算说明文字 ——
那是对账结果（✓/⚠），是这个功能的信任锚点，不能删。

**hover 一律用主色高亮，不压暗。** 有个特异度陷阱值得记住：`app.css` 里
`button:hover:not(:disabled)` 是 (0,2,1)，比 `.someclass:hover` 的 (0,2,0) 高，
所以图标类按钮（`.nav-i` / `.hamb` / `.eye`）自定义 hover 时选择器必须再带一个类
前缀（`.side .nav-i:hover`），否则会被刷成主色实底、配蓝字直接不可读。已经踩过一次。

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

## 账单页（`/bill.html`）

从侧边栏「账单」进入，或在总览页点账号名（会带上该账号和当前账期）。四部分：

1. **筛选** —— 账号下拉（单选，默认第一个）+ 账期，两者都同步到 URL
2. **账号信息** —— 邮箱 / UID / 脱敏 AK，加原授信额度 / 授信余额 / 已用 /
   当期消费四张卡。授信部分直接复用 `billing.build_account`，与总览页同一口径 ——
   在这里另算一套迟早会和总览页对不上
3. **日消费趋势图** —— 每天一根柱，值是当天应付合计（不区分计费方式），
   当月只画到今天
4. **资源级明细表（扁平，可排序）** —— 日期 / 产品 / 实例ID / 地域 / 规格 /
   计费方式 / 单价 / 用量 / 原始金额 / 抹零 / 应付金额 / 项目。点表头升降排序
   （金额列默认降序，日期和文本默认升序，**空值恒定垫底、不随升降翻转**），
   另有关键字搜索、分页（50/100/全部）、「只看有消费的行」开关，表头滚动时固定

用 `GroupTerm=0 + GroupPeriod=1`：前者保住资源级字段（`GroupTerm=2` 会把
`InstanceName`/`InstanceNo`/`Region`/`ConfigName`/`Price` 全部清空），后者让
`ExpenseDate` 有值。**一次调用同时喂图和表。**

> **这张图回答的是「哪天产生了账」，不是「哪天用了多少」。** 包月/预付资源会把整笔
> 费用记在**购买或续费当天**，所以某天可能明显偏高（实测 2026-08-04 的 `52.16` 里
> 有 `51.15` 是包月入账）。展开那天看**计费方式**列即可分辨：`Pay-as-you-go` 是当天
> 真实用量，`Yearly/monthly` 是一次性入账。图上不再拆分这两类 —— 表格里逐行都标了。

### 图表的两个实现细节

* **柱子直接用主色 `#1664ff`**：界面已固定 Light Mode，只需对白色面板达标 ——
  实算对比度 4.89:1（图形元素要求 3:1，小字文本要求 4.5:1），所以一色通用，
  不用再像双主题时期那样给图元单独配一档蓝。
* **非零柱至少 2px**：包月入账那天会把纵轴顶到 `52.16`，小额日子（实测 `0.04`、
  `0.48`）按比例算出来不到 1px，和「零」完全看不出区别，等于把数据弄丢了。
  加了最小高度后小额可见，零值仍严格渲染为 0，两者可区分。

* 真实 URL：`/bill.html?uid=<uid>&period=<YYYY-MM>` —— 可收藏、可分享、
  浏览器返回键天然回到总览，标签页标题是账号邮箱
* **按需加载** —— 不点开就不查
* 页面上换账期会同步改写 URL，刷新后还是同一个月
* 表格下方的合计条把明细合计与**概览合计**对账，显式标 ✓ 或 ⚠
* **按返回键回总览不会重新查询**（见下）

### 对账数据为什么由服务端给

账单页是独立页面，拿不到总览页那份数据。所以 `/api/detail` 会额外查一次概览
（约 +0.25 秒）并把合计一起返回，页面据此自我核对。另外两条路都不行：前端自己调
`/api/accounts` 要拉全部账号（N×2 次调用，太贵）；用 URL 传期望值不可信、能被改。

顶部授信卡虽然来自同一次请求（`build_account`），但对账那一栏**不复用**它：
已结账月份的 `selected_spend` 可能来自本地缓存，拿缓存值去对账等于自己跟自己对，
校验就废了。所以那次概览查询是独立的、实时的，宁可多花 0.25 秒。

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
会存进 `sessionStorage`，返回时直接拿来渲染，不打接口；快照超过 10 分钟作废。

「返回」有两条路径，都要覆盖：

| 操作 | `navigation.type` | 怎么识别 |
|---|---|---|
| 浏览器返回/前进键 | `back_forward` | 直接判断类型 |
| 明细页的「返回总览」链接 | `navigate` | 链接带 `?restore=1` 显式标记 |

第二条容易漏 —— 点链接属于**前进导航**，拿不到 `back_forward`。本来想用
`document.referrer` 判断来源，但响应头设了 `Referrer-Policy: no-referrer`，
它恒为空，所以只能用显式标记。`restore=1` 用完会 `replaceState` 洗掉，
不会残留在地址栏或被收藏。

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

## 账号管理（`/accounts.html`）

侧边栏「账号管理」进入。表格列出**全部**账号（含已停用的）：账号 / UID /
脱敏 AK / 状态，行尾一个启用或停用按钮。这一页**不打任何 BytePlus 接口**，
只读 `cred.xlsx`，所以是秒开的、也不吃限流配额。

### 停用（软删除）

写的是 `cred.xlsx` 的 `STATUS` 列，不删行、不动密钥。停用之后：

* 总览页不再统计它（`account_count` 和各项合计都不含它）
* 账单页的账号下拉里没有它
* `/api/detail?uid=<停用的 uid>` 直接返回 **403** —— 老书签也看不到
* 启动预热跳过它，不白花接口配额

已停用的账号仍留在管理页里（整行压灰 + 「已停用」），点「启用」即恢复 ——
软删除本来就该能撤回。停用是**点两次**的：第一次点击变成「确认停用？」，
4 秒内不点第二次就自动取消。没用 `window.confirm()`：原生弹窗会挡住整页，
而且在没有窗口焦点的环境里根本不弹出来（页面看起来就是卡住了）。

### 新增账号

右上角「+ 新增账号」弹出小框，填邮箱（可空）/ UID / AK / SK。
**保存前先拿这对 AK/SK 实调一次 `GetQuotaAcctInfo`**（最轻的那个接口，约 0.3 秒），
并且比对返回的 `AccountID` 和填入的 UID —— 实测这两者恒等。
验证不过就不写文件，错误原文直接显示在弹层里（签名不匹配 / token 无效
是两种不同的错填，分得清）。

只验证「密钥能用」是不够的：UID 是历史缓存的键，配错了会把另一个账号的消费
算到这个账号头上，而且要到很久以后对账才会发现。

UID 和 AK 都查重：UID 重复会让 `load_accounts` 直接报错、整个系统起不来；
AK 重复几乎一定是复制粘贴错了（一把 AK 只属于一个账号）。

### 写入 cred.xlsx 的安全网

**服务器上那份 `cred.xlsx` 从此是唯一真相** —— 网页上加的账号只存在那里，
所以不能再从本地覆盖上去（要同步就从服务器往下拷）。

这个文件写坏了等于所有账号一起丢，所以每次写入都是同一条路径
（`creds.py` 的 `_write_workbook()`，唯一的写入出口）：

1. 取进程内写锁 —— 两个标签页同时点「停用」会各读各写，后写的覆盖先写的
2. 备份到 `cache/cred-<时间戳>.xlsx` 并 **验证备份能被解析**（存不回去的备份等于没有备份），只留最近 10 份
3. 内容先在内存里拼完整，再一次 `write` + `fsync` 覆盖原文件
4. **回读解析验证**，失败就从备份回滚，并在错误里给出备份路径

这里是**原地覆盖**而不是「临时文件 + `os.replace`」：原子替换需要目录可写，
而线上 `ProtectSystem=strict` 只放开了 `cred.xlsx` 这一个文件，项目目录是只读的。

> **写入前会清掉表格里的超链接。** Excel 会把邮箱自动变成 `mailto:` 链接，而
> openpyxl 在可写模式下会把「有超链接、但没有值」的单元格**用超链接目标当值
> 创建出来**。在表格里删过行之后就会留下这种孤立超链接，于是一保存就凭空多出
> 一行 `mailto:xxx@yyy.com` —— 它有内容却没有 UID/AK/SK，`load_accounts` 报错、
> 写入被回滚，此后再也加不了账号。实测踩到过，所以写入前一律清超链接、
> 并把最后一行真实数据之后的内容清空。

### 还没有的功能

**改密钥（轮换 AK/SK）目前仍要手工编辑 xlsx**：新增会因 UID 重复被拒，而没有
「编辑」功能。轮换的正确顺序是先在控制台建新密钥、手工改表、确认面板正常、
再停掉旧密钥。**硬删除**也没做 —— 停用已经够用，且可撤回。

## 认证

自带登录页 `login.html`，底层还是 HTTP Basic —— **服务端不存任何 session/cookie**，
每个请求自带 `Authorization` 头。

流程：登录页校验凭据 → 存进当前标签页的 `sessionStorage` → 跳主页；
主页每次请求带上这个头，收到 401 就自动跳回登录页。**关闭标签页即自动登出。**

| 路径 | 是否需要认证 | 说明 |
|---|---|---|
| `/`、`/login.html`、`/bill.html`、`/accounts.html` | 否 | 只是页面外壳，不含任何业务数据 |
| `/common.js`、`/app.css` | 否 | 共用的工具函数与样式 |
| `/static/<文件名>`、`/favicon.ico` | 否 | 图片。登录页要在**登录之前**显示品牌图，放到认证之后只会得到一片 401 占位框 |
| `/api/accounts` | **是** | 总览数据 |
| `/api/account-list` | **是** | 账号列表；只读 `cred.xlsx`，不打 BytePlus 接口。`scope=all` 才含已停用的（只有账号管理页用） |
| `POST /api/account/add` | **是** | 新增账号；先实调接口验证密钥，通过才写文件 |
| `POST /api/account/status` | **是** | 启用/停用（软删除） |
| `/api/detail` | **是** | 明细数据 |
| `/api/verify` | **是** | 供登录页校验凭据，不返回业务数据 |
| `POST /login` | 自带校验 | 塔台一键登录：收表单凭据，校验同 `/api/verify`，见[下文](#塔台一键登录post-login) |
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

### 塔台一键登录（`POST /login`）

塔台在用户浏览器里自动提交一个表单过来（`application/x-www-form-urlencoded`，
字段 `username` / `password`）。表单带不上 `Authorization` 头，所以凭据走请求体：

| 情况 | 响应 |
|---|---|
| 校验通过 | `200` 一小段 HTML：把 token 写进 `sessionStorage`，再 `location.replace("/")`。之后就和从登录页进来的完全一样 |
| 用户名或密码错 | `302` → `/login.html?err=1`（这个值是和塔台约定的） |
| 该 IP 正在锁定中 | `302` → `/login.html?err=2` |
| 不是 urlencoded 表单 | `415` JSON —— 集成写错了，**不计**失败次数 |

* **校验就是 `/api/verify` 那一套**（`Handler._check_credentials()`）：同一个比较函数、
  同一个失败计数。两条路径撞密码会累计到同一个 IP 的锁定上，谁也绕不开谁。
* token 按 `login.html` 的算法拼（`base64(utf8(username + ':' + password))`），再拿
  **它本身**去走 Basic 校验 —— 验过的就是要存进去的那一串，不会出现「这里通过了、
  之后每个请求却 401」。
* 写进页面时按塔台要求做了 JSON 转义，**另外再把 `< > &` 转成 `<` 这类转义**
  （`_js_str()`）：光靠 `json.dumps` 挡不住值里的 `</script>` —— HTML 解析先于 JS，
  引号拦不住结束标签。token 是 base64，本来就只有 `A-Z a-z 0-9 + / =`，这层是纵深防御。
* 成功页带着凭据，所以是 **`no-store`**。这是「页面文档不能 no-store」那条的唯一
  例外，并不冲突：它立刻被 `replace` 掉，本来就进不了 bfcache。用 `replace`
  而不是赋值，是为了这个 POST 结果页不留在历史记录里。
* **没有 CSRF token、没有验证码，是有意的**（塔台的要求：自动提交的表单带不上）。
  这不会让它比 `/api/verify` 好攻破 —— 不知道密码照样进不来；全站只有一个共享账号，
  「把受害者登录成攻击者的账号」（login CSRF）也无从谈起。
  真正多出来的只有一点：任何网站都能让访客的浏览器往这里提交错误密码，把那个
  出口 IP 锁 5 分钟。以前做不到 —— 跨站 `fetch` 要带 `Authorization` 头就得先过
  CORS 预检，而本服务不处理 `OPTIONS`（实测 `OPTIONS /api/verify 501`，真正的请求
  根本没发出来）。要堵的话可以按 `Origin` 头只放行塔台的域名，这不是 CSRF token，
  自动提交照样能过 —— **前提是塔台页面没设 `Referrer-Policy: no-referrer`**：
  实测设了之后浏览器发的是 `Origin: null`，白名单会把塔台自己也挡掉。
* **改登录密码要同步给塔台。** 它存着这份密码；不同步的话每次一键登录都算一次
  失败，同一个出口 IP 攒够 8 次，手工登录的人也会一起被锁 5 分钟。

给塔台的集成要求（前三条是拿浏览器实测出来的）：

* 地址必须写 **`https://<域名>/login`**。写成 `http://` 会被 nginx 301 到 https，而
  浏览器跟 301 时会把 POST 改成 GET、丢掉表单体 —— 用户最后停在一个
  `{"error": "需要用户名和密码"}` 的页面上，应用日志里只看到一条 `GET /login 401`。
* 必须**顶层打开**（当前标签页或新标签页），不能提交进 iframe：所有响应都带
  `X-Frame-Options: DENY`。那种情况下服务端校验照样通过（日志里是
  `POST /login 200`），但页面被浏览器拦下，token 写不进去。
* 密码若含非 ASCII 字符，塔台的页面必须是 UTF-8（或 `<form accept-charset="utf-8">`），
  否则浏览器按页面编码（比如 GBK）提交，正确的密码也会被判错。纯 ASCII 密码无所谓。
* `method="post"`。GET 会把密码放进 URL，进 nginx 访问日志。

## 安全说明

* AK/SK **不会**出现在任何 HTTP 响应里，页面只显示脱敏 AK（`AKAPOG...kMWI`）
* `Account.sk` 标了 `repr=False`，SK 不会随 traceback / 日志泄漏
* 日志不打印 `Authorization` 头
* 没配用户名密码时**拒绝**监听非回环地址（防漏配密码就暴露公网）
* 线上传输走 HTTPS（TLSv1.2/1.3）；应用只监听 `127.0.0.1`，**公网唯一入口是 nginx**
* 服务以专用用户 `byteplus` 运行，非 root；systemd 单元开了
  `ProtectSystem=strict` / `ProtectHome` / `NoNewPrivileges`，只有 `cache/` 可写
* `cred.xlsx`、`config.json`、`cache/`、`*.pem` 都在 `.gitignore` 里
* 写 `cred.xlsx` 只有一条路径（`creds._write_workbook`）：备份 + 回读校验 + 失败回滚，
  见[写入 cred.xlsx 的安全网](#写入-credxlsx-的安全网)
* **SK 只进不出**：新增账号时 SK 由浏览器 POST 上来（HTTPS），之后既不回显、
  也不写日志 —— 写操作的响应只含 uid / 邮箱 / 脱敏 AK / 启用状态
* `/static/` 是**唯一按文件名取文件的入口**，防护写在 `_serve_static()`：文件名
  正则只放过一层普通文件名、**故意不做 unquote**、realpath 复核最终位置、扩展名
  白名单。实测 `/static/../config.json`、`/static/..%5cconfig.json`、
  `/static/%2e%2e%2fconfig.json`、`/static/sub/x.png`、`/static/zz.txt` 全部 404

⚠ **别在源码里写真实密钥。** 首次推 GitHub 时就因为 `script/byteplus_billing.py`
里硬编码了一对在用的 AK/SK 被推送保护拦下，且密钥在被拦前已上传到对方服务器、
只能作废重建。经过见 [DEPLOY-NOTES](deploy/DEPLOY-NOTES.md)。

⚠ **手工传文件时不要用 `scp -r .`** —— SSH 私钥在 `.pem/` 目录下，
`.gitignore` 只管 git、不管 scp。按 [deploy/DEPLOY.md](deploy/DEPLOY.md)
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
│   ├── bill.html               账单页(筛选 + 趋势图 + 可排序明细表)
│   ├── accounts.html           账号管理页(列表 + 停用 + 新增弹层)
│   ├── app.css                 设计系统(Light Mode 单主题 + 侧边栏 + 表格 + 图表)
│   ├── common.js               共用工具函数 + 侧边栏渲染 + 排序
│   └── static/                 图标与登录页配图(唯一按文件名取文件的目录)
├── deploy/
│   ├── DEPLOY.md               阿里云 ECS 部署步骤
│   ├── DEPLOY-NOTES.md         踩坑记录 / 待办 / 遗留项 / 排查经验
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
* 账号**新增**和**停用（软删除）**已经有了，见[账号管理](#账号管理accountshtml)。
  还没有的是：**改**（轮换 AK/SK 仍要手工编辑 xlsx）和**硬删除**。
  当年记的三个阻塞点现在的状态：SK 过公网 —— 已上 HTTPS，解除；并发写 xlsx ——
  进程内写锁 + 备份/回滚，解除；双份真相 —— **已定：服务器那份是唯一真相**，
  不再从本地覆盖上去。AK 变更要清对应 UID 的历史缓存这条仍然成立，
  所以轮换密钥时别顺手改 UID
