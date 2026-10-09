# Windows 快速上手

目标：完成一次可核对的只读任务。当前为早期测试候选；真实 provider 兼容性见 [兼容表](compatibility.md)。外部 API 调用可能产生费用。

## 1. 检查环境

在项目根目录的 PowerShell 运行：

```powershell
python --version
Get-Command codex -All | Select-Object CommandType, Source
codex --version
```

需要 Python 3.11+ 和真正的 `codex.exe`。如果 PATH 中只有 `.cmd`/`.bat` 启动器，请找到实际 exe，并在 MCP 注册项的环境变量中设置 `CODEX_CLI_PATH`。核心服务只使用 Python 标准库。暂不启用网页搜索时无需配置 Node 或浏览器。

## 2. 创建本机配置

```powershell
Copy-Item tools/laowu_mcp/user_config.example.json tools/laowu_mcp/user_config.json
```

仅首次复制；已存在本机配置时保留原文件。样例 `paths.workspace` 的 `../..` 指向本仓库根目录；安装目录与目标项目分开时改为目标项目绝对路径。使用完整样例文件，保留其他路径及 provider 映射。

第一次只使用路线 A 的第一个分组：把路线 B 的 `enabled` 改为 `false`，保留两条路线的 `native_codex_fallback=false`。这样首次任务不会因多路线而要求选择，也不会触发原生回退。

## 3. 注册 provider 和 MCP

在用户级 `~/.codex/config.toml` 合并以下配置；如果已有同名段落，编辑原段落，不要重复添加。将占位路径和地址换成自己的实际值：

```toml
[model_providers.provider_a_group_1]
name = "External provider A"
base_url = "https://api.example.com/v1"
env_key = "PROVIDER_A_GROUP_1_API_KEY"
wire_api = "responses"
requires_openai_auth = false

[mcp_servers.wuhezhizhong]
command = "python"
args = ["C:/path/to/wuhezhizhong/tools/laowu_mcp/server.py"]

# 仅在 PATH 无法找到真正 codex.exe 时添加，并替换路径：
# [mcp_servers.wuhezhizhong.env]
# CODEX_CLI_PATH = "C:/path/to/codex.exe"
```

这只是对应样例首个 provider 的配置。服务地址需要兼容此执行链的 Responses 协议和工具调用；只有 Chat Completions 或能返回 `/models` 不足以证明可用。密钥不要写入 TOML 或 JSON。

## 4. 保存密钥与模型

重启 Codex，确认 MCP 服务连接，然后打开一次“乌合之众”活动面板。在一些宿主中，每次调用面板入口都会新开一个标签；不要重复调用入口来刷新。后续查看活动快照或任务进度时，使用 `laowu_activity_snapshot` 或 `laowu_task_result`。在路线 A 第一个分组填写实际 API 地址、模型 ID 和密钥并保存；确认路线 A 启用且分组参与 Auto。密钥写入本机加密存储。

在“子代理配置”页确认 Codex 全局发起权限和勘察员权限已允许。找不到面板时先确认当前宿主支持 MCP Apps；工具连接成功不代表该宿主一定支持面板。

## 5. 执行第一次只读任务

向 Codex 输入以下请求，把路径替换为自己的目标项目：

> 使用乌合之众，先读取可调用 profiles，再用勘察员只读检查 C:/path/to/my-project。列出顶层目录与三个主要入口文件，不修改任何文件。使用 Auto；收到活动 ID 后查询同一任务，读完全部结果后确认交付并汇总。

成功标准：面板出现活动；实际使用的 provider/模型符合配置；任务完成；主控收到全文；目标工作区没有被修改。任务失败时查看脱敏错误，不因等待超时重复派发。

## 常见问题

| 表现 | 检查 |
|---|---|
| MCP 未连接 | Python 与 server.py 绝对路径、JSON 是否有效、Codex 重启后是否加载新配置 |
| `codex` 找不到或启动器被拒绝 | `CODEX_CLI_PATH` 指向真实 exe，并放在 MCP 的 env 段落 |
| provider/key 映射缺失 | 样例 provider ID、用户 TOML 中 model_provider ID、env_key 与密钥槽是否对应 |
| 模型能查询但任务失败 | 服务是否支持 Responses、流式事件及模型工具调用；记录实际错误 |
| 工作目录被拒绝 | cwd 是否位于 paths.workspace 解析后的真实路径内 |
| 多路线选择提示 | 选择启用路线，或首轮只启用路线 A；不要自动换到其他服务 |
| 有工具但没有面板 | 查看宿主是否支持 MCP Apps，以及连接与资源加载错误 |

更多配置、重置和权限说明见 [README](../README.md)。反馈问题时使用合成示例，勿上传完整本机配置或凭据。
