# Feishu-KiroCLI Bot

[中文文档](README.zh-CN.md)

Bridge Feishu (Lark) group/private chat messages to Kiro CLI via ACP protocol.

> **Which repo to use?**
> - **This repo** (`feishu-kirocli-bot`): Feishu only, lightweight and simple.
> - [`kirocli-bot-gateway`](https://github.com/terrificdm/kirocli-bot-gateway): Multi-platform (Feishu + Discord + more). Use this if you need multiple platforms or plan to expand later.

## Architecture

```
Feishu WebSocket ──→ Bridge ──→ kiro-cli acp (on-demand)
       ↑                │
       └── Reply ←──────┘
```

## Features

- Real-time message receiving via Feishu WebSocket
- Group chat: triggered by @bot mention; Private chat: direct processing
- **📁 Workspace Modes** - `per_chat` (isolated per user) or `fixed` (shared project directory)
- **🔐 Interactive Permission Approval** - Kiro asks user (y/n/t) before sensitive operations
- **⚡ On-Demand Startup** - kiro-cli starts only when message received, saves resources
- **⏱️ Auto Idle Shutdown** - Configurable idle timeout to auto-stop kiro-cli
- **🛑 Cancel Operation** - Send "cancel" to interrupt current operation
- **🔧 MCP & Skills Support** - Global or workspace-level config (`.kiro/settings/mcp.json`, `.kiro/skills/`)
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

1. Create an enterprise app on [Feishu Open Platform](https://open.feishu.cn/app)
   - Click **Create Enterprise Self-Built App**
   - Fill in app name and description

2. Get credentials: In **Credentials & Basic Info**, copy **App ID** (format: `cli_xxx`) and **App Secret** into your `.env` file

3. Add "Bot" capability: In **App Features** > **Bot**, enable bot — `BOT_NAME` in your `.env` must match the bot's display name in Feishu (usually the same as the app name)

4. Configure permissions (you can bulk import via the Feishu Open Platform permissions page):
   - `im:message` - Read and write messages (base permission)
   - `im:message:send_as_bot` - Send messages as bot
   - `im:message:readonly` - Read message history
   - `im:message.group_at_msg:readonly` - Receive group @messages
   - `im:message.p2p_msg:readonly` - Receive private chat messages
   - `im:chat.access_event.bot_p2p_chat:read` - Private chat events
   - `im:chat.members:bot_access` - Bot group membership access
   - `im:resource` - Access message resources (images, files, etc.)

   <details>
   <summary>Bulk import JSON</summary>

   ```json
   {
     "scopes": {
       "tenant": [
         "im:message",
         "im:message:send_as_bot",
         "im:message:readonly",
         "im:message.group_at_msg:readonly",
         "im:message.p2p_msg:readonly",
         "im:chat.access_event.bot_p2p_chat:read",
         "im:chat.members:bot_access",
         "im:resource"
       ],
       "user": []
     }
   }
   ```

   </details>

5. Start the bot first (required for event subscription to save):
   ```bash
   python bridge.py
   ```
   The bot only connects to Feishu WebSocket — it won't receive any messages yet, but the connection is needed for the next step.

6. Event subscription: In **Event Subscription**, select **Use long connection to receive events** (WebSocket) — no public webhook URL required
   - Add event: `im.message.receive_v1`

7. Publish the app: In **Version Management & Release**, create a version and publish
   - Enterprise self-built apps are usually auto-approved
   - Permission changes require publishing a new version to take effect

> ⚠️ **External Group Limitation**: Due to Feishu's access control, the bot can **only** be added to internal enterprise groups by default. For external groups, see Feishu documentation.

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
| `WORKSPACE_MODE` | No | Workspace mode: `per_chat` (default) or `fixed` |
| `WORKING_DIR` | No | Workspace root path (default: /tmp/feishu-kirocli-bot-workspaces) |
| `IDLE_TIMEOUT` | No | Idle timeout in seconds (default: 300, 0=disabled) |
| `DEBUG` | No | Debug mode (default: false) |

### Workspace Modes

- **per_chat** (default): Each chat gets its own subdirectory under `WORKING_DIR`. Suitable for multi-user scenarios where each user needs isolated workspace.

- **fixed**: All chats share the same `WORKING_DIR`. Suitable for project-specific use cases — point to an existing project directory to let Kiro work with its files and use project-level `.kiro/` configurations.

### MCP Server Configuration

MCP servers can be configured at two levels:

1. **Global** (`~/.kiro/settings/mcp.json`): Available in all modes
2. **Workspace** (`{WORKING_DIR}/.kiro/settings/mcp.json`): Only in `fixed` mode

For `fixed` mode, you can place `.kiro/settings/mcp.json` and `.kiro/skills/` in your `WORKING_DIR` to use project-specific MCP servers and skills.

```bash
# Add an MCP server globally
kiro-cli mcp add --name memory --command npx --args '"-y","@modelcontextprotocol/server-memory"' --scope global

# List configured servers
kiro-cli mcp list
```

## Running

```bash
python bridge.py
```

## Usage

- **Group chat**: @bot + your question
- **Private chat**: Send message directly
- **Cancel**: Send "cancel" to interrupt current operation
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
