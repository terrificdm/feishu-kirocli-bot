# Feishu-KiroCLI Bot

[English](README.md)

将飞书（Lark）群聊/私聊消息桥接到 Kiro CLI，通过 ACP 协议驱动 AI 编程助手。

## 架构

```
飞书 WebSocket ──→ Bridge ──→ kiro-cli acp (按需启动)
     ↑                │
     └── 回复 ←───────┘
```

## 功能

- 飞书 WebSocket 长连接实时接收消息
- 群聊 @机器人 触发，私聊直接处理
- 每个群聊/用户独立工作目录和会话
- **🔐 交互式权限审批** - Kiro 执行敏感操作前会询问用户（y/n/t）
- **⚡ 按需启动** - 收到消息才启动 kiro-cli，节省资源
- **⏱️ 空闲自动关闭** - 可配置空闲超时，自动关闭 kiro-cli
- **🛑 取消功能** - 发送"取消"可中断当前操作
- **🔧 MCP 服务器支持** - 可配置额外的 MCP 工具
- 自动使用已配置的全局 MCP servers 和 Skills
- 展示工具调用过程 + 最终回复
- 并发控制（同一会话同时只处理一个请求）
- kiro-cli 进程异常自动重启

## 权限审批流程

当 Kiro 需要执行文件操作、命令执行等敏感操作时：

```
1. Kiro: 我要创建 hello.txt
2. 机器人: 🔐 Kiro 请求执行操作:
          📋 Creating hello.txt
          回复: y(允许) / n(拒绝) / t(信任，本会话始终允许)
          ⏱️ 60秒内未回复将自动拒绝
3. 用户: y
4. Kiro: (执行操作)
5. 机器人: 📝 Creating hello.txt ✅
          Done. Created hello.txt
```

支持的回复：
- **y** / yes / 是 / 允许 / 同意 / ok - 本次允许
- **n** / no / 否 / 拒绝 / 不 - 本次拒绝
- **t** / trust / always / 总是 / 始终允许 / 信任 - 本会话始终允许此工具

## 按需启动模式

为节省资源，kiro-cli 采用按需启动：

1. **程序启动** → 只连接飞书，不启动 kiro-cli
2. **收到消息** → 启动 kiro-cli，处理请求
3. **空闲超时** → 自动关闭 kiro-cli（默认 5 分钟）
4. **再次收到消息** → 重新启动 kiro-cli（新会话）

设置 `IDLE_TIMEOUT=0` 可禁用自动关闭。

## 前置条件

- Python 3.9+
- [kiro-cli](https://kiro.dev/docs/cli/) 已安装并登录
- 飞书应用机器人（需要 WebSocket 事件订阅）

## 飞书应用配置

1. 在 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用
2. 添加「机器人」能力
3. 配置权限：
   - `im:message` - 发送和接收消息
   - `im:message.group_at_msg` - 接收群内 @消息
4. 事件订阅 → 启用 WebSocket 模式
5. 订阅事件：`im.message.receive_v1`
6. 发布应用

## 安装

```bash
cd feishu-kirocli-bot
pip install -e .
```

## 配置

```bash
cp .env.example .env
# 编辑 .env 填入你的配置
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | 是 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 是 | 飞书应用 App Secret |
| `BOT_NAME` | 是 | 机器人显示名称（用于 @提及检测） |
| `KIRO_CLI_PATH` | 否 | kiro-cli 路径（默认：kiro-cli） |
| `WORKING_DIR` | 否 | 工作目录根路径（默认：/tmp/feishu-kirocli-bot-workspaces） |
| `MCP_SERVERS` | 否 | 额外的 MCP 服务器配置（JSON 数组） |
| `IDLE_TIMEOUT` | 否 | 空闲超时秒数（默认：300，0=禁用） |
| `DEBUG` | 否 | 调试模式（默认：false） |

### MCP 服务器配置

可选配置额外的 MCP 工具服务器（全局配置的 MCP servers 会自动可用）：

```bash
MCP_SERVERS='[{"name": "github", "serverType": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}]'
```

## 运行

```bash
python bridge.py
```

## 使用

- **群聊**：@机器人 + 你的问题
- **私聊**：直接发送消息
- **取消**：发送「取消」或「cancel」中断当前操作
- **权限审批**：收到权限请求时回复 y/n/t

### 命令

| 命令 | 说明 |
|------|------|
| `/agent` | 查看可用 Agent |
| `/agent <代理名>` | 切换 Agent |
| `/model` | 查看可用模型 |
| `/model <模型名>` | 切换模型 |
| `/help` | 显示帮助 |

## 图标说明

| 图标 | 含义 |
|------|------|
| 📄 | 文件读取 |
| 📝 | 文件编辑 |
| ⚡ | 终端命令 |
| 🔧 | 其他工具 |
| ✅ | 执行成功 |
| ❌ | 执行失败 |
| ⏳ | 执行中 |
| 🚫 | 已拒绝 |
| 🔐 | 权限请求 |

## 项目结构

```
feishu-kirocli-bot/
├── acp_client.py   # ACP 协议客户端（JSON-RPC over stdio）
├── bridge.py       # 飞书↔Kiro 桥接逻辑
├── config.py       # 配置管理
├── feishu_bot.py   # 飞书 WebSocket 连接
└── README.md       # 文档
```
