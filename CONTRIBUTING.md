# 贡献指南

欢迎提交可复现的问题、文档改进、测试和代码修改。当前范围是 Windows 上的 Codex 本地 MCP 委派；跨平台、自动 worktree 和新的 provider 协议适配应先讨论具体需求。

## 开发与验证

1. 安装 Python 3.11+、Git 和 Node.js 24。核心 Python 服务只使用标准库，不需要安装 pip 依赖。真实任务另外需要可用的 Codex CLI 和兼容 provider。
2. 阅读 [快速上手](docs/quickstart.md)、[兼容性](docs/compatibility.md) 和 [发布说明](docs/release.md)。
3. 使用独立分支修改；避免覆盖他人的未提交工作。新增公开文件同时更新 `release-files.json`，必要时更新构建器必需文件集合。
4. 运行 `python -B tools/run_tests.py`。测试自动隔离配置、临时目录、Codex home、状态和凭据；不要移除这种隔离来让测试通过。
5. 影响路由、进程清理、权限或结果交付时，补充能重现问题的回归检查。工具 schema 变化同步更新 `tools/laowu_mcp/test_server.py`。

GitHub Actions 配置会在 Windows 的 Python 3.11、3.12 和 3.13 上执行离线测试和源码构建。工作流提交到 GitHub 后才会实际运行；本地通过不代表云端已通过。

## PR 内容

说明问题、最终行为、实际验证命令与结果、尚未验证的环境。宿主面板或 provider 行为变化请按 [验收表](docs/acceptance.md) 记录环境和证据。不要把模型列表查询、模拟测试或静态断言写成真实外部模型验收。

API key、本机配置、密钥库、活动历史、完整诊断日志和私人提示词不应提交。并行编码任务应分配互不冲突的文件；当前工具不会自动创建 worktree 或合并任务改动。

## 安全与许可

漏洞按 [安全政策](SECURITY.md) 私密报告。普通问题使用仓库 Issues；附日志或截图前自行脱敏。提交内容应为你有权贡献的材料，并接受仓库 MIT 许可；引入第三方代码时保留必要的许可与来源说明。
