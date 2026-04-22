# biMEMO User Guide

## Getting Started

### Web Interface

1. Open http://localhost:5273 for the development frontend
2. Enter your application password when setup or unlock is required
3. Start chatting!

biMEMO has one application password. Manual unlock means a person enters that
password in the browser. Automatic unlock means Docker provides the same
password from a local secret file at startup.

### Telegram Bot

1. Configure Telegram integration via Admin panel (see [INSTALL.md](INSTALL.md))
2. Message your bot: `/start`
3. Chat naturally

### Discord Bot

1. Configure Discord integration via Admin panel (see [INSTALL.md](INSTALL.md))
2. Send a DM to your bot
3. Chat naturally

All three channels (Web, Telegram, Discord) share the same conversation context and memory.

---

## Features

### Chat

Send messages and get AI responses. The assistant remembers conversation context.

```
User: What is Python?
AI: Python is a high-level programming language...
```

### Tools

biMEMO can use tools to help you:

#### GREEN Tools (automatic)

- **file_read** — Read files from allowed directories
- **file_search** — Search files by name
- **reminder_list** — List reminders

```
User: Read my notes.txt
AI: [reads file] Here's the content of notes.txt...
```

#### YELLOW Tools (notify after)

- **file_write** — Write files
- **web_search** — Search the web via SearXNG

```
User: Search for "biMEMO AI"
AI: [searches web] Found these results...
```

#### RED Tools (require confirmation)

- **email_send** — Send emails
- **http_fetch** — Make HTTP requests

```
User: Send email to john@example.com
Confirmation required
   Tool: email_send
   To: john@example.com
   Body: [draft]

[Confirm] [Deny]
```

On Telegram: inline keyboard buttons. On Discord: discord.ui.View buttons. On Web: ConfirmationCard component.

### Memory System

biMEMO automatically manages conversation context:

- **Dialog Recorder** — Monitors conversations, extracts key information
- **Summary Distiller** — Creates summary distillations periodically
- **Vector search** — Find relevant past conversations
- **FTS5** — Keyword-based search across messages and observations

### Trust Zones

Tools are categorized by risk:

| Zone | Icon | Behavior | Examples |
|------|------|----------|----------|
| GREEN | safe | Automatic | file_read, search |
| YELLOW | notify | Notify after | file_write, web_search |
| RED | confirm | Confirm first | email_send, http_fetch |

---

## Configuration

### Changing Models

The normal path is the Admin UI: open the Web UI, go to the admin area, and
edit the active provider under LLM Configuration.

For repeatable local setups, you can also seed provider settings through
`.env` before startup:

```env
# For Ollama
BIMEMO_OLLAMA_MODEL=qwen3:14b
BIMEMO_OLLAMA_DIALOG_RECORDER_MODEL=qwen3:4b

# For OpenRouter
BIMEMO_OPENROUTER_MODEL=qwen/qwen3-14b
BIMEMO_OPENROUTER_DIALOG_RECORDER_MODEL=qwen/qwen3-4b
```

### File Tool Scope

File tools are restricted to the application data directory derived from
`BIMEMO_DATABASE_PATH`. In the default Docker setup that means `/data/db`.
Do not point file tools at arbitrary host directories unless you intentionally
change the worker wiring and understand the security impact.

### Rate Limits

Default limits per minute:

| Tool | Requests/min |
|------|-------------|
| file_read | 10 |
| file_write | 5 |
| web_search | 3 |
| email_send | 1 |

---

## Keyboard Shortcuts (Web UI)

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message |
| `Escape` | Cancel generation |
| `Ctrl+K` | Search chats |

---

## Troubleshooting

### "Model not found" error

```bash
# If using Ollama, pull the model
ollama pull qwen3:14b

# Or switch to OpenRouter in .env
```

### Conversation getting too long

biMEMO automatically compresses context via Dialog Recorder. You can manually trigger reflection:

```
User: /reflect
```

### Clear conversation context

```
User: /newchat
```

### Check system status

```
User: /status
```

---

## Tips

1. **Be specific** — "Read file X" works better than "read my file"
2. **Use tools wisely** — GREEN tools are fast, RED need confirmation
3. **Memory works automatically** — No need to explicitly save
4. **All channels share context** — Web + Telegram + Discord share the same conversation and memory
