# 乌合之众 (wu he zhi zhong) MCP

面向 **Windows Codex 用户的早期测试候选**：在 Codex 面板中管理外部模型路线、子任务权限、执行活动和完整结果。当前没有稳定公开版本；真实宿主/provider 兼容性需按具体环境验收。

[首次上手](docs/quickstart.md) · [兼容性与限制](docs/compatibility.md) · [贡献指南](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) · [验收状态](docs/acceptance.md)

乌合之众是一个面向 Codex 的本地 MCP 服务，用于把可独立执行的任务交给用户配置的外部模型。Codex 主控负责拆解和验收；外部模型可以检查代码、生成补丁或在授权的工作区内修改文件。

它的目标是让用户不必为了委派任务再安装并登录另一个厂商的编码 CLI。外部模型仍使用用户自己的 API 站点和密钥，可能产生独立费用或用量限制；本项目不提供免费额度，也不保证降低总费用。

## 连接关系与术语

```text
Codex 主控 / MCP App 面板
  → 本地 stdio MCP 服务
    → 本地 Codex CLI 子进程
      → 用户配置的外部 provider
      → 路线明确允许时的本机 Codex 回退
```

“角色”描述子任务的工作边界；“provider”是 Codex 的 API 服务定义；“分组”保存模型、地址和密钥槽；“路线”组织分组及 Auto 顺序。乌合之众角色、本机 Codex 回退与 Codex 原生子代理分别管理。Orca、WorkBuddy、Trae 等独立产品不属于本服务自动接入范围。

发布样例为每条路线显式设置 `native_codex_fallback=false`。旧配置缺字段时仅路线 A 保留兼容默认值；迁移时应检查并明确保存该开关。配置文件损坏或不是 JSON 对象时，服务会报告文件路径并拒绝启动，避免静默恢复默认后覆盖原配置。

## 能做什么

- **路线和分组：**在路线中按顺序配置模型分组，可手动选组，也可按路线的 Auto 设置依次尝试启用的分组。启用原生 Codex 回退时，它显示在 Auto 顺序末尾；没有 Auto 分组时会直接使用该回退。删除启用路线的最后一个 Auto 分组且未启用原生回退时，该路线会自动停用。启用多条路线时，Auto 需要明确选择路线；工作目录会解析符号链接/联接后限制在配置的工作区内。
- **任务角色：**内置勘察员（scout）、审查员（reviewer）、验证员（tester）、编码员（coder）和自由者（free）。勘察员、审查员使用只读沙箱；其他角色使用工作区写入沙箱。自由者适合非代码任务，只在明确要求代码工作时检查或修改源码。
- **活动和结果：**记录任务进度，支持并行派发、取消、查看结果、保留活动和管理保存的子代理配置。并发上限可在“子代理配置”页选择 1–8，默认 8；调低后已运行任务会继续，空出位置后才启动新任务。勾选“允许主控复调用”后，主控会对照原任务目标检查结果；未完成时可通过 `laowu_continue_task` 在同一会话提出具体修改，已满足时不为可选润色重复续聊。
- **本地配置：**在面板管理路线、模型、API 地址和密钥。密钥不会写入项目配置文件。

并行写入任务共享所选工作区，不会自动建立 worktree 或合并改动；主控须为任务分配互不冲突的文件。子任务思考强度当前固定为 `medium`。只需 Codex 自身模型分工的用户也可以评估原生子代理；本项目适合需要外部 provider 路由与可视化管理的场景。

调用失败时，服务会区分可重试错误和需要停止处理的错误。已保存的路线优先用于 Auto；未保存路线时，只有一条启用且配置了 Auto 分组的路线会被自动选中，多条可用路线时必须通过 `route_choice` 明确选择。Auto 只会在选定路线内，对可重试错误按顺序尝试后续启用分组；手动选择分组时只调用该组。每条路线可单独允许原生 Codex 回退：Auto 的可重试错误耗尽或手动非默认分组的可重试失败后，服务先检查并把工作区状态带入任务上下文，再最多启动一次同角色的本机 `codex exec --ephemeral`。新路线默认关闭；缺少该字段的旧配置仅 `route_a` 兼容开启。超时、取消、不可重试或未分类错误、清理状态不确定以及 `default` 组失败都不会回退；不会跨路线或自动调用 default。原生 Codex 使用本机 Codex 登录状态，不使用外部 provider API key。启用路线、分组及其调用顺序以本机设置为准。

