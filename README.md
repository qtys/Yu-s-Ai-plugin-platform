# Yu's AI Plugin Platform

Yu's AI 是一款本地优先的 Windows 私人 AI 聊天工具。它将角色扮演、多模型 API、长期记忆、离线翻译和桌面宠物整合在同一个应用中，并为后续插件能力预留扩展空间。

当前版本：**0.8.1**

## 主要功能

### 私人 AI 对话

- 支持 OpenAI Chat Completions 兼容接口，可连接 DeepSeek 等兼容服务
- 支持流式回复、停止生成、多会话和本地历史记录
- 可配置 API 地址、API Key、模型名称、温度和最大输出长度
- 最近对话支持重命名和删除
- 用户消息与 AI 回复均可修改，后续对话会使用修改后的内容作为上下文
- 提供暮紫、午夜蓝、暖砂和纸墨四套界面主题

### 角色卡与记忆

- 角色卡支持头像、开场白、身份背景、性格、说话方式、用户关系、行为边界和示例对话
- 结构化角色信息会自动生成系统提示词，也可继续填写自定义提示词
- 支持创建、编辑、删除，以及 JSON 格式的角色导入和导出
- 可手动设置最近上下文消息数（2–200）和相关长期记忆条数（0–50）
- 长对话会保留较早内容摘要，并按角色隔离长期记忆
- 可从用户明确要求记住的信息中提取姓名、偏好和个人资料，并按当前话题选择相关记忆

### 蓝色雨滴桌宠

- 应用启动后默认显示透明、无边框且始终置顶的蓝色史莱姆桌宠
- 按住桌宠并移动鼠标即可拖动，位置会在下次启动时恢复
- 单击桌宠展开功能圈；点击“对话”打开漫画式聊天气泡
- 对话、翻译等面板可以独立关闭，隐藏区域不会继续拦截桌面点击
- 桌宠大小、透明度和气泡文字大小均可调整并保存
- 气泡会根据屏幕边界调整布局，同时保持靠近桌宠且不与桌宠重叠
- 桌宠与主窗口共用角色、会话和消息记录
- 可从模型设置或系统托盘开启、关闭 Windows 开机自启
- 程序采用单实例运行，重复启动只会唤醒已有窗口，不会重复启动本地服务

### 离线翻译

- 基于 Argos Translate 提供中文与英文双向翻译
- 中英、英中语言包可按需单独下载
- 语言包安装完成后无需联网，模型保存在安装目录中
- Windows 桌面版支持连续翻译：开启后，在其他应用中用鼠标选中文本即可自动识别方向并显示译文

## 安装与使用

项目当前提供 Windows NSIS 安装包。自行构建后，安装包位于：

```text
frontend\src-tauri\target\release\bundle\nsis\Yus AI_0.8.1_x64-setup.exe
```

安装后无需打开终端，直接启动 **Yus AI** 即可：

1. 从系统托盘打开主界面。
2. 创建或导入一个角色。
3. 在“模型设置”中填写兼容 API 地址、API Key 和模型名称。
4. 返回聊天页面创建对话并发送消息。

关闭主窗口不会退出程序，而是切换回桌宠模式。需要彻底结束程序时，请在系统托盘右键菜单中选择“退出”。托盘菜单也可以显示或隐藏桌宠，并调整桌宠大小和透明度。

## 本地数据与隐私

桌面版的运行数据保存在软件安装目录中，覆盖安装或升级时不会删除：

```text
<安装目录>\data\yus_ai.db
<安装目录>\data\translation-models\
<安装目录>\data\character-exports\
<安装目录>\logs\
```

- `yus_ai.db`：角色、会话、消息、设置、桌宠位置和记忆
- `translation-models`：离线翻译语言包
- `character-exports`：导出的角色卡
- `logs`：后端运行日志和诊断信息

首次运行新版时，如果安装目录内还没有数据库，程序会尝试从旧版 Windows 用户应用数据目录复制已有数据，并保留旧文件作为备份。

日志不会记录 API Key、Authorization Header、聊天正文或系统提示词。需要排查问题时，可参考 [调试接口](docs/DEBUG_API.md)。

> 注意：API Key 当前保存在本地 SQLite 数据库中，尚未接入 Windows Credential Manager。请勿将 `data`、`logs` 或本机配置文件上传到公开仓库。

## 本地开发

### 环境要求

- Windows 10/11
- Node.js 20+
- Python 3.10+
- Rust 与 Tauri 2 所需的 Windows 开发环境

### 启动后端

在项目根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload
```

后端地址为 `http://localhost:8000`，交互式接口文档位于 `http://localhost:8000/docs`。

### 启动网页开发界面

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。网页开发模式主要用于界面调试，不包含完整的桌宠、系统托盘和原生文件能力。

### 启动桌面开发模式

先构建本地后端 sidecar：

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-build.txt
.\scripts\build-backend.ps1
cd frontend
npm run desktop:dev
```

### 生成 Windows 安装包

```powershell
cd frontend
npm run desktop:build
```

## 测试

前端构建：

```powershell
cd frontend
npm run build
```

后端测试：

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q
```

网页开发模式的数据写入 `backend/data/yus_ai.db`；桌面版数据写入软件安装目录，两者均已加入 Git 忽略规则。

## 当前限制

- 当前只提供 Windows 桌面安装包
- 本地后端固定监听 `127.0.0.1:8000`
- API Key 暂时以本地数据库形式保存
- 目前内置插件能力以离线翻译为主，通用插件系统仍在逐步建设

## 项目文档

- [MVP 规格](docs/MVP.md)
- [项目进度](docs/PROGRESS.md)
- [修改记录](CHANGELOG.md)
- [架构说明](docs/ARCHITECTURE.md)
- [调试接口](docs/DEBUG_API.md)
