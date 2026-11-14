"""Prompt playground: chat with an Ollama model that can call the genericCorpusMcpBuilder tools.

    .venv/bin/python playground/chat.py                       # interactive, gpt-oss:20b, toy server, prompts/default.md
    .venv/bin/python playground/chat.py --mcp corpus          # the real genericCorpusMcpBuilder corpus (in-process)
    .venv/bin/python playground/chat.py --prompt playground/prompts/minimal.md --model qwen3:14b
    .venv/bin/python playground/chat.py --ask "Where does the obligation of shnayim mikra come from?" \
        --prompt playground/prompts/default.md --prompt playground/prompts/minimal.md   # batch A/B

The system prompt file is re-read before every model call, so edit it in your editor and /retry.
Every turn is appended to playground/logs/<session>.jsonl for later comparison.
"""
import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx2
from mcp import Client

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIM, BOLD, CYAN, YELLOW, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[31m", "\033[0m"

HELP = f"""{BOLD}commands{RESET}
  /retry               re-ask the last question with the current prompt file + settings
  /reset               clear the conversation
  /prompt <file>       switch system prompt file
  /set <key> <value>   ctx 32768 | temp 0.7 | temp default | think low|medium|high|on|off|default
                       tools on|off | instructions on|off | maxchars 8000 | rounds 8 | verbose on|off | model <name>
  /show                settings + the system prompt as sent
  /tools               list MCP tools
  /quit"""


@dataclass
class Settings:
    model: str = "gpt-oss:20b"
    ollama: str = "http://127.0.0.1:11434"
    prompt: str = str(HERE / "prompts" / "default.md")
    ctx: int = 32768
    temp: float | None = None          # None = the model's own default
    think: str | bool | None = None    # gpt-oss: low/medium/high; qwen3: True/False; None = model default
    tools: bool = True
    instructions: bool = True          # append the MCP server's own instructions, as real hosts do
    maxchars: int = 8000               # truncate each tool result to this many characters
    rounds: int = 8                    # max model calls per question
    verbose: bool = False              # print reasoning and full tool results


def system_prompt(s: Settings, server_instructions: str | None) -> str:
    text = Path(s.prompt).read_text()
    if s.instructions and server_instructions:
        text += "\n\n# Tool server notes\n" + server_instructions
    return text


def tool_schemas(tools) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.input_schema}} for t in tools]


async def call_model(http: httpx2.AsyncClient, s: Settings, messages: list[dict], tools: list[dict]) -> tuple[dict, dict]:
    options = {"num_ctx": s.ctx}
    if s.temp is not None:
        options["temperature"] = s.temp
    body = {"model": s.model, "messages": messages, "stream": True, "options": options}
    if tools:
        body["tools"] = tools
    if s.think is not None:
        body["think"] = s.think

    content, thinking, calls, final = "", "", [], {}
    async with http.stream("POST", f"{s.ollama}/api/chat", json=body) as r:
        if r.status_code != 200:
            raise RuntimeError(f"ollama {r.status_code}: {(await r.aread()).decode()}")
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            m = chunk.get("message", {})
            if m.get("thinking"):
                if not thinking:
                    print(f"{DIM}[thinking]{' ' if s.verbose else '…'}", end="", flush=True)
                thinking += m["thinking"]
                if s.verbose:
                    print(m["thinking"], end="", flush=True)
            if m.get("content"):
                if not content:
                    print(f"{RESET}\n" if thinking else "", end="")
                content += m["content"]
                print(m["content"], end="", flush=True)
            calls += m.get("tool_calls") or []
            if chunk.get("done"):
                final = chunk
    print(RESET)

    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
        if thinking:
            message["thinking"] = thinking  # gpt-oss expects its reasoning back during a tool-call chain
    return message, final


