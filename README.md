# Conduit Server

Python backend for the Conduit AI assistant. Multi-provider LLM routing, tool execution, memory, and agent orchestration.

## Structure

```
server/     # FastAPI backend — agents, tools, memory, providers
web/        # Web chat frontend
docs/       # Design docs and plans
scripts/    # Utility scripts
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp server/.env.example server/.env  # add API keys
python -m server
```

## Tablet Deployment

The tablet runs the same codebase with a config overlay. See `conduit-tablet/` for tablet-specific config.

## Features

- Multi-provider LLM routing (OpenAI-compat, Anthropic, Gemini, ChatGPT, Claude Code)
- Agent system with binding-based routing
- Tool framework (filesystem, web, email, PDF, execute)
- Hybrid memory (Firestore semantic + SQLite BM25)
- Telegram bot integration
- File watcher with auto-sort
- SearXNG web search
