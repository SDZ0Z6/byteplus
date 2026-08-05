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

## 两个踩过的坑

**1. `certbot` 在这个系统上装不了。** Alibaba Cloud Linux 4 只启用 `alinux4-os/plus/updates`
三个仓库，里面没有 certbot，也没有 `epel-release` 可装。硬要用就得塞进 venv
（系统 Python 是 dnf 管的，直接 pip 装会冲突）。**改用 acme.sh** —— 纯 shell、零依赖、
自带续期 cron、不碰系统 Python。

**2. dnf 是严格模式。** 一条 `dnf install -y nginx certbot python3-certbot-nginx`
里只要有一个包没匹配上，**整个事务回滚**，nginx 也不会装上。所以要分开装。

---

## 重建步骤

### 1. 装依赖

```bash
sudo dnf install -y python3 python3-pip nginx
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

### 3. 上传代码

在**本地**项目目录执行。**逐项列出，不要用 `scp -r .`** —— 那会把 SSH 私钥
（`byteplus-kuromi-portal.pem`）一起传上去。

```bash
scp -r app deploy README.md root@43.107.53.16:/opt/byteplus-billing/
scp cred.xlsx config.example.json root@43.107.53.16:/opt/byteplus-billing/
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

**加/改 BytePlus 账号** —— 改 `cred.xlsx` 重新上传即可，**不用重启**（每次请求都会重读）：

```bash
scp cred.xlsx root@43.107.53.16:/opt/byteplus-billing/
ssh root@43.107.53.16 "chown byteplus:byteplus /opt/byteplus-billing/cred.xlsx && chmod 600 /opt/byteplus-billing/cred.xlsx"
```

**改登录密码**：改 `config.json` 后 `sudo systemctl restart byteplus-billing`。

**更新代码**：

```bash
scp -r app root@43.107.53.16:/opt/byteplus-billing/
ssh root@43.107.53.16 "chown -R byteplus:byteplus /opt/byteplus-billing/app && systemctl restart byteplus-billing"
```

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

## 已知遗留项

**1. 443 只监听 IPv4。** 现状 `0.0.0.0:443`，而 80 还额外监听了 `[::]:80`。
域名目前没有 AAAA 记录，所以没影响。但**如果以后加了 AAAA 记录**，IPv6 客户端
会走 80 拿到 301、再连 443 时失败。要支持就补上：

```nginx
listen [::]:443 ssl;
```

**2. 证书在公开 CT 日志里。** Let's Encrypt 签发的证书会进 Certificate Transparency
公开日志，扫描器盯着它 —— 域名上线后几分钟内就会有陌生 IP 来摸（已观测到）。
认证能挡住，属正常现象，不必紧张；但这也意味着**登录密码强度和失败锁定是真正的防线**。

**3. 想彻底关掉 80**：改用 DNS-01 校验（阿里云 DNS 有 API）：

```bash
export Ali_Key="你的AccessKeyId"
export Ali_Secret="你的AccessKeySecret"
~/.acme.sh/acme.sh --issue --dns dns_ali -d kuromicloud.top --server letsencrypt
```

这样续期不需要 80 端口，安全组可以只留 443（甚至只对自己的 IP 开放）。
代价是要给 acme.sh 一对阿里云 AK/SK。
