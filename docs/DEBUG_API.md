# 本地调试与诊断接口

桌面程序在 `127.0.0.1:8000` 暴露诊断 API，Swagger 文档位于 `http://127.0.0.1:8000/docs`。接口只监听本机，不提供任意 Shell 或 PowerShell 执行能力。

## 状态与日志

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/status
Invoke-RestMethod 'http://127.0.0.1:8000/api/diagnostics/logs?lines=200'
```

日志按 2 MB 轮转，最多保留 5 个历史文件。桌面版优先写入软件安装位置的 `logs/yus-ai.log`，sidecar 启动和崩溃信息写入同目录的 `sidecar.log`。若安装目录不可写，会回退到用户应用数据目录。实际路径由 `/api/diagnostics/status` 的 `log_file` 字段返回。

## 诊断指令

指令入口为 `POST /api/diagnostics/commands`，只接受以下白名单：

- `database_check`：执行 SQLite 完整性检查。
- `database_checkpoint`：执行安全的 WAL checkpoint。
- `model_connection_test`：调用当前模型服务的 `/models` 检查连接。
- `log_marker`：在日志中写入一条人工诊断标记。

示例：

```powershell
$Body = @{ command = 'database_check' } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/diagnostics/commands `
  -ContentType application/json `
  -Body $Body
```

日志不会主动记录 API Key、Authorization Header、聊天正文或系统提示词。模型调用仅记录状态码、耗时、错误类型和输出字符数。
