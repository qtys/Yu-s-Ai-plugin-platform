# 开发测试与界面热更新

## 开发测试（不打安装包）

运行 `scripts/开发测试.cmd`，打开 http://127.0.0.1:5181/ 。前端自动刷新，Python 后端在修改后自动重启。

桌面窗口测试运行 `scripts/开发测试.cmd -Desktop`，首次及 Rust 修改后需要编译，但不生成安装包，也不依赖打包后的 Python 后端文件。使用独立应用标识 `com.qtys.yus-ai.dev`；正式版可同时保留。测试数据位于工程 `build/dev-workspace/data`，日志位于 `build/dev-workspace/logs`。开发版不设置开机自启。关闭启动终端时清理该脚本启动的后端进程树，不停止正式版。

角色、模型和聊天初始为空。需要真实测试数据时，手动从正式版创建备份、在测试版恢复；备份包含 API Key，不能上传。测试数据库不会自动读取或覆盖正式版数据库。

## GitHub 界面小包更新

这是界面资源更新，不是任意代码加载器。支持 HTML/CSS/JS、图标、图片及字体；Python 后端、依赖、数据库迁移、原生窗口接口变动仍需完整安装包。只有安装了含此加载器的正式版本后才可使用，之前的安装版需要先更新一次安装包。

原生加载器只接受 Ed25519 签名正确、`host_version` 精确匹配当前原生版本的小包。下载来自本项目 GitHub Release，不读取任意仓库 URL。解压前核对清单与 ZIP SHA-256，限制总大小 40 MB，拒绝路径穿越、重复路径、软链接及可执行文件；加载前重新核对所有文件。外部界面运行在原有本地 Tauri 协议，不开放远程页面的原生权限。

在设置中点击「检查界面更新」，确认后下载并重启，重启前需保存未发送的输入。启动成功由主页面与后端健康检查确认；未确认成功的更新在下次启动自动回退。也可手动「回退界面」；界面无法打开时可从托盘「回退界面并重启」。旧包保留作回退，不覆盖数据库。

## 发布者构建

签名私钥保存在仓库之外，例如 `E:\Geek\Yus-AI-signing\ui-private.pem`。必须备份并妥善保护，不放进安装包、提交、日志或 GitHub Secret 以外的公开位置；公钥可公开。更换公钥需要完整安装包。

1. 安装发布工具：`.venv\Scripts\python.exe -m pip install -r scripts/requirements-release.txt`。
2. 前端在没有 `VITE_API_URL` 环境变量时运行 `npm run build`。
3. 在工程根运行：`.venv\Scripts\python.exe scripts/build-ui-update.py build --key E:\Geek\Yus-AI-signing\ui-private.pem --revision 1 --tag v0.16.11`。revision 为全局单调递增序号，不是应用版号，后续发布不可重复或降低。
4. 将生成目录内 `ui-update.json` 与 `ui-update.zip` 上传到相应 GitHub Release。Release 必须同时保留 Windows 安装包，否则现有完整安装包更新检查可能无法找到安装程序。应先上传 ZIP，再上传清单。

下载清单固定为本项目 `releases/latest/download/ui-update.json`。若没有界面包，显示暂无界面更新；旧版与不兼容版本继续使用完整安装包。不要单独创建一个不带安装包的“最新”热更新 Release。

目前不支持后台自动下载安装、后端热替换、跨原生版本界面包或第三方签名者。
