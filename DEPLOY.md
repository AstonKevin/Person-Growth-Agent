# Personal-Growth-Agent 云服务器部署手册（Ubuntu / 境外免备案）

> 三阶段：① IP 部署 → ② 域名解析 → ③ Caddy 自动 HTTPS。

## 阶段①：用 IP 部署（先跑通）

### 1. 服务器装 Docker（SSH 登录后执行一次）

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
docker --version && docker compose version
```

### 2. 把项目代码传到服务器

在**本地 Windows PowerShell**（不是服务器）执行：

```powershell
scp -r "D:\My project\Person_Growth_Agent" root@<服务器IP>:/opt/pga
```

> 或用 git：先推到 GitHub 私有仓库，服务器上 `git clone <仓库> /opt/pga`。

### 3. 在服务器上创建生产环境变量

```bash
cd /opt/pga
cp .env.prod.example .env
openssl rand -hex 16     # 输出当作 DB_PASSWORD
openssl rand -hex 32     # 输出当作 SECRET_KEY
nano .env                # 填 DB_PASSWORD / LLM_API_KEY / SECRET_KEY；DOMAIN 先留空
```

### 4. 启动三个应用容器（先不起 caddy）

```bash
docker compose -f docker-compose.prod.yml up -d --build db api web
docker compose -f docker-compose.prod.yml logs -f api    # 看到 Uvicorn running 即成功
```

### 5. 放行并验证

- 服务器厂商防火墙/安全组放行端口：`22`（SSH）、临时开放 `8000`、`8501`（阶段③后关掉）。
- 浏览器访问：`http://<服务器IP>:8000/docs`、`http://<服务器IP>:8501`。

## 阶段②：买域名 + 解析

1. 买域名（境外服务器免备案）：Cloudflare Registrar / Porkbun / Namecheap 较便宜；
   也可在阿里云/腾讯云买，用 DNS 解析即可。
2. 加两条 **A 记录**指向服务器 IP：
   - `app`  → `<服务器IP>`
   - `api`  → `<服务器IP>`
3. 等待解析生效（几分钟～几十分钟）。本机验证：
   ```bash
   nslookup app.<你的域名>
   ```

## 阶段③：Caddy 自动 HTTPS

1. 在服务器 `.env` 填 `DOMAIN=<你的域名>`。
2. 厂商防火墙放行 `80`、`443`（Caddy 申请证书要用 80）。
3. 删掉 `docker-compose.prod.yml` 里 api/web 两段 `ports:`（不再直接暴露），只保留 caddy 的 80/443。
4. 启动（含 caddy）：
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
5. Caddy 会自动申请证书，稍后访问：
   - `https://app.<你的域名>`（Streamlit）
   - `https://api.<你的域名>/docs`（FastAPI）
6. 回到防火墙关闭临时的 `8000`、`8501` 入站。

## 常用运维命令

```bash
docker compose -f docker-compose.prod.yml ps          # 看状态
docker compose -f docker-compose.prod.yml logs -f web # 看日志
docker compose -f docker-compose.prod.yml down        # 停（保留数据）
docker compose -f docker-compose.prod.yml up -d --build api web  # 改代码后重建

# 初始化/提升首个管理员（D4 决策）：
docker compose -f docker-compose.prod.yml exec api python scripts/set_admin.py <用户名>
```

## 上线前检查

- [ ] MySQL 没有公网端口（db 服务无 ports 映射）
- [ ] DB 密码、SECRET_KEY 用随机值，不是 root123 / change-me
- [ ] CREDENTIAL_ENCRYPTION_KEY 已在 `.env` 配置强密钥（Fernet 格式）
- [ ] 执行过首个管理员初始化：`python scripts/set_admin.py <用户名>`
- [ ] 防火墙只放行 22、80、443（8000/8501 阶段③后关闭）
- [ ] `.env` 不提交 git，只在服务器上
