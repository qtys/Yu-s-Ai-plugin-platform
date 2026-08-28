# 架构说明

项目采用单仓库结构：

- `frontend/`：React + TypeScript + Vite 响应式界面
- `backend/`：FastAPI API、SQLite 存储、模型服务适配
- `docs/`：产品规格与技术决策

当前聊天请求经过：用户消息 → 角色系统提示词 → 历史消息 → OpenAI 兼容 API → 流式响应 → 消息持久化。

后续插件钩子预留在输入处理、上下文组装、模型调用和输出处理四个阶段。MVP 不加载外部插件代码。
