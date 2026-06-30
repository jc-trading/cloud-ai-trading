# 📚 Cloud AI Trading - 文档中心

所有项目文档都组织在这个目录中。根据您需要的内容，选择相应的文件夹。

---

## 🗂️ 文档分类

### 📊 [project/](project/) - 项目规划和架构

项目的战略规划、设计和进度跟踪文档。

| 文件 | 说明 |
|------|------|
| [System-Architecture.md](project/System-Architecture.md) | 完整的系统架构设计、组件关系、数据流 |
| [Functional-Spec.md](project/Functional-Spec.md) | 功能规格书、API 定义、数据模型 |
| [Project-Progress.md](project/Project-Progress.md) | 开发进度、完成的阶段、下一步计划 |
| [Implementation-Notes.md](project/Implementation-Notes.md) | 实现细节、技术决策、最佳实践 |

**适合：** 需要了解系统整体设计、功能规格、项目进度的开发者和项目管理人员。

---

### 🚀 [setup/](setup/) - 部署和配置指南

安装、配置和部署相关的所有文档。

| 文件 | 说明 |
|------|------|
| [Quick-Start.md](setup/Quick-Start.md) | 30 秒快速启动现有系统 |
| [Installation.md](setup/Installation.md) | 完整的安装和初始化步骤 |
| [Deployment.md](setup/Deployment.md) | 部署到生产环境的详细指南 |
| [Backend-Quick-Start.md](setup/Backend-Quick-Start.md) | 后端开发环境快速启动 |

**适合：** 需要部署、配置或启动系统的人员。

**快速导航：**
- 首次部署？ → [Installation.md](setup/Installation.md)
- 已部署想启动？ → [Quick-Start.md](setup/Quick-Start.md)
- 部署到生产？ → [Deployment.md](setup/Deployment.md)
- 后端开发？ → [Backend-Quick-Start.md](setup/Backend-Quick-Start.md)

---

### 🏗️ [implementation/](implementation/) - 实现阶段文档

每个开发阶段（Phase）的完整实现文档、规格说明和测试计划。

**当前实现**
- **Phase 4 - P0: Claude AI Celery Integration** ✅ COMPLETE
  - [P0-PHASE_4_CLAUDE_AI_INTEGRATION.md](implementation/P0-PHASE_4_CLAUDE_AI_INTEGRATION.md) - 技术规格、API 成本、成功标准
  - [P0-IMPLEMENTATION_SUMMARY.md](implementation/P0-IMPLEMENTATION_SUMMARY.md) - 快速参考、改动摘要
  - [P0-TESTING_CHECKLIST.md](implementation/P0-TESTING_CHECKLIST.md) - 7 个测试阶段、SQL 查询、故障排除

**历史实现**
- [Frontend-Architecture.md](implementation/Frontend-Architecture.md) - Vue.js 前端架构、组件设计
- `backend/` - 后端 Phase 1-3 的实现文档

**适合：** 需要了解具体如何实现每个 Phase 的开发者。

---

### 📋 [operations/](operations/) - 运营和维护文档

系统监控、故障排除、运维日志和 Session 总结。

| 文件 | 说明 |
|------|------|
| [Session-Summary.md](operations/Session-Summary.md) | 最新工作 Session 总结、完成的工作、发现的问题 |
| [Next-Session-Quickstart.md](operations/Next-Session-Quickstart.md) | 下一个 Session 的快速启动卡 |
| [Ready-to-Deploy.md](operations/Ready-to-Deploy.md) | 部署前的检查清单和确认清单 |
| [Monitoring-Report.md](operations/Monitoring-Report.md) | 系统监控、故障排除、常见问题解决 |

**适合：** DevOps、系统管理员、日常运维人员。

**快速导航：**
- 遇到问题？ → [Monitoring-Report.md](operations/Monitoring-Report.md)
- 上次的工作？ → [Session-Summary.md](operations/Session-Summary.md)
- 准备下个 Session？ → [Next-Session-Quickstart.md](operations/Next-Session-Quickstart.md)

---

### 🔍 [code-review/](code-review/) - 代码审查和质量报告

每个阶段的代码审查结果、问题追踪和修复历史。

| 文件 | 说明 |
|------|------|
| [README.md](code-review/README.md) | 代码审查索引和标准 |
| [P0-CLAUDE_REVIEW_FIX_SUMMARY.md](code-review/P0-CLAUDE_REVIEW_FIX_SUMMARY.md) | P0 审查、发现问题、修复和验证 |
| [CODEX_AUDIT_REPORT.md](code-review/CODEX_AUDIT_REPORT.md) | Phase 4 初始审计报告 |
| [ISSUES_FOUND_AND_FIXES.md](code-review/ISSUES_FOUND_AND_FIXES.md) | 历史问题和修复记录 |