## 工具调用与结果交付

所有发起入口（`run_subagent_*`、`run_subagent_profile`、兼容入口 `run_subagent_task`、面板发起）以及 `laowu_continue_task` 都先返回 `activity_id`。`run_subagents_parallel` 每批最多接收 8 项；实际同时运行的代理数由“子代理配置”页的并发上限控制（1–8），超出的任务在共享执行池中等待。默认 8 个运行槽，队列最多等待 32 项。

1. 用 `laowu_task_result` 的 `action="get"` 查询活动 ID；queued/running 时继续等待同一任务。
2. 终态从 `offset=0` 读取正文，每页最多 12,000 字符；继续使用返回的 `next_offset`，直到它为 `null`。`total_chars` 是完整结果长度，`complete` 表示当前页已到末尾。
3. 汇总并使用所有页后调用 `action="acknowledge"`。未顺序读取全部正文时，服务拒绝清除。重试同一页不会重复消费结果。
4. 对照原任务和验收要求评估结果。若仍有明确未完成项，且该活动允许主控复调用，就用同一 `activity_id` 调用 `laowu_continue_task`，附上具体修正和验证要求；不要为普通修改另起重复任务。若目标已满足，不为可选润色续聊；遇到阻塞或反复失败时汇报原因并停止。

这是相对早期同步工具返回正文的接口变更；更新自定义主控提示词和调用脚本，并重启 MCP，使工具 schema 与源码一致。宿主断开不应直接重复派发：先核对活动；服务在 stdio 断开时通知现有工作停止，重启后将未完成结果标记为 interrupted。

面板会取完分页结果再确认交付，并提供“查看完整结果”。普通活动的完整结果随本次 MCP 进程保留；点击“保留”后，完整结果随记录持久化，重启后可再次读取。删除记录会移除其本地全文副本；确认待取结果只清除待交付副本。排队和运行中的任务都应先停止，再删除记录。

全局 Codex 调用开关及角色/profile 权限同时作用于发起和续聊；关闭不会自动终止已运行任务，可单独停止。面板手动发起仍属于用户操作。续聊必须先读取并确认上一份待取结果，保留原会话、provider、模型、工作区和 MCP/Skills 能力，不重新走 Auto 或跨 provider 回退。新建活动同时记录 provider 地址和身份；编辑后拒绝旧会话续聊，应新建任务。早期记录缺少地址快照，建议更新后新建活动再验收此边界。本机 Codex 回退使用一次性会话，完成后不会继续展示先前失败 provider 的续聊入口。

## 运行要求

- Windows PowerShell，用于当前用户加密保存 API 密钥。
- Python 3.11 或更新版本。
- Codex CLI 已安装，并能从 MCP 服务进程的 `PATH` 中找到；也可通过 `CODEX_CLI_PATH` 指定真正的 `codex.exe` 文件路径。Windows 的 `.cmd` 和 `.bat` 启动器不受支持，服务会拒绝它们并提示改用可执行文件路径。
- Codex 中注册本服务为 stdio MCP server。
- 可选网页搜索需要 Node.js 和本机 Chrome 或 Edge。当前实现仅检查 `C:\Program Files\nodejs\node.exe`，以及 `C:\Program Files\Google\Chrome\Application\chrome.exe`、`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`、`C:\Program Files\Microsoft\Edge\Application\msedge.exe`；不会从 `PATH` 搜索 Node，也没有浏览器路径配置项。浏览器脚本 `headless_cdp.js` 需与 MCP 文件一起保留。缺少这些依赖时，普通代码任务仍可使用，但网页搜索不可用。

当前凭据脚本是 Windows 专用；本项目尚未声明或验证 macOS、Linux 支持。

开始配置前，可在 PowerShell 检查核心工具：

```powershell
python --version
Get-Command codex -All | Select-Object CommandType, Source
codex --version
```

`python` 需为 3.11 或更新版本。若 `codex` 解析为 `.cmd` 或 `.bat`，请找到实际的 `codex.exe`，并通过 `CODEX_CLI_PATH` 指定。只有启用网页搜索时才需要检查可选浏览器环境：运行 `& 'C:\Program Files\nodejs\node.exe' -p "typeof WebSocket"` 应输出 `function`；Node.js、Chrome 和 Edge 都必须位于上文列出的路径。

## 首次配置

