# Feishu-KiroCLI Bot

[中文文档](README.zh-CN.md)

Bridge Feishu (Lark) group/private chat messages to Kiro CLI via ACP protocol.

## Architecture

```
Feishu WebSocket ──→ Bridge ──→ kiro-cli acp (on-demand)
       ↑                │
       └── Reply ←──────┘
```

## Features

- Real-time message receiving via Feishu WebSocket
- Group chat: triggered by @bot mention; Private chat: direct processing
- Independent workspace and session for each chat/user
- **🔐 Interactive Permission Approval** - Kiro asks user (y/n/t) before sensitive operations
- **⚡ On-Demand Startup** - kiro-cli starts only when message received, saves resources
- **⏱️ Auto Idle Shutdown** - Configurable idle timeout to auto-stop kiro-cli
- **🛑 Cancel Operation** - Send "cancel" to interrupt current operation
- **🔧 MCP Server Support** - Configure additional MCP tools
- Auto-uses globally configured MCP servers and Skills
- Shows tool call progress + final response
- Concurrency control (one request per session at a time)
- Auto-restart on kiro-cli process failure

## Permission Approval Flow

When Kiro needs to perform sensitive operations like file operations or command execution:

```
1. Kiro: I need to create hello.txt
2. Bot:  🔐 Kiro requests permission:
         📋 Creating hello.txt
         Reply: y(allow) / n(reject) / t(trust, always allow in this session)
         ⏱️ Auto-reject in 60 seconds if no response
3. User: y
4. Kiro: (executes operation)
5. Bot:  📝 Creating hello.txt ✅
         Done. Created hello.txt
```

Supported responses:
- **y** / yes / ok - Allow this time
- **n** / no - Reject this time
- **t** / trust / always - Always allow this tool in current session

## On-Demand Mode

To save resources, kiro-cli uses on-demand startup:

1. **Program starts** → Only connects to Feishu, kiro-cli not started
2. **Message received** → Starts kiro-cli, processes request
3. **Idle timeout** → Auto-stops kiro-cli (default 5 minutes)
4. **Next message** → Restarts kiro-cli (new session)

Set `IDLE_TIMEOUT=0` to disable auto-shutdown.

## Prerequisites

- Python 3.9+
- [kiro-cli](https://kiro.dev/docs/cli/) installed and logged in
- Feishu app bot (requires WebSocket event subscription)

## Feishu App Configuration

1. Create an enterprise app on [Feishu Open Platform](https://open.feishu.cn/)
2. Add "Bot" capability
3. Configure permissions:
   - `im:message` - Send and receive messages
   - `im:message.group_at_msg` - Receive group @messages
4. Event subscription → Enable WebSocket mode
5. Subscribe to event: `im.message.receive_v1`
6. Publish the app

## Installation

```bash
cd feishu-kirocli-bot
pip install -e .
```

## Configuration

```bash
cp .env.example .env
# Edit .env with your configuration
```

| Variable | Required | Description |
|----------|----------|-------------|
| `FEISHU_APP_ID` | Yes | Feishu App ID |
| `FEISHU_APP_SECRET` | Yes | Feishu App Secret |
| `BOT_NAME` | Yes | Bot display name (for @mention detection) |
| `KIRO_CLI_PATH` | No | kiro-cli path (default: kiro-cli) |
| `WORKING_DIR` | No | Workspace root path (default: /tmp/feishu-kirocli-bot-workspaces) |
| `MCP_SERVERS` | No | Additional MCP server config (JSON array) |
| `IDLE_TIMEOUT` | No | Idle timeout in seconds (default: 300, 0=disabled) |
| `DEBUG` | No | Debug mode (default: false) |

### MCP Server Configuration

Optionally configure additional MCP tool servers (globally configured MCP servers are auto-available):

```bash
MCP_SERVERS='[{"name": "github", "serverType": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}]'
```

## Running

```bash
python bridge.py
```

## Usage

- **Group chat**: @bot + your question
- **Private chat**: Send message directly
- **Cancel**: Send "cancel" or "取消" to interrupt current operation
- **Permission approval**: Reply y/n/t when permission request received

### Commands

| Command | Description |
|---------|-------------|
| `/agent` | List available agents |
| `/agent <name>` | Switch agent |
| `/model` | List available models |
| `/model <name>` | Switch model |
| `/help` | Show help |

## Icon Legend

| Icon | Meaning |
|------|---------|
| 📄 | File read |
| 📝 | File edit |
| ⚡ | Terminal command |
| 🔧 | Other tool |
| ✅ | Success |
| ❌ | Failed |
| ⏳ | In progress |
| 🚫 | Rejected |
| 🔐 | Permission request |

## Project Structure

```
feishu-kirocli-bot/
├── acp_client.py   # ACP protocol client (JSON-RPC over stdio)
├── bridge.py       # Feishu↔Kiro bridge logic
├── config.py       # Configuration management
├── feishu_bot.py   # Feishu WebSocket connection
└── README.md       # Documentation
```
