# 部署到阿里云 ECS（域名 + HTTPS）

当前线上：**<https://kuromicloud.top/>**

这份文档既是现有部署的记录，也是重建步骤。

## 现状速查

| 项 | 值 |
|---|---|
| ECS 地域 | `ap-southeast-8`（马来西亚·吉隆坡）—— **境外节点，不需要 ICP 备案** |
| 系统 | Alibaba Cloud Linux 4（`platform:alnx4`，Anolis 系） |
| Python | 3.11.6 + openpyxl 3.1.5 |
| 域名 | `kuromicloud.top` → `43.107.53.16` |
| 反代 | nginx 1.30.2（`alinux4-updates` 仓库） |
| 证书 | Let's Encrypt，acme.sh v3.1.5 签发，存 `/etc/nginx/ssl/billing.{crt,key}` |
| 续期 | acme.sh 自带 cron，每天 4 次（02:58 / 08:58 / 14:58 / 20:58） |
| 应用 | systemd `byteplus-billing`，`User=byteplus`，绑 **`127.0.0.1:8787`** |
| 安全组 | 开 80 / 443；**8787 已关**（应用只监听回环，外部不可达） |
| 代码管理 | git，私有仓库 `github.com/SDZ0Z6/byteplus`，分支 **`main`** |
| 部署方式 | 服务器 `git pull` + 重启（不再用 scp） |

> 踩过的坑、事故记录、已知遗留项都在 **[DEPLOY-NOTES.md](DEPLOY-NOTES.md)**。
> 出问题先翻那份，很多现象那里已经有解释。

---

## 重建步骤

### 1. 装依赖

`dnf` 是严格模式 —— 一条命令里只要有一个包没匹配上就**整个事务回滚**，
其它包也不会装。所以别把没把握的包混进来。

```bash
sudo dnf install -y python3 python3-pip nginx git
```

```bash
sudo pip3 install openpyxl
```

### 2. 建专用用户和目录

服务持有所有账号的 SK，别用 root 跑。

```bash
sudo useradd -r -s /sbin/nologin byteplus
sudo mkdir -p /opt/byteplus-billing
```

### 3. 拉取代码

目录里有 `cred.xlsx` / `config.json` / `cache/` 这些**不能被覆盖**的文件，而
`git clone` 要求目标目录为空 —— 所以要**原地 init**，不能 clone：

```bash
cd /opt/byteplus-billing
git config --global --add safe.directory /opt/byteplus-billing
git init
git remote add origin https://github.com/SDZ0Z6/byteplus.git
git fetch origin
git checkout -f -b main origin/main
```

`checkout -f` 只覆盖仓库里存在的文件（也就是代码）。`cred.xlsx` 那几个不在
仓库里、又被 `.gitignore` 覆盖，会原封不动留着。

> **`safe.directory` 那行别省。** 目录属主是 `byteplus` 而你用 root 跑 git，
> 新版 git 会判定 `dubious ownership` 直接拒绝执行。

`cred.xlsx` 不在仓库里（也绝不该在），需要单独传，且**必须带上权限修正** ——
scp 到 root 会让它变成 `644 root:root`：

```bash
scp cred.xlsx root@43.107.53.16:/opt/byteplus-billing/ && \
ssh root@43.107.53.16 "chown byteplus:byteplus /opt/byteplus-billing/cred.xlsx && chmod 600 /opt/byteplus-billing/cred.xlsx"
```

### 4. 写配置

```bash
cd /opt/byteplus-billing && cp config.example.json config.json && vi config.json
```

```json
{
  "host": "127.0.0.1",
  "port": 8787,
  "username": "admin",
  "password": "长随机密码"
}
```

