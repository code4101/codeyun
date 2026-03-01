# CodeYun Ubuntu 24.04 部署指南 (简化版)

本指南基于 **GitHub Actions + SSH + uv + systemd** 的自动化部署方案。
利用服务器已有的 `uv` 和 `Nginx` 环境，实现代码推送后自动更新并重启服务。

## 1. 服务器准备 (首次部署)

### 1.1 基础环境
确保服务器已安装 `git`, `uv`, `nginx`。
由于你是 `deploy` 用户（假设），请确保有 sudo 权限用于 Nginx 配置。

```bash
# 登录服务器
ssh deploy@your-server-ip

# 安装 uv (如果未安装)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆代码到主目录 (确保目录名为 codeyun)
cd ~
git clone https://github.com/your-username/codeyun.git
cd codeyun

# 创建 .env 文件 (用于生产环境配置)
cp .env.example .env
nano .env
# 填入:
# CODEYUN_SECRET_KEY=your-secure-secret-key
# DATABASE_URL=sqlite:////home/deploy/codeyun/backend/data/codeyun.db
```

### 1.2 运行初始化脚本
项目包含一个 `deploy/setup_server.sh` 脚本，可自动配置 systemd 服务和 Nginx。

```bash
# 在服务器项目目录下运行
chmod +x deploy/setup_server.sh
./deploy/setup_server.sh
```

脚本会自动：
1. 使用 `uv sync` 安装依赖。
2. 配置并启动 `systemd --user` 服务 (`codeyun-backend`)。
3. (可选) 配置 Nginx 反向代理 (需要 sudo 密码)。

**验证服务状态：**
```bash
systemctl --user status codeyun-backend
curl http://localhost:8000/docs
```

## 2. GitHub Actions 配置

在 GitHub 仓库 -> Settings -> Secrets and variables -> Actions 中添加以下 Secrets：

| Secret 名称 | 说明 | 示例值 |
|---|---|---|
| `DEPLOY_SSH_HOST` | 服务器 IP | `1.2.3.4` |
| `DEPLOY_SSH_PORT` | SSH 端口 | `22` |
| `DEPLOY_SSH_USER` | SSH 用户名 | `deploy` |
| `DEPLOY_SSH_PRIVATE_KEY` | 私钥内容 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `DEPLOY_APP_DIR` | 项目路径 | `/home/deploy/codeyun` |

## 3. 自动更新流程

每次 push 到 `main` 分支时，GitHub Actions 会：
1. SSH 连接到服务器。
2. 进入项目目录。
3. 执行 `deploy/update.sh`：
   - `git pull` 拉取最新代码。
   - `uv sync` 更新依赖。
   - `systemctl --user restart codeyun-backend` 重启服务。

## 4. 手动管理命令

```bash
# 查看日志
journalctl --user -u codeyun-backend -f

# 重启服务
systemctl --user restart codeyun-backend

# 停止服务
systemctl --user stop codeyun-backend
```

## 5. Nginx HTTPS 配置 (如需手动调整)
如果脚本自动配置失败，可手动编辑 `/etc/nginx/sites-available/codeyun`，确保指向后端端口 8000：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```
前端静态文件通常由 Nginx 直接托管在 `/var/www/codeyun` 或类似路径，具体视你的前端构建流程而定。
本方案主要关注后端服务的自动化部署。
