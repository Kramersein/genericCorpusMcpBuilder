# Prompt playground

A terminal chat with a local Ollama model that can call the genericCorpusMcpBuilder tools. Made for quick testing of
system prompts, models and context settings.

```sh
.venv/bin/python playground/chat.py                          # gpt-oss:20b + toy server + prompts/default.md
.venv/bin/python playground/chat.py --model qwen3:14b --think off
.venv/bin/python playground/chat.py --mcp http://127.0.0.1:8765/mcp   # use the running service instead
.venv/bin/python playground/chat.py --mcp corpus                       # real genericCorpusMcpBuilder corpus, in-process
.venv/bin/python playground/chat.py --mcp none                         # plain chat, no tools
```

MCP servers run **inside** the script, so nothing else needs to be started except Ollama. The default is
`toy_server.py`: Genesis 1:1, Leviticus 19:18 and Deuteronomy 6:4, with `list_refs`, `get_text` and `search`.
Use it to test the chat loop and prompts while the real server is in flux. `--mcp corpus` loads the real one
from `./data`. A good first test: ask about a verse that isn't in the toy (e.g. Exodus 20:2) and see whether
the model admits it or quotes from memory.

## Loop for tinkering
1. Open `playground/prompts/default.md` (or a copy) in your editor.
2. Ask a question.
3. Edit the prompt, save, type `/retry`. The prompt file is re-read on every call.
4. `/set think high`, `/set ctx 16384`, `/set tools off`, `/set instructions off` to see what each changes.
   `/show` prints the exact system prompt sent.

Each answer ends with `[N prompt tok / num_ctx M, K out]`. Watch N. When it nears M, Ollama silently drops
the start of the conversation (your system prompt first). Raise `ctx`, or lower `maxchars` (the per-tool-result cap).

## A/B several prompts at once
```sh
.venv/bin/python playground/chat.py \
  --prompt playground/prompts/default.md --prompt playground/prompts/minimal.md \
  --ask "Where does the obligation of shnayim mikra ve'echad targum come from?" \
  --ask "What does Berakhot 2a say about when to say the evening Shema?"
```

Every turn (settings, exact system prompt, every tool call and result, token usage) goes to
`playground/logs/<session>.jsonl`.

## Notes
- This uses Ollama's native `/api/chat` rather than `/v1`, because only the native API accepts `num_ctx`,
  `temperature` and `think` per request.
- gpt-oss takes `think` = `low|medium|high`. qwen3 takes `on|off`.
- `instructions on` (the default) appends the MCP server's own instructions to your prompt, the way Claude
  and other MCP hosts do. Turn it off to test your prompt alone.
