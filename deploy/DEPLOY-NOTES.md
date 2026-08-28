# 运维笔记

[DEPLOY.md](DEPLOY.md) 是「怎么做」，这份是「为什么这么做」和「踩过什么」。
遇到怪现象先翻这里，很多已经有解释。

---

## ⚠ 待办

### 1. 轮换 bytep169 的 AK/SK（未完成）

**背景**：`script/byteplus_billing.py` 曾把一对真实 AK/SK 硬编码在源码里
（`DEFAULT_AK` / `DEFAULT_SK`）。首次 `git push` 时被 GitHub 推送保护拦下：

```
- VolcEngine Access Key ID
    commit: 221f07a…  path: script/byteplus_billing.py:45
```

那把密钥属于 `bytep169@sapsh.com`（UID 3001315456），是 `cred.xlsx` 里**在用**的账号。

**已做**：用 `git-filter-repo --replace-text` 从全部历史清除，扫描 43 个对象
（`--batch-all-objects`，含悬空对象）确认 0 命中；当前文件改为空字符串。

**未做**：推送被拒之前，`Writing objects: 100%` 和 `remote: Resolving deltas: 100%`
都已完成 —— **包含明文密钥的对象已经传到 GitHub 服务器上了**。清理历史让推送通过，
但换不回已经出去的东西。

**要做**：BytePlus 控制台给该账号新建一对 AK/SK → 更新 `cred.xlsx` 并上传 →
**确认面板正常后**再停用旧的（顺序反了会立刻报错）。

> 教训：源码里永远不放真实密钥，哪怕只是「临时方便一下」。
> 仓库是私有的也一样 —— 密钥离开了你控制的边界。

---

## 环境相关

### `certbot` 在 Alibaba Cloud Linux 4 上装不了

只启用了 `alinux4-os` / `alinux4-plus` / `alinux4-updates` 三个仓库，里面没有
certbot，也没有 `epel-release` 可装。硬要用得塞进 venv（系统 Python 由 dnf 管，
直接 pip 装会冲突）。

**改用 acme.sh**：纯 shell、零依赖、自带续期 cron、不碰系统 Python。
代价是它不会自动改 nginx 配置，443 段要手写（DEPLOY.md 第 13 步已给全）。

### `dnf` 是严格模式

```bash
dnf install -y nginx certbot python3-certbot-nginx   # certbot 不存在
# → 整个事务回滚，nginx 也没装上
```

一个包没匹配上就全部失败。分开装，或确认包都存在再合并。

### git 的 `dubious ownership`

部署目录属主是 `byteplus`，而 git 以 root 执行 —— 新版 git 会直接拒绝：

```bash
git config --global --add safe.directory /opt/byteplus-billing
```

---

## 运行时

### `SO_REUSEADDR` 在 Windows 和 Linux 上语义相反

这个坑造成过约 40 秒线上停机，`NRestarts` 涨到 12。

| | Windows | Linux |
|---|---|---|
| 对**存活**的监听者 | 允许双绑（危险）| 拒绝（EADDRINUSE）|
| 对 TIME_WAIT 残留 | — | 允许重绑（重启必需）|

本地开发时为了防「起了第二个实例、请求随机落到旧进程、改完代码却拿到旧数据」，
把 `allow_reuse_address` 关掉了；到 Linux 上就变成重启时绑不上端口、systemd
反复重试。现在按平台取值：

```python
allow_reuse_address = (os.name != "nt")
```

两个性质同时保住：Windows 不会静默双绑，Linux 重启一次成功。

**教训**：本地测不出来的东西，部署会替你测。所以重启后一定要看
`systemctl is-active` 和 `/api/health`，别只看命令有没有报错。

### `cred.xlsx` 每次 scp 都会被重置成 644

`scp` 到 root 会让文件变成 `644 root:root`（部署过程中就复现过一次）。
虽然父目录 `/opt/byteplus-billing` 是 `750 byteplus:byteplus`、机器上也没有
非系统用户，实际暴露面接近零 —— 但属主是 root 时，服务其实是**靠 world-read 位**
才读到它的，哪天父目录权限一松就真暴露了。

所以传完必须跟一条修正，写成一行别拆开：

```bash
scp cred.xlsx root@43.107.53.16:/opt/byteplus-billing/ && \
ssh root@43.107.53.16 "chown byteplus:byteplus /opt/byteplus-billing/cred.xlsx && chmod 600 /opt/byteplus-billing/cred.xlsx"
```

### 部署时的原子切换

如果一次部署里有**新增文件**（比如加 `common.js` 那次），逐个 scp 覆盖会有个
几百毫秒的窗口：`index.html` 已是新版、`common.js` 还没到，这期间访问的人拿到
白屏（`PERIOD_RE is not defined`）。