`host` 填 `127.0.0.1` —— 只让本机 nginx 连得上。生成强密码：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(24))"
```

> 没配 `username`/`password` 时，服务会**拒绝**监听非回环地址，防止漏配密码就暴露公网。

### 5. 收紧权限

```bash
cd /opt/byteplus-billing
sudo mkdir -p cache
sudo chown -R byteplus:byteplus /opt/byteplus-billing
sudo chmod 750 /opt/byteplus-billing
sudo chmod 600 cred.xlsx config.json
sudo chmod 700 cache
ls -l cred.xlsx config.json    # 应为 -rw------- byteplus byteplus
```

### 6. 装成 systemd 服务

```bash
sudo cp /opt/byteplus-billing/deploy/byteplus-billing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now byteplus-billing
sudo systemctl status byteplus-billing
```

启动时会预热历史账期缓存（推算原授信额度用），首次约几秒到几十秒，
之后重启直接读 `cache/history.json`。

### 7. 域名与 DNS

任意注册商都行。**境外节点不需要备案**；在阿里云买则仍需域名实名认证
（和备案不是一回事，通常几小时）。

加一条 A 记录指向 `43.107.53.16`，然后确认生效：

```bash
nslookup kuromicloud.top
```

用 Cloudflare 的话先保持「仅 DNS」（灰云），别开代理 —— 否则签发和真实 IP 传递都要多绕一层。

### 8. 安全组放行 80 / 443

控制台 → ECS → 安全组 → 入方向，加 TCP `80` 和 `443`。

**80 必须对 `0.0.0.0/0` 开放** —— acme.sh 用 HTTP-01 校验，Let's Encrypt
会从不固定的 IP 来访问 `/.well-known/acme-challenge/`，限制来源会导致**续期失败**。
（想关掉 80 就得改用 DNS-01 校验，见文末。）

### 9. 装 acme.sh

官方是 `curl https://get.acme.sh | sh`，但那等于把远程脚本直接喂给 shell。先下来看一眼：

```bash
curl -fsSL -o /tmp/acme-install.sh https://get.acme.sh && head -40 /tmp/acme-install.sh
```

```bash
sh /tmp/acme-install.sh email=你的邮箱
```

装在 `~/.acme.sh/`，续期 cron 它自己会加。

### 10. nginx 先只配 80（签证书要用）

`/etc/nginx/conf.d/byteplus.conf`：

```nginx
server {
    listen 80;
    server_name kuromicloud.top;

    location /.well-known/acme-challenge/ { root /var/www/acme; }

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo mkdir -p /var/www/acme
sudo nginx -t && sudo systemctl enable --now nginx
```

### 11. 签证书

acme.sh 默认走 ZeroSSL，显式指定 Let's Encrypt：

```bash
~/.acme.sh/acme.sh --issue -d kuromicloud.top -w /var/www/acme --server letsencrypt
```

### 12. 装证书 + 挂自动 reload

```bash
sudo mkdir -p /etc/nginx/ssl
~/.acme.sh/acme.sh --install-cert -d kuromicloud.top \
  --key-file       /etc/nginx/ssl/billing.key \
  --fullchain-file /etc/nginx/ssl/billing.crt \
  --reloadcmd      "systemctl reload nginx"
```

`--reloadcmd` 很关键 —— 续期后 nginx 不 reload 就还在用旧证书。

### 13. 加 443 段

把 `byteplus.conf` 整体替换成：

```nginx
server {
    listen 80;
    server_name kuromicloud.top;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    http2 on;
    server_name kuromicloud.top;

    ssl_certificate     /etc/nginx/ssl/billing.crt;
    ssl_certificate_key /etc/nginx/ssl/billing.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

> **`X-Real-IP` 那行不能省。** 应用的登录限流和访问日志靠它取真实客户端 IP；
> 缺了之后所有请求在应用看来都来自 `127.0.0.1`，限流会从「按 IP」退化成
> 「全局」—— 任何人连错 8 次会把所有人一起锁 5 分钟。详见 README 的「认证」一节。

### 14. 收口

确认 HTTPS 能用之后：把 `config.json` 的 `host` 改成 `127.0.0.1`（第 4 步已是），
然后在安全组里**删掉 8787** 那条规则。

### 15. 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://kuromicloud.top/login.html        # 200
curl -s -o /dev/null -w '%{http_code}\n' https://kuromicloud.top/api/accounts      # 401
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://kuromicloud.top/ # 301 -> https://
curl -s -m 8 -o /dev/null -w '%{http_code}\n' http://43.107.53.16:8787/            # 000 已收口
```

