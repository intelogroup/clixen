#!/bin/bash
# Claude Code with MCP tools via ollama-mcp-bridge
# Usage: ./run-claude-mcp.sh [args...]

export ANTHROPIC_AUTH_TOKEN=ollama
export ANTHROPIC_API_KEY=""
export ANTHROPIC_BASE_URL=http://localhost:8000

# Optional: override default model
# export ANTHROPIC_MODEL=qwen3:8b

exec /Users/kalinovdameus/.local/bin/claude "$@"