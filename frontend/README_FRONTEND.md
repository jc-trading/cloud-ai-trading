# CloudAiTrading 系统监控前端

一个 Vue 3 + Ant Design Vue 构建的实时系统监控仪表板。

## 功能特性

✅ **实时系统监控** - CPU、内存、磁盘使用率 (5秒刷新)
✅ **任务健康状态** - 后台进程状态监控 (online/offline/failed)
✅ **实时日志查看** - WebSocket 推送，支持按类别和优先级筛选
✅ **JWT 认证** - 安全的 API 访问
✅ **自适应设计** - 支持桌面和移动设备

## 技术栈

- **Vue 3** - 前端框架
- **Ant Design Vue 4** - UI 组件库
- **Vite** - 构建工具
- **Axios** - HTTP 客户端
- **Pinia** - 状态管理
- **ECharts** - 数据可视化（预留）
- **WebSocket** - 实时通信

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 配置环境

复制环境变量模板：

```bash
cp .env.example .env.local
```

编辑 `.env.local`（如果后端不是本地 8000 端口）：

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_URL=ws://localhost:8000/api/v1/system/ws/logs
```

### 3. 启动开发服务器

```bash
npm run dev
```

访问 http://localhost:3000

## 默认登录凭证

```
邮箱: test@example.com
密码: TestPassword123!
```

> 在登录页面默认填充，或从后端创建新用户

## 项目结构

```
frontend/
├── index.html              # 入口 HTML
├── package.json            # 项目依赖
├── vite.config.js          # Vite 配置
├── .env.example            # 环境变量模板
├── README_FRONTEND.md      # 本文件
└── src/
    ├── main.js             # 应用入口
    ├── App.vue             # 根组件
    ├── api/
    │   └── index.js        # API 接口定义
    ├── stores/
    │   └── auth.js         # Pinia 认证 store
    ├── utils/
    │   └── websocket.js    # WebSocket 管理器
    ├── views/
    │   ├── Login.vue       # 登录页面
    │   └── Dashboard.vue   # 仪表板页面
    └── components/
        ├── SystemMonitor.vue      # 系统监控组件
        ├── TaskStatusPanel.vue    # 任务状态组件（核心）
        └── LogViewer.vue          # 日志查看器组件
```

## API 对接

前端自动对接以下后端 API：

| 功能 | 端点 | 方法 |
|------|------|------|
| 登录 | `/api/v1/auth/login` | POST |
| 注册 | `/api/v1/auth/register` | POST |
| 实时指标 | `/api/v1/system/metrics` | GET |
| 系统日志 | `/api/v1/system/logs` | GET |
| 任务状态 | `/api/v1/system/tasks` | GET |
| 系统健康 | `/api/v1/system/health` | GET |
| 同步任务 | `/api/v1/system/tasks/sync` | POST |
| 清理日志 | `/api/v1/system/logs/cleanup` | POST |
| WebSocket | `/api/v1/system/ws/logs` | WS |

所有请求自动包含 JWT token，存储在 localStorage 中。

## 功能说明

### 1. 实时系统监控

- 显示 CPU、内存、磁盘使用百分比
- 5 秒自动刷新
- 不可用数据显示为 "-"
- 使用进度条直观展示，颜色代码：
  - 🟢 绿色: 0-70% (正常)
  - 🟡 橙色: 71-85% (警告)
  - 🔴 红色: 85%+ (危险)

### 2. 任务健康状态（核心）

监控后台任务是否正常运行：

- ✅ **online** - 任务在线并正常工作
- ⏸️ **offline** - 任务离线（未启动或已停止）
- 🔄 **running** - 任务正在执行
- 😴 **idle** - 任务空闲
- ❌ **failed** - 任务失败（显示错误信息）

关键监控的任务：
- `collect_market_data` - 市场数据收集
- `generate_trading_signals` - 交易信号生成
- `calculate_portfolio_stats` - 投资组合统计
- `update_indicators` - 指标更新
- `cleanup_market_data` - 数据清理

### 3. 实时日志查看器

- 实时接收日志通过 WebSocket
- 支持按类别筛选：market_data, trading, schedule, system
- 支持按优先级筛选：DEBUG, INFO, WARNING, ERROR, CRITICAL
- 显示相关标签（任务名、交易对等）
- 可展开查看元数据（JSON 格式）

### 4. 系统健康评分

综合评估系统状态：
- 🟢 **健康** - 所有指标正常
- 🟡 **警告** - 部分指标接近阈值
- 🔴 **异常** - 存在失败的任务或超高使用率

## 构建和部署

### 开发构建

```bash
npm run dev
```

### 生产构建

```bash
npm run build
```

输出文件在 `dist/` 目录。

### 与后端集成

构建后的文件可以与后端集成：

```bash
# 复制构建输出到后端静态目录
cp -r dist/* ../backend/app/static/

# 然后访问 http://localhost:8000/
```

### 部署到 VPS

1. **安装 Node.js**

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

2. **部署前端**

```bash
cd /opt/cloudaitrading/frontend
npm install
npm run build
```

3. **后端服务 Nginx 配置**

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端路由
    location / {
        root /opt/cloudaitrading/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 代理
    location /api {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket 代理
    location /api/v1/system/ws {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 常见问题

### Q: CORS 错误？

A: 确保后端在 `app/config.py` 中配置了 CORS：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Q: WebSocket 连接失败？

A: 检查：
1. 后端是否运行在 http://localhost:8000
2. `.env.local` 中的 WebSocket URL 是否正确
3. 防火墙是否阻止 WebSocket

### Q: 数据不刷新？

A: 
1. 检查浏览器控制台错误
2. 确保后端数据库有数据
3. 检查 Celery 任务是否在运行

## 开发指南

### 添加新 API

在 `src/api/index.js` 中添加：

```javascript
export const myNewApi = () =>
  api.get('/my/endpoint')
```

### 修改 UI 主题

使用 Ant Design Vue 的主题配置，在 `src/main.js` 中：

```javascript
import { theme } from 'ant-design-vue'
app.config.globalProperties.$theme = {
  token: {
    colorPrimary: '#1890ff',
  },
}
```

### 添加新组件

在 `src/components/` 创建 Vue SFC 文件，在 Dashboard 中导入使用。

## 性能优化建议

1. **图片优化** - 使用 WebP 格式
2. **代码分割** - 使用 Vue Router 动态导入
3. **缓存策略** - 配置 Vite 缓存
4. **CDN 部署** - 将静态文件上传到 CDN

## 许可证

MIT

## 联系方式

有问题？提交 Issue 或 PR！

---

**最后更新**: 2026-04-14
**版本**: 1.0.0
