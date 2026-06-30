# 🚀 Cloud AI Trading - 部署指南

## 快速开始 (本地)

### 首次部署

```bash
# 1. 进入项目目录
cd /path/to/CloudAiTrading

# 2. 复制环境文件
cp .env.example .env

# 3. 编辑 .env，填入你的 API 密钥
nano .env

# 4. 运行部署脚本
chmod +x deploy.sh
./deploy.sh
```

### 日常启动/重启

```bash
# 启动所有服务（自动运行迁移）
./deploy.sh

# 或者手动启动
docker compose up -d
docker compose exec backend alembic upgrade head
```

### 停止服务

```bash
docker compose down
```

---

## VPS 部署（生产环境）

### 前置要求

- Docker + Docker Compose
- Python 3.12
- PostgreSQL 16+
- Redis 7+
- 4GB RAM 最小推荐

### 部署步骤

#### 1️⃣ 准备 VPS

```bash
# 登录 VPS
ssh user@your-vps-ip

# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 安装 Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 验证安装
docker --version
docker-compose --version
```

#### 2️⃣ 部署应用

```bash
# 克隆仓库（或上传代码）
git clone <your-repo-url> CloudAiTrading
cd CloudAiTrading

# 复制环境文件
cp .env.example .env

# 编辑 .env（重要！更新生产环境的敏感信息）
nano .env

# 关键配置项：
# - SECRET_KEY: 生成新的随机密钥（至少32个字符）
# - ENCRYPTION_KEY: 生成新的加密密钥
# - ANTHROPIC_API_KEY: 生产环境的API密钥
# - DB_PASSWORD: 强随机密码
# - DEBUG: False（生产环境必须关闭）
# - ENVIRONMENT: production

# 运行部署脚本
chmod +x deploy.sh
./deploy.sh
```

#### 3️⃣ 验证部署

```bash
# 查看容器状态
docker compose ps

# 检查 API 健康状态
curl http://localhost:8000/api/health

# 查看日志
docker compose logs -f backend

# 检查数据库
docker compose exec backend alembic current
```

#### 4️⃣ 设置域名和 SSL（推荐）

```bash
# 使用 Nginx Reverse Proxy + Let's Encrypt

# 安装 Nginx
sudo apt install nginx -y

# 创建 Nginx 配置
sudo nano /etc/nginx/sites-available/cloudai

# 添加以下内容：
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# 启用站点
sudo ln -s /etc/nginx/sites-available/cloudai /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# 安装 SSL 证书 (Let's Encrypt)
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

---

## 环境变量配置

### 本地开发

```env
ENVIRONMENT=local
DEBUG=True
DB_PASSWORD=dev_password_123
SECRET_KEY=dev-secret-key-not-secure
ENCRYPTION_KEY=your-base64-encoded-key
```

### 生产环境

```env
ENVIRONMENT=production
DEBUG=False
DB_PASSWORD=<强随机密码>
SECRET_KEY=<生成随机密钥: python -c "import secrets; print(secrets.token_urlsafe(32))">
ENCRYPTION_KEY=<生成加密密钥: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

### 生成密钥命令

```bash
# 生成 SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 生成 ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 故障排查

### API 无法访问

```bash
# 1. 检查容器是否运行
docker compose ps

# 2. 查看后端日志
docker compose logs backend --tail 50

# 3. 检查端口是否开放
netstat -tlnp | grep 8000

# 4. 测试 API
curl -v http://localhost:8000/api/health
```

### 迁移失败

```bash
# 1. 查看迁移历史
docker compose exec backend alembic history

# 2. 查看当前版本
docker compose exec backend alembic current

# 3. 查看迁移日志
docker compose exec backend alembic upgrade head --sql

# 4. 手动回滚（如果需要）
docker compose exec backend alembic downgrade -1

# 5. 重新运行迁移
docker compose exec backend alembic upgrade head
```

### 数据库连接失败

```bash
# 1. 检查 PostgreSQL 是否运行
docker compose exec postgres pg_isready

# 2. 测试数据库连接
docker compose exec postgres psql -U postgres -d cloudaitrading -c "SELECT 1"

# 3. 检查数据库环境变量
docker compose exec backend env | grep DATABASE_URL

# 4. 查看 PostgreSQL 日志
docker compose logs postgres
```

---

## 备份和恢复

### 备份数据库

```bash
# 备份到本地文件
docker compose exec -T postgres pg_dump -U postgres cloudaitrading > backup_$(date +%Y%m%d_%H%M%S).sql

# 备份到云存储
docker compose exec -T postgres pg_dump -U postgres cloudaitrading | gzip > backup_$(date +%Y%m%d).sql.gz
# 上传到 S3/GCS
```

### 恢复数据库

```bash
# 从备份文件恢复
docker compose exec -T postgres psql -U postgres cloudaitrading < backup_20260413.sql

# 或从压缩文件
gunzip < backup_20260413.sql.gz | docker compose exec -T postgres psql -U postgres cloudaitrading
```

---

## 监控和日志

### 查看实时日志

```bash
# 所有服务
docker compose logs -f

# 特定服务
docker compose logs -f backend
docker compose logs -f celery-worker
docker compose logs -f celery-beat
```

### 收集性能指标

```bash
# 容器资源使用
docker stats

# 数据库表大小
docker compose exec postgres psql -U postgres -d cloudaitrading -c "
  SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
  FROM pg_tables 
  WHERE schemaname='public'
  ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"
```

---

## 自动化（CI/CD）

### GitHub Actions 示例

```yaml
# .github/workflows/deploy.yml
name: Deploy to VPS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Deploy to VPS
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd ~/CloudAiTrading
            git pull origin main
            ./deploy.sh
```

---

## 清单

### 本地部署前

- [ ] 复制 `.env.example` 到 `.env`
- [ ] 填入所有 API 密钥
- [ ] 运行 `./deploy.sh`
- [ ] 验证 `docker compose ps` 所有服务都是 `Up`
- [ ] 测试 `curl http://localhost:8000/api/health`
- [ ] 检查表是否创建 `docker compose exec backend alembic current`

### VPS 部署前

- [ ] 更新操作系统
- [ ] 安装 Docker 和 Docker Compose
- [ ] 配置防火墙（允许 80, 443, 8000 端口）
- [ ] 生成强随机 SECRET_KEY 和 ENCRYPTION_KEY
- [ ] 设置 DEBUG=False
- [ ] 设置 ENVIRONMENT=production
- [ ] 配置 SSL 证书
- [ ] 备份任何存在的数据库
- [ ] 运行 `./deploy.sh`
- [ ] 验证 API 可访问
- [ ] 设置监控告警

---

## 常用命令速查

```bash
# 启动所有服务
docker compose up -d

# 停止所有服务
docker compose down

# 查看日志
docker compose logs -f backend

# 运行迁移
docker compose exec backend alembic upgrade head

# 进入数据库
docker compose exec postgres psql -U postgres -d cloudaitrading

# 重启特定服务
docker compose restart backend

# 完全重建镜像
docker compose build --no-cache && docker compose up -d

# 清理所有数据（危险！）
docker compose down -v
```

---

## 支持和问题

如有问题，请检查：
1. 日志：`docker compose logs -f`
2. 环境变量：`cat .env`
3. 迁移状态：`docker compose exec backend alembic current`
4. 网络连接：`curl http://localhost:8000/api/health`
