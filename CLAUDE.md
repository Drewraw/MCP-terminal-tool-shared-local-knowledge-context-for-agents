# IMPORTANT — PruneTool Knowledge Base

> This project uses PruneTool MCP. Call `describe_project` manually when you need full KB context.

## Session Protocol
1. Call `session_start` with your model ID and current timestamp at the start of every session.
2. Call `describe_project` when you need project context (folder map, prune library, etc.).
3. Call `report_tokens` after every response (input_tokens, output_tokens, model). Do NOT pass user_message.
4. On `/save docs`: scroll back through the conversation, merge into `prune library/library.md` and `prune library/PROGRESS.md`.
5. At 35K tokens: warn the user to type `/save docs`.