async def call_tool(mcp: Client, s: Settings, call: dict) -> dict:
    name = call["function"]["name"]
    args = call["function"].get("arguments") or {}
    if isinstance(args, str):
        args = json.loads(args or "{}")
    print(f"{CYAN}→ {name}({json.dumps(args, ensure_ascii=False)}){RESET}")
    if mcp is None:
        return {"role": "tool", "tool_name": name, "content": "ERROR: no tools are available in this session."}
    try:
        result = await mcp.call_tool(name, args)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
        if getattr(result, "is_error", False):
            text = "ERROR: " + text
            if not s.verbose:
                print(f"{DIM}  (/set verbose on to see the server's traceback){RESET}")
    except Exception as e:  # surface tool failures to the model rather than crashing the session
        text = f"ERROR: {type(e).__name__}: {e}"
    full = len(text)
    if full > s.maxchars:
        text = text[:s.maxchars] + f"\n…[truncated: {full - s.maxchars} more characters]"
    preview = text if s.verbose else text[:300].replace("\n", " ") + ("…" if len(text) > 300 else "")
    print(f"{DIM}  ← {full} chars: {preview}{RESET}")
    return {"role": "tool", "tool_name": name, "content": text}


async def ask(http, mcp, s: Settings, server_instructions, tools, history: list[dict], question: str, log) -> list[dict]:
    """Run one question to completion; returns the new history."""
    turn = [{"role": "user", "content": question}]
    schemas = tool_schemas(tools) if s.tools else []
    usage = []
    started = time.time()
    for _ in range(s.rounds):
        messages = [{"role": "system", "content": system_prompt(s, server_instructions)}, *history, *turn]
        message, final = await call_model(http, s, messages, schemas)
        turn.append(message)
        prompt_tokens = final.get("prompt_eval_count", 0)
        usage.append({"prompt_tokens": prompt_tokens, "output_tokens": final.get("eval_count", 0)})
        warn = f" {RED}(near num_ctx: older context may be silently cut){RESET}{DIM}" if prompt_tokens > 0.9 * s.ctx else ""
        print(f"{DIM}[{prompt_tokens} prompt tok / num_ctx {s.ctx}, {final.get('eval_count', 0)} out]{warn}{RESET}")
        if not message.get("tool_calls"):
            break
        for call in message["tool_calls"]:
            turn.append(await call_tool(mcp, s, call))
    else:
        print(f"{YELLOW}stopped after {s.rounds} model calls (/set rounds N to allow more){RESET}")
    log({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - started, 1),
         "settings": asdict(s), "system_prompt": system_prompt(s, server_instructions),
         "question": question, "turn": turn, "usage": usage})
    return [*history, *turn]


def parse_value(key: str, value: str):
    if key in ("ctx", "maxchars", "rounds"):
        return int(value)
    if key == "temp":
        return None if value == "default" else float(value)
    if key == "think":
        return {"on": True, "off": False, "default": None}.get(value, value)
    if key in ("tools", "instructions", "verbose"):
        return value in ("on", "true", "1")
    return value


def set_server_logging(verbose: bool) -> None:
    """The in-process server and HTTP client log through `logging`; keep that out of the chat unless verbose."""
    level = logging.INFO if verbose else logging.CRITICAL
    for name in ("", "mcp", "httpx2", "httpcore", "genericCorpusMcpBuilder", "sentence_transformers"):
        logging.getLogger(name).setLevel(level)


