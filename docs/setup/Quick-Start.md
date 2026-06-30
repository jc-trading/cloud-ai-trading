# ⚡ 快速开始指南

## 本地开发（每次启动）

```bash
cd CloudAiTrading

# 一键启动（包括迁移、表创建、验证）
./deploy.sh
```

**就这样！** ✨

---

## 首次设置

```bash
# 1. 复制配置
cp .env.example .env

# 2. 编辑 .env（添加你的 API 密钥）
nano .env

# 3. 启动
./deploy.sh
```

---

## 访问应用

| 服务 | 地址 | 说明 |
|------|------|------|
| API | http://localhost:8000 | FastAPI 服务器 |
| Docs | http://localhost:8000/api/docs | Swagger 文档 |
| Health | http://localhost:8000/api/health | 健康检查 |

---

## 常用操作

```bash
# 查看日志
docker compose logs -f backend

# 停止所有容器
docker compose down

# 重启单个服务
docker compose restart backend

# 进入数据库
docker compose exec postgres psql -U postgres -d cloudaitrading
```

---

## VPS 部署

详见 [DEPLOYMENT.md](DEPLOYMENT.md)

简单版本：
```bash
./deploy.sh
```

---

## 故障排查

### API 无法访问？
```bash
docker compose logs backend | tail -50
```

### 数据库有问题？
```bash
docker compose exec backend alembic current
```

### 表不存在？
```bash
docker compose logs postgres
```

详细指南见 [DEPLOYMENT.md](DEPLOYMENT.md) 的"故障排查"章节。

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `deploy.sh` | 自动化部署脚本（本地/VPS 通用） |
| `.env.example` | 环境变量模板 |
| `.env` | 实际配置（永远不要提交到 Git） |
| `DEPLOYMENT.md` | 详细部署和运维指南 |
| `docker-compose.yml` | Docker 服务配置 |
| `alembic/` | 数据库迁移脚本 |