1. 将 `tools/laowu_mcp/user_config.example.json` 复制为同目录的 `user_config.json`。
2. 检查 `paths.workspace`。样例默认值 `../..` 是相对于 `user_config.json` 所在目录解析的，适用于把 MCP 放在项目的 `tools/laowu_mcp` 目录中。工作区是允许派发任务的根目录；服务会解析真实路径（包括符号链接/联接）并拒绝工作区之外的目录。若 MCP 工具安装目录与实际项目目录分开，请把 `workspace` 改为实际项目目录的绝对路径。
3. 在本机 Codex 配置中，为样例里的 `model_provider` 建立匹配的 provider 定义，并让该定义使用对应的 `key_name` 环境变量。provider API 地址必须使用 HTTPS；仅 localhost 或 loopback IP 可使用 HTTP，以支持本地服务。外部服务需符合该 Codex provider 定义使用的 API 协议。不要将真实密钥写进 `user_config.json` 或提交到版本控制。
4. 在用户级 `~/.codex/config.toml` 中注册本地 MCP 服务。把示例中的路径换成自己的实际路径：

   ```toml
   [mcp_servers.wuhezhizhong]
   command = "python"
   args = ["C:/path/to/AAA/tools/laowu_mcp/server.py"]
   ```

   `command` 也可以填 Python 可执行文件的绝对路径。Codex CLI、桌面应用和 IDE 扩展共用此 MCP 配置；可在 Codex 中查看 MCP 服务状态，或运行 `codex mcp list`。更多 MCP 配置选项见 [Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)。
5. 重启 Codex，使 MCP 服务加载当前配置。服务运行后，从 Codex 打开“乌合之众”活动面板。
6. 在设置页添加或启用路线分组，填写模型 API 地址和模型 ID，再保存 API 密钥。面板会调用本地 PowerShell helper，将密钥加密保存到 `keys.xml`；保存后当前服务进程可立即使用新密钥。
7. 对支持标准 `/models` 接口的服务，可以在面板查询模型列表；也可以手动填写模型 ID。查询模型列表不等于验证模型一定支持 Codex 执行所需的工具调用能力。

默认情况下，所有相对 `paths`（例如 `workspace`、`key_setup`、`ui`、状态和日志文件）都相对于 `user_config.json` 所在目录解析。若通过环境变量 `LAOWU_USER_CONFIG_PATH` 指定外置配置文件，路径仍相对于该配置文件的位置，而不是 MCP 源码目录；独立部署时建议将 `workspace`、`key_setup` 和 `ui` 写成绝对路径，或确认相对路径从配置文件目录能正确到达目标。下面是工具安装目录和项目目录分开的示例，路径均需替换为本机实际路径：

```json
{
  "paths": {
    "workspace": "C:/path/to/my-project",
    "key_setup": "C:/path/to/AAA/tools/laowu_mcp/set-provider-keys.ps1",
    "credential_store": "C:/path/to/user-data/keys.xml",
    "ui": "C:/path/to/AAA/laowu-sidebar.html",
    "state": "C:/path/to/user-data/laowu-state.json",
    "ui_preferences": "C:/path/to/user-data/ui-preferences.json",
    "diagnostic_log": "C:/path/to/user-data/laowu-dispatch-debug.jsonl"
  }
}
```

将上述 `paths` 对象放入外置 `user_config.json`，并在 MCP 注册项中指定该文件，例如：

```toml
[mcp_servers.wuhezhizhong]
command = "python"
args = ["C:/path/to/AAA/tools/laowu_mcp/server.py"]

[mcp_servers.wuhezhizhong.env]
LAOWU_USER_CONFIG_PATH = "C:/path/to/user-data/user_config.json"
```

替换这些占位路径，并确认 MCP 文件、helper、面板、配置和工作区都存在后，此布局即可使用。`workspace` 指向实际项目目录，且该目录必须是允许派发任务的根目录。

保存配置后重启 Codex/MCP 服务，使新路径生效。

样例中的 provider ID 和 `model_provider` 名称是配置占位符，不代表项目内置了这些服务。要增加样例以外的 provider，须在 `user_config.json` 的 `providers` 中添加映射（至少包含 `key_name` 和 `model_provider`），并在 Codex 配置中定义相应的 `model_provider`；之后重启 MCP 服务，使启动时加载的新映射生效。仅通过 helper 更新已映射 provider 的密钥时，不需为密钥本身重启；helper 不会修改 provider 映射。路线与分组只会使用 MCP 已加载的 provider。

