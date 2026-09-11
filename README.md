# Yu's AI Plugin Platform

一个本地优先、支持个性化角色与多模型 API 的私人 AI 聊天工具。项目会先把日常聊天体验做好，再逐步开放翻译、记忆和上下文处理等插件能力。

## 当前能力

- OpenAI Chat Completions 兼容 API
- 流式聊天与停止生成
- 角色卡和系统提示词
- 多会话与 SQLite 本地持久化
- 模型地址、密钥及生成参数配置
- 桌面和手机宽度的响应式界面
- 蓝色雨滴桌宠、漫画回复气泡和桌宠快捷输入

产品范围见 [MVP 规格](docs/MVP.md)，当前完成度见 [项目进度](docs/PROGRESS.md)，版本变化见 [修改记录](CHANGELOG.md)，技术边界见 [架构说明](docs/ARCHITECTURE.md)，本地排错方式见 [调试接口](docs/DEBUG_API.md)。

## 直接安装桌面版

Windows 安装包生成在：

```text
frontend\src-tauri\target\release\bundle\nsis\Yus AI_0.3.1_x64-setup.exe
```

安装后直接启动即可，不需要打开终端。应用默认显示蓝色雨滴桌宠；单击桌宠可展开圆形功能入口，选择“对话”后显示聊天气泡，拖动桌宠可调整并保存位置。关闭主窗口会切换回桌宠，通过系统托盘可以显示主界面、显示或隐藏桌宠以及彻底退出。数据库保存在安装位置的 `data` 文件夹，运行日志优先保存在安装位置的 `logs` 文件夹；覆盖升级不会删除这两个运行时目录。

## 开发模式

需要 Node.js 20+ 和 Python 3.10+。

### 后端

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload
```

后端地址为 `http://localhost:8000`，接口文档位于 `http://localhost:8000/docs`。

### 网页界面

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。首次使用时先创建角色，再进入“模型设置”填写 API 地址、API Key 和模型名称。

### 桌面开发模式

先构建本地后端 sidecar：

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-build.txt
.\scripts\build-backend.ps1
cd frontend
npm run desktop:dev
```

生成 Windows 安装包：

```powershell
cd frontend
npm run desktop:build
```

## 验证

```powershell
cd frontend
npm run build

cd ..\backend
..\.venv\Scripts\python -m pytest -q
```

网页开发模式的数据写入 `backend/data/yus_ai.db`。桌面版数据写入软件安装目录的 `data/yus_ai.db`，两者都不会提交到 Git。首次运行新版桌面端时，如果安装目录还没有数据库，会自动从旧版的 Windows 用户应用数据目录复制现有数据，旧文件暂时保留作为备份。