TLS 检查：

```bash
echo | openssl s_client -connect kuromicloud.top:443 -servername kuromicloud.top 2>/dev/null \
  | grep -E "Protocol|Cipher|Verify return code"
```

续期检查：

```bash
crontab -l | grep acme
~/.acme.sh/acme.sh --list
```

> `/` 和 `/login.html` 本身不需要认证（只是页面外壳，不含业务数据），所有数据都在
> `/api/accounts` 后面。所以直接 curl `/` 拿到 200 是正常的，不代表数据没保护。

---

## 日常维护

### 更新代码

本地 `git push` 之后，服务器上：

```bash
cd /opt/byteplus-billing && git pull && systemctl restart byteplus-billing
```

然后确认真的起来了（**别省这一步**）：

```bash
systemctl is-active byteplus-billing && curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/health
```

期望 `active` 和 `200`。有过一次「命令没报错、服务其实在崩溃循环」的经历
（见 [DEPLOY-NOTES](DEPLOY-NOTES.md) 的端口复用那条），多这一行能立刻发现。

回滚：`git log --oneline -5` 找到上一个提交，然后

```bash
git reset --hard <sha> && systemctl restart byteplus-billing
```

> **为什么不用 `git reset --hard origin/main` 做常规部署**：`git pull` 在服务器上
> 有人手动改过文件时会**报错停下**，而 `reset --hard` 会默默丢弃。前者更适合
> 人工执行 —— 它会告诉你「这里有你没料到的改动」。
>
> **别只 pull 不 restart**：HTML/JS 每次请求现读磁盘、立刻生效，Python 要重启
> 才生效，中间会是「新前端 + 旧后端」。

### 加/改 BytePlus 账号

改 `cred.xlsx` 重新上传即可，**不用重启**（每次请求都会重读）。
**权限修正必须跟上** —— scp 到 root 会把它重置成 `644 root:root`：

```bash
scp cred.xlsx root@43.107.53.16:/opt/byteplus-billing/ && \
ssh root@43.107.53.16 "chown byteplus:byteplus /opt/byteplus-billing/cred.xlsx && chmod 600 /opt/byteplus-billing/cred.xlsx"
```

### 改登录密码

改 `config.json` 后 `sudo systemctl restart byteplus-billing`。
`config.json` 不在仓库里，`git pull` 不会碰它。

**缓存疑似算错**（删掉重启会重新扫）：

```bash
sudo rm -f /opt/byteplus-billing/cache/history.json
sudo systemctl restart byteplus-billing
```

**看日志**（访问日志里是真实客户端 IP）：

```bash
sudo journalctl -u byteplus-billing -f
sudo journalctl -u byteplus-billing | grep 认证失败      # 撞库痕迹
```

---

## 踩过的坑与遗留项

已移到 **[DEPLOY-NOTES.md](DEPLOY-NOTES.md)**，包含：

- 待办事项（**有一项是密钥轮换，未完成**）
- 环境相关：`certbot` 在这个系统装不了、`dnf` 严格模式、git 的 `safe.directory`
- 运行时：Windows/Linux 的 `SO_REUSEADDR` 语义差异导致重启失败
- 权限：`cred.xlsx` 每次 scp 都会被重置成 644
- 遗留项：443 只监听 IPv4、证书进 CT 日志被扫、如何彻底关掉 80 端口
- 排查经验：几个「看着像 bug 其实不是」的现象
