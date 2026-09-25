# 独立 Android 版（0.1.0 测试版）

独立手机端工程位于 `frontend/mobile-native/`，与 Windows 桌宠工程分开。公开提供 [ARM64 测试版 APK](https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/download/v0.15.27/Yus-AI-Mobile-0.1.0-arm64.apk)。手机上的角色、对话和非敏感模型设置由 WebView 的 IndexedDB 保存；聊天直接向用户配置的 HTTPS OpenAI 兼容接口请求，不需要电脑开机。模型 API Key 单独保存在 Android 应用私有目录，未接入硬件密钥库；请勿分发调试版 APK 或手机数据备份。桌面和手机暂不自动同步；卸载应用可能清空手机数据。

**已安装 USB 调试包的用户注意：**公开测试包使用独立的正式签名，Android 不允许它覆盖不同签名的调试包。不要为了安装测试包直接卸载调试包，否则现有角色、聊天、密钥可能被清除。请先保留当前安装，待提供完整的本机备份/迁移流程后再切换。没有安装过调试包的 ARM64 用户可以直接安装公开 APK。以后更新公开版也必须使用同一签名密钥。

手机端首批插件：消息显示、对话环境时间、小说式回复。二次审核、离线翻译、主动互动明确标为待适配；桌面插件开关与手机端互不影响。聊天页现可打开「指令与模板」抽屉，创建、编辑、启停、删除角色通用或当前对话指令；启用的指令会随下一次请求一起提交给模型。模板可填变量后作为仅下一条、当前对话或角色通用指令应用。角色、模型和消息显示选择也改为统一的卡片式底部抽屉。

## 开发与调试

电脑浏览器预览：

```powershell
cd frontend/mobile-native
npm run dev -- --host 127.0.0.1
```

预览地址是 `http://127.0.0.1:5174/`。浏览器预览使用浏览器的 `fetch`，会受模型服务的 CORS 限制；Android 包改由 Rust 原生网络请求实现流式输出。

构建 APK 前需按照 [Tauri 官方 Android 前置条件](https://v2.tauri.app/start/prerequisites/)安装 Android SDK、NDK、JDK 17 与 Rust Android target，并执行一次 `npm run android:init` 生成 Android 工程。环境变量 `ANDROID_HOME`、`NDK_HOME` 和 `JAVA_HOME` 可仅在构建终端设置，不需要写入系统全局环境。安装手机调试包前，用 `adb devices -l` 确认设备状态为 `device`。

```powershell
cd frontend/mobile-native
npm run build
npm run android:build -- --debug --target aarch64
```

ARM64 调试 APK 构建后位于 `frontend/mobile-native/src-tauri/gen/android/app/build/outputs/apk/arm64/debug/app-arm64-debug.apk`。它包含调试能力，不是公开发布包，也不上传到 GitHub。在本机通过 USB 调试安装：

```powershell
adb devices -l
adb install -r 'src-tauri\gen\android\app\build\outputs\apk\arm64\debug\app-arm64-debug.apk'
```

发布构建须使用独立签名密钥，通过 `YUS_ANDROID_KEYSTORE_PATH`、`YUS_ANDROID_KEYSTORE_PASSWORD`、`YUS_ANDROID_KEY_ALIAS`、`YUS_ANDROID_KEY_PASSWORD` 四个环境变量传入 `npm run android:build -- --target aarch64`。密钥与口令不得放进仓库或安装包，并应由发布者安全备份；遗失密钥会使后续版本无法覆盖已安装的公开版。

当前代码只允许 HTTPS 模型地址且禁用 HTTP 重定向，防止 API Key 被明文传输或随重定向转发。调试时应验证安装、启动、会话切换、最新消息滚动与输入法布局；不同 Android WebView 和输入法仍需分别测试。

输入法适配：Android Activity 使用 `adjustResize`，并关闭会阻碍部分设备窗口缩放的边到边模式。前端根据实际可见视口高度调整聊天界面；键盘打开时临时隐藏底部导航并滚到最新消息，收起后恢复。修改原生 Android 文件后，需要重新组装 APK，仅运行网页预览不会验证输入法行为。

## 从电脑迁移到手机

可选的本地迁移脚本位于 `frontend/mobile-native/scripts/migrate_desktop_to_android.py`，能够把用户指定的桌面数据库中的角色、普通会话消息、模型配置、指令和模板合并到连接的调试手机。加 `--instructions-only` 时只合并指令和模板，不覆盖手机聊天与模型密钥。角色卡原始字段随记录保留；手机目前只使用其编译后的文本设定。未接续的桌宠主动发言不显示在普通聊天记录里。附件、记忆库和桌宠专属插件尚未迁入手机功能。**仓库不包含任何用户的数据库、聊天记录、密钥或迁移备份。**

迁移会先把手机端已有的 IndexedDB 数据备份到用户指定的 `--backup-dir`，再按固定的桌面 ID 合并；重复运行不会复制出重复角色和对话。备份不含密钥，但包含聊天内容，且是本机明文 JSON，请妥善保管。密钥通过临时的本机 ADB 调试通道传入手机应用私有目录，脚本不会打印密钥。这个操作需要授权的 USB 调试设备和可调试 APK，不是面向普通用户的自动同步方案。模型连接是否成功仍需在手机上发出一条测试消息验证。

如果 Windows 禁止创建符号链接，Tauri CLI 可能在“Rust ARM64 库编译成功”后无法完成 `jniLibs` 链接。可以将已编译的 `src-tauri/target/aarch64-linux-android/debug/libyus_ai_mobile_lib.so` 复制到 `src-tauri/gen/android/app/src/main/jniLibs/arm64-v8a/`。为减小调试包，用已安装 NDK 的 `llvm-strip.exe --strip-debug` 处理**复制后的** `.so`（不要处理 `target/` 原件）。然后确认 `gen/android/app/build` 是本工程的构建产物，再在 `src-tauri/gen/android/` 运行 `gradlew.bat :app:clean :app:assembleArm64Debug -x :app:rustBuildArm64Debug --no-daemon`。务必只针对自己生成的库和工程目录操作。

正式签名包遇到相同限制时，改为复制 `src-tauri/target/aarch64-linux-android/release/libyus_ai_mobile_lib.so`，设置上述四个签名环境变量，然后在 `src-tauri/gen/android/` 执行 `gradlew.bat :app:assembleArm64Release -x :app:rustBuildArm64Release --no-daemon`。发布前必须用 Android SDK 的 `apksigner verify` 核实签名和 `aapt dump badging` 核实版本、包名及 ARM64 架构。