### Codex provider 配置示例

在用户级 `~/.codex/config.toml` 中，为每个 `model_provider` ID 添加一段配置。以下示例对应样例配置里的 `provider_a_group_1` 和 `PROVIDER_A_GROUP_1_API_KEY`；把 ID、密钥变量名和地址替换成自己的值。**不要把 API 密钥本身写进 TOML。**

```toml
[model_providers.provider_a_group_1]
name = "External provider A"
base_url = "https://api.example.com/v1"
env_key = "PROVIDER_A_GROUP_1_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

乌合之众会在启动子代理时设置对应的密钥环境变量，并用面板中保存的 API 地址覆盖该 provider 的 `base_url`。Codex 自定义 provider 的 `wire_api` 目前使用 Responses 协议；只支持 Chat Completions 的站点不能仅凭 `/models` 查询成功就视为兼容。配置应放在用户级 `config.toml`；Codex 不接受项目级 `.codex/config.toml` 中的 provider 定义。[Codex Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)

如果通过命令行 helper 而不是面板添加密钥，先确保已创建本地 `user_config.json`。面板保存密钥会立即更新当前 MCP 进程内存中的密钥并写入加密存储，因此可立即用于后续任务；helper 仅写入加密存储，不会通知已运行的 MCP 进程，故使用 helper 后需重启 MCP 服务（通常重启 Codex）以重新加载密钥。若改动 `user_config.json` 的 provider 映射，也需重启 MCP 服务。

helper 支持 `-SetKey <provider_id>`（交互式设置单个 provider）、`-SetKeyFromStdin`（由面板调用，读取 JSON 输入）、`-ClearAll`（清除密钥存储）和 `-ForRunner`（输出供任务启动器读取的密钥 JSON）。最小的交互式示例：

```powershell
Set-Location C:\path\to\AAA\tools\laowu_mcp
.\set-provider-keys.ps1 -SetKeyName PROVIDER_A_GROUP_1_API_KEY
```

手动操作外置配置时，helper 需显式添加 `-UserConfigPath C:\path\to\user-data\user_config.json`；不传时使用 helper 所在目录的 `user_config.json`。`LAOWU_USER_CONFIG_PATH` 由 MCP 服务读取，服务调用 helper 时会传入选定路径。

`SetKeyName` 使用配置中的稳定密钥槽名称，因此旧版本地分组 ID 迁移期间也可用。新配置也可用 `-SetKey route_a_group_1` 按 provider ID 选择密钥。也可不传这两个参数，依次为配置中的 provider 输入密钥；留空会跳过该项。

## 密钥、隐私与工作区权限

- `keys.xml` 使用 Windows 当前用户可解密的 SecureString 存储。换 Windows 用户或迁移到另一台电脑时，不要假设原密钥文件仍可用；通常需要重新录入密钥。
- 提示词、所选工作目录中的文件内容以及模型生成的结果会发送给本次任务选用的外部 provider。请只使用你信任的服务，并避免把无关的机密信息交给外部模型。
- 本地活动状态可能保存任务描述、可见进度和结果；诊断日志用于本地排查。两者默认保存在配置指定的本机路径中。
- 派发的子代理运行于本机用户权限下的 Codex CLI 子进程，并受其任务角色对应的沙箱限制。勘察员和审查员为只读；验证员和编码员允许在工作区内写入。
- 子进程保留 PATH、CODEX_HOME 等运行环境，过滤已登记的 provider 密钥变量以及名称中以独立字段出现的 KEY、TOKEN、SECRET、PASSWORD、PASSWD、CREDENTIAL 等常见凭据变量；外部任务只重新注入本次 provider 的密钥。原生回退使用本机 Codex 登录状态。这个过滤按变量名工作，不保证识别所有自定义秘密名称，也不限制显式 MCP 配置或任务工具对文件的访问。
- 可选浏览器只提供公开网页搜索和 HTTPS 页面读取。子代理指引禁止向浏览器查询发送私有代码、凭据或个人数据；网页内容应视作不可信输入。

## 配置和数据重置

路线、provider 映射和本地文件路径在 `user_config.json` 中配置。密钥保存在 `keys.xml`；活动、待取结果和保存的子代理配置保存在状态文件；界面偏好保存在 `ui_preferences.json`。实际位置可由 `paths` 设置覆盖。

`paths.credential_store` 应指向仅供本工具使用的密钥文件。“清理本机数据”调用 `-ClearAll` 时会删除该文件本身；不要将其配置为包含其他应用或无关凭据的共享存储。

设置页提供“清理本机数据”操作，确认后会清除密钥、自定义路线与分组设置、provider 的标签/模型/API 地址、外观偏好、面板本机偏好、自定义子代理配置、Codex 发起权限、活动历史、待交付结果和诊断日志，并恢复程序默认的路线、分组和 provider 配置。`paths` 中的路径覆盖会保留，以免改变本机数据位置；Codex 登录状态、Codex 对话记录和宿主 MCP 注册由 Codex 管理，不属于本工具的清理范围。若存在排队或运行中的子代理（包括续聊），重置会被拒绝；已完成但尚未读取的结果也会随重置清除。

## 开发目录

- `tools/laowu_mcp/server.py` — MCP 服务、路线解析、派发、活动与结果管理。
- `tools/laowu_mcp/process_runner.py` — 转发 Codex 子进程的标准输入输出，并保持可供进程树清理的监督进程。
- `tools/laowu_mcp/headless_browser_mcp.py` 和 `headless_cdp.js` — 可选的只读浏览器工具。
- `tools/laowu_mcp/set-provider-keys.ps1` — 本地加密密钥 helper。
- `tools/laowu_mcp/user_config.example.json` — 不含真实凭据的配置样例。
- `laowu-sidebar.html` — 活动面板和设置界面。
- `tests/` 及 `tools/laowu_mcp/test_*.py` — 离线测试。
- `tools/run_tests.py` — 两组隔离测试入口；面板行为检查需要 Node.js。
- `tools/build_release.py`、`release-files.json` — 显式公共文件清单与源码打包。
- `docs/release.md` — 发布准备、候选包和正式包构建说明。

## 贡献

- 普通问题可通过 GitHub Issues 反馈，并提供复现步骤、预期结果和实际结果；Issue 与评论是公开的。漏洞细节、利用步骤、凭据和私有数据不要发到公开 Issue、PR 或讨论区。GitHub 仓库公开前应启用 Private vulnerability reporting；启用后请使用 Security 页面中的“Report a vulnerability”私密表单。若看不到该入口，只开一个不含漏洞细节的 Issue，请求维护者提供私密联系渠道。更多说明见 [安全政策](SECURITY.md)。私密入口的实际配置状态见 [发布验收表](docs/acceptance.md)。
- 提交 PR 前，请说明改动目的和影响范围，并运行相关离线测试：`python -B tools/run_tests.py`。PR 应附上实际运行的命令和结果；不要把未运行的测试写成通过。
- 涉及 Codex 宿主、MCP App、Codex CLI 或外部 provider 的改动，还需在目标 Codex 宿主和 provider 环境中核验，并在 PR 中说明具体环境、步骤和结果。离线测试只覆盖本地模拟和回归检查，不代表真实 Codex/provider 集成已通过。

详细开发步骤及提交要求见 [贡献指南](CONTRIBUTING.md)。核心 Python 服务使用标准库，无需安装 pip 依赖；完整离线测试另外需要 Node.js。

## 发布

完整步骤见 [发布说明](docs/release.md)。源码发布应从已审查的 Git commit 或 tag 构建，并在发布前复查该版本和发布文件清单。不要直接压缩当前工作区：其中可能包含未提交改动、本地配置、凭据、状态、日志或测试临时文件。离线测试通过也不能替代真实 Codex 宿主与 provider 集成核验；发布说明应清楚区分实际完成的检查。

运行离线测试：

```powershell
python -B tools/run_tests.py
```

入口为两组测试分别启动进程，把配置、Codex home、临时文件、状态和日志隔离在仓库内的临时目录，并屏蔽真实密钥加载；凭据测试只使用独立的合成密钥库。临时目录结束后自动清理。也可只运行一组或一个测试文件：

```powershell
python -B tools/run_tests.py --suite mcp
python -B tools/run_tests.py --suite tests --pattern test_task_lifecycle.py
```

## 许可

本项目采用 [MIT 许可证](LICENSE)。任何人都可以使用、修改、分发或出售本项目及其副本，也可以将修改版作为闭源软件发布；再发布时必须保留版权声明和许可证文本。软件按现状提供，不附带任何明示或默示保证；完整条款见 [LICENSE](LICENSE)。