改用 git 之后这个问题基本消失（`git pull` 一次性更新工作区），但如果哪天又回到
手工传文件，记得走「传到 `app.new/` → `mv` 一次性换过去 → 重启」。

---

## 遗留项

### 1. 443 只监听 IPv4

现状 `0.0.0.0:443`，而 80 还额外监听了 `[::]:80`。域名目前没有 AAAA 记录，
所以没影响。但**一旦加了 AAAA 记录**，IPv6 客户端会走 80 拿到 301、再连 443 时失败。
要支持就补：

```nginx
listen [::]:443 ssl;
```

### 2. 证书在公开 CT 日志里，会被扫

Let's Encrypt 签发的证书会进 Certificate Transparency 公开日志，扫描器盯着它 ——
域名上线几分钟内就有陌生 IP 来摸（已观测到 `35.165.215.140` 摸 `/login.html`
和 `/api/accounts`，拿到 401）。

属正常现象，不必紧张。但这意味着**登录密码强度和失败锁定是真正的防线**：
同 IP 连错 8 次锁 5 分钟。密码建议用长随机串：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(24))"
```

### 3. 想彻底关掉 80 端口

现在 80 必须对 `0.0.0.0/0` 开放 —— acme.sh 用 HTTP-01 校验，Let's Encrypt 会从
不固定的 IP 来访问 `/.well-known/`，限制来源会导致**续期静默失败**，三个月后
证书过期才发现。

换成 DNS-01 就不需要 80：

```bash
export Ali_Key="你的AccessKeyId"
export Ali_Secret="你的AccessKeySecret"
~/.acme.sh/acme.sh --issue --dns dns_ali -d kuromicloud.top --server letsencrypt
```

代价是要给 acme.sh 一对阿里云 AK/SK。

### 4. `.git` 不会被 Web 暴露（已验证，不是遗留项，是确认项）

很多部署栽在 nginx 直接 `root` 到代码目录、`.git/config` 裸奔。这套结构上没这个
问题 —— 应用没有任意静态文件服务，只白名单了 4 个路径，其余先过认证再 404：

```
/.git/config    -> 401
/cred.xlsx      -> 401
/../config.json -> 401
```

改 nginx 时别加 `root` / `try_files` 指向代码目录，这个性质就一直成立。

---

## 排查经验：看着像 bug，其实不是

### `file://` 打开页面报 `PERIOD_RE is not defined`

编辑器直接预览磁盘上的 `bill.html` 时，`/common.js` 和 `/app.css` 解析成
`file:///common.js`、`file:///app.css` 加载失败，于是所有共用函数都未定义、
页面还没样式。**页面必须经服务器访问**，它还依赖 `/api/detail`。
console 里看到 `file:///...` 开头的报错直接忽略。

### 浏览器自动化里量到「柱宽 0px、标签全截断、页面横向滚动」

预览面板没显示时，视口 `clientWidth` 是 **0**，所有 flex 布局都塌成 0 宽。
不是 CSS 问题。量几何前先显式设视口：

```
resize_window(width=1280, height=900)
```

### 浏览器自动化里量到「侧边栏收起了但宽度不变」

预览面板没显示时页面**不产帧**，于是 CSS transition 一启动就永远停在起始值 ——
而运行中的 transition 优先级高于普通声明，所以连 `!important` 之外的规则都压不过它。
表现：`data-side` 已经变成 `mini`、`display:none` 的文字也确实隐藏了（那条没有过渡），
但 `.side` 的 `width` 死活还是 216px；只有内联 `!important` 能改动它。

不是 CSS 的问题。量几何前先把过渡关掉：

```js
const st = document.createElement('style');
st.textContent = '*,*::before,*::after{transition:none !important;animation:none !important}';
document.head.appendChild(st);
```

关掉之后实测 216 → 64 → 216 完全正常。同类陷阱见上面「柱宽 0px」那条 ——
凡是"看着像布局 bug"，先怀疑面板没显示。

### bfcache 在自动化环境里永远不生效

Chrome 在**附加了调试器**时禁用 bfcache，而浏览器自动化正是通过 CDP 连接的。
所以「返回不重查」这个功能没法在自动化里验证 bfcache 那一层 —— 真正起作用的是
`sessionStorage` 快照那一层（这也是为什么要做两层）。

### 服务「启动成功」但其实在崩溃循环

`systemctl restart` 不报错不代表起来了。表现是 `is-active` 显示
`activating (auto-restart)`、`MainPID` 为 0、`NRestarts` 持续上涨。
重启后固定跑这两条：

```bash
systemctl is-active byteplus-billing
curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/health
```