**适合：** 需要了解代码质量、已知问题、修复历史的人员。

---

## 🎯 快速查找

### 按用户角色

**👨‍💼 项目经理/Product**
- 开始：[System-Architecture.md](project/System-Architecture.md) (理解系统)
- 继续：[Project-Progress.md](project/Project-Progress.md) (项目进度)
- 参考：[Session-Summary.md](operations/Session-Summary.md) (最新工作)

**👨‍💻 后端开发者**
- 开始：[Backend-Quick-Start.md](setup/Backend-Quick-Start.md)
- 深入：[System-Architecture.md](project/System-Architecture.md)
- 实现：[implementation/backend/](implementation/backend/)
- 问题排查：[Monitoring-Report.md](operations/Monitoring-Report.md)

**🎨 前端开发者**
- 开始：[Frontend-Architecture.md](implementation/Frontend-Architecture.md)
- 快速启动：[Quick-Start.md](setup/Quick-Start.md)
- API 定义：[Functional-Spec.md](project/Functional-Spec.md)

**🚀 DevOps/运维**
- 部署：[Deployment.md](setup/Deployment.md)
- 监控：[Monitoring-Report.md](operations/Monitoring-Report.md)
- 故障排除：[Ready-to-Deploy.md](operations/Ready-to-Deploy.md)
- 快速启动：[Quick-Start.md](setup/Quick-Start.md)

**🔍 代码审查/QA**
- 审计报告：[Code-Audit-Report.md](audit/Code-Audit-Report.md)
- 问题修复：[Issues-and-Fixes.md](audit/Issues-and-Fixes.md)
- 架构设计：[System-Architecture.md](project/System-Architecture.md)

### 按任务类型

| 任务 | 查看 |
|------|------|
| 首次部署系统 | [Installation.md](setup/Installation.md) |
| 启动已部署的系统 | [Quick-Start.md](setup/Quick-Start.md) |
| 了解系统架构 | [System-Architecture.md](project/System-Architecture.md) |
| 查看 API 文档 | [Functional-Spec.md](project/Functional-Spec.md) |
| 查看开发进度 | [Project-Progress.md](project/Project-Progress.md) |
| 遇到错误问题 | [Monitoring-Report.md](operations/Monitoring-Report.md) |
| 查看最新工作 | [Session-Summary.md](operations/Session-Summary.md) |
| 开始新 Session | [Next-Session-Quickstart.md](operations/Next-Session-Quickstart.md) |
| 检查代码质量 | [Code-Audit-Report.md](audit/Code-Audit-Report.md) |

---

## 📖 阅读建议顺序

**新项目成员：**
1. 根项目的 [README.md](../README.md)
2. [System-Architecture.md](project/System-Architecture.md)
3. [Quick-Start.md](setup/Quick-Start.md)
4. 相关领域的文档（后端/前端/运维）

**继续开发者：**
1. [Session-Summary.md](operations/Session-Summary.md) (了解最新进度)
2. [Next-Session-Quickstart.md](operations/Next-Session-Quickstart.md) (快速启动)
3. 相关领域的特定文档

**系统故障排除：**
1. [Quick-Start.md](setup/Quick-Start.md) (检查系统状态)
2. [Monitoring-Report.md](operations/Monitoring-Report.md) (故障排除)
3. [Code-Audit-Report.md](audit/Code-Audit-Report.md) (已知问题)

---

## 📊 文档统计

- **项目规划:** 4 个文档
- **部署指南:** 4 个文档
- **实现文档:** 1 个前端 + 4 个后端文档
- **运营文档:** 4 个文档
- **审计报告:** 3 个文档

**总计:** 20 个精选、有组织的文档

---

## 💡 文档维护

- **最后更新:** 2026-04-14
- **维护者:** Cloud AI Trading Team
- **更新频率:** 每次 Session 后更新 Session-Summary 和 Project-Progress

### 如何保持文档最新

每个开发周期后：
1. 更新 `Project-Progress.md` (项目进度)
2. 更新 `Session-Summary.md` (工作总结)
3. 如有重大变化，更新相关的实现文档
4. 如有问题修复，更新 `Issues-and-Fixes.md`

---

## 🔗 相关链接

- **根目录 README:** [../README.md](../README.md)
- **源代码:** [../backend/](../backend/) 和 [../frontend/](../frontend/)
- **Docker 配置:** [../docker/](../docker/)
- **运维脚本:** [../scripts/](../scripts/)

---

**需要帮助？** 查看上面的快速查找表或按照您的角色选择相应文档。
