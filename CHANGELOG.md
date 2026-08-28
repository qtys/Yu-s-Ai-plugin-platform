# 修改记录

本项目遵循语义化版本。日期采用 `YYYY-MM-DD`。

## [0.1.0] - 2026-08-28

### 新增

- React + TypeScript 响应式聊天界面。
- FastAPI + SQLite 本地后端。
- OpenAI Chat Completions 兼容模型接口与流式回复。
- 角色卡、系统提示词、多会话和消息历史持久化。
- 模型地址、API Key、模型名称、温度和最大输出配置。
- Tauri 2 Windows 桌面外壳。
- 窗口始终置顶、迷你聊天窗口和系统托盘。
- PyInstaller 后端 sidecar，桌面应用自动启动和关闭本地服务。
- Windows NSIS 安装包构建流程。
- 轮转文件日志、sidecar 启动日志和安全诊断 API。
- 数据库检查、WAL checkpoint、模型连接测试及日志标记指令。
- 后端健康检查等待，避免桌面界面早于本地服务启动。

### 修复

- 修复 PyInstaller 未包含 `app` 包导致安装版后端无法启动的问题。
- 修复旧 sidecar 占用 8000 端口时缺乏可诊断信息的问题。
- 修复 HTTPX 继承异常代理环境导致 DeepSeek TLS `ConnectError` 的问题。
- 修复安装目录日志缺失；现在优先写入安装位置的 `logs` 目录。
- 修复角色创建时后端尚未就绪导致的 `Failed to fetch`。

### 安全

- 日志不记录 API Key、Authorization Header、聊天正文和系统提示词。
- 诊断 API 仅支持白名单操作，不提供任意 Shell 或 PowerShell 执行。
- 数据库、日志、构建产物和本机配置均已加入 Git 忽略规则。