def connect_target(spec: str):
    if spec.startswith("http"):
        return spec
    if spec not in ("toy", "corpus", "inproc"):
        raise SystemExit(f"--mcp must be toy, corpus, none or a URL (got {spec!r})")
    if spec == "toy":
        sys.path.insert(0, str(HERE))
        from toy_server import server
        return server
    os.environ.setdefault("CORPUS_MCP_DATA", str(ROOT / "data"))
    sys.path.insert(0, str(ROOT))
    from genericCorpusMcpBuilder.server import server  # loads the corpus in this process
    return server


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=Settings.model)
    p.add_argument("--ollama", default=Settings.ollama)
    p.add_argument("--prompt", action="append", help="system prompt file (repeat with --ask to A/B several)")
    p.add_argument("--ctx", type=int, default=Settings.ctx)
    p.add_argument("--think", help="gpt-oss: low|medium|high, qwen3: on|off")
    p.add_argument("--mcp", default="toy", help="'toy' (default: 3-verse demo server), 'corpus' (real genericCorpusMcpBuilder, in-process), a URL like http://127.0.0.1:8765/mcp, or 'none' for plain chat")
    p.add_argument("--ask", action="append", help="batch mode: run this question (repeatable) and exit")
    args = p.parse_args()

    prompts = args.prompt or [Settings.prompt]
    s = Settings(model=args.model, ollama=args.ollama.rstrip("/"), prompt=prompts[0], ctx=args.ctx,
                 think=parse_value("think", args.think) if args.think else None)

    (HERE / "logs").mkdir(exist_ok=True)
    log_path = HERE / "logs" / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"

    def log(record):
        with log_path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    no_mcp = args.mcp == "none"
    if no_mcp:
        s.tools = False
    session = contextlib.nullcontext() if no_mcp else Client(connect_target(args.mcp))
    async with session as mcp, httpx2.AsyncClient(timeout=None) as http:
        set_server_logging(s.verbose)
        tools = [] if no_mcp else (await mcp.list_tools()).tools
        server_instructions = None if no_mcp else mcp.instructions
        print(f"{DIM}{'no MCP (plain chat)' if no_mcp else f'{len(tools)} tools from {args.mcp}'} · model {s.model} · log {log_path.relative_to(ROOT)}{RESET}")

        if args.ask:
            for prompt in prompts:
                s.prompt = prompt
                for question in args.ask:
                    print(f"\n{BOLD}=== {Path(prompt).name} :: {question}{RESET}")
                    await ask(http, mcp, s, server_instructions, tools, [], question, log)
            return

        try:
            import readline  # noqa: F401  (arrow keys + history in input())
        except ImportError:
            pass
        print(f"{DIM}prompt: {s.prompt}  ·  /help for commands{RESET}")
        history, last_question, before_last = [], None, []
        while True:
            try:
                line = (await asyncio.to_thread(input, f"{BOLD}> {RESET}")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.startswith("/"):
                cmd, *rest = line[1:].split(maxsplit=2)
                if cmd in ("quit", "exit", "q"):
                    return
                elif cmd == "help":
                    print(HELP)
                elif cmd == "reset":
                    history, last_question = [], None
                    print(f"{DIM}conversation cleared{RESET}")
                elif cmd == "prompt" and rest:
                    if not Path(rest[0]).exists():
                        print(f"{RED}no such file: {rest[0]}{RESET}")
                    else:
                        s.prompt = rest[0]
                        print(f"{DIM}prompt: {s.prompt}{RESET}")
                elif cmd == "set" and len(rest) == 2 and rest[0] in Settings.__dataclass_fields__:
                    try:
                        setattr(s, rest[0], parse_value(rest[0], rest[1]))
                        set_server_logging(s.verbose)
                        print(f"{DIM}{rest[0]} = {getattr(s, rest[0])!r}{RESET}")
                    except ValueError as e:
                        print(f"{RED}{e}{RESET}")
                elif cmd == "show":
                    print(json.dumps(asdict(s), indent=2))
                    print(f"{DIM}--- system prompt as sent ---\n{system_prompt(s, server_instructions)}{RESET}")
                elif cmd == "tools":
                    for t in tools:
                        print(f"{CYAN}{t.name}{RESET} {DIM}{(t.description or '').splitlines()[0] if t.description else ''}{RESET}")
                elif cmd == "retry":
                    if last_question is None:
                        print(f"{DIM}nothing to retry{RESET}")
                    else:
                        history = await safe_ask(http, mcp, s, server_instructions, tools, before_last, last_question, log)
                else:
                    print(HELP)
                continue
            before_last, last_question = history, line
            history = await safe_ask(http, mcp, s, server_instructions, tools, history, line, log)


async def safe_ask(http, mcp, s, server_instructions, tools, history, question, log):
    try:
        return await ask(http, mcp, s, server_instructions, tools, history, question, log)
    except (RuntimeError, httpx2.HTTPError) as e:
        print(f"{RED}{e}{RESET}")
    except KeyboardInterrupt:
        print(f"{YELLOW}interrupted{RESET}")
    return history


if __name__ == "__main__":
    asyncio.run(main())
