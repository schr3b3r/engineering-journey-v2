"""
OpenAI provider adapter.

Unlike anthropic_provider.py and gemini.py, this adapter is API-key-only.
There is no OAuth/subscription-based path here: `codex login`'s ChatGPT
subscription OAuth token is scoped to Codex's own backend and CLI tool
loop, not usable as a bearer credential against the public OpenAI Chat/
Responses API that this adapter's tool-calling loop depends on. Do not
add a fake "OAuth" branch here to make this adapter look consistent with
the other two -- if OpenAI ever ships a real subscription-OAuth path for
the public API, add it then and update this docstring, but don't invent
one now.

Everything else mirrors gemini.py / anthropic_provider.py: normalized
message format, tool registry format, and `ModelResponse` return shape
are identical -- see gemini.py's module docstring for the full rationale.
"""

from dataclasses import dataclass, field
import os

import openai


DEFAULT_MODEL = "gpt-5"


@dataclass
class ModelResponse:
    """Normalized result of a single call to the model. Same shape as
    gemini.ModelResponse -- see that module for field semantics."""
    text: str | None
    tool_calls: list = field(default_factory=list)
    stop_reason: str | None = None


def _get_client() -> openai.OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. This adapter is API-key-only -- "
            "there is no OAuth/subscription path for the public OpenAI "
            "API (see module docstring for why). Set OPENAI_API_KEY in "
            ".env before calling the harness."
        )
    return openai.OpenAI(api_key=api_key)


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Translate our normalized message list into OpenAI Chat Completions
    message param shape. OpenAI represents tool calls as `tool_calls` on
    an assistant message (like our own normalized shape already does,
    almost verbatim) and tool results as a dedicated `tool` role message
    carrying `tool_call_id` -- so this translation is the thinnest of the
    three adapters."""
    result = []
    for msg in messages:
        role = msg["role"]

        if role == "user":
            result.append({"role": "user", "content": msg["content"]})

        elif role == "assistant":
            out = {"role": "assistant", "content": msg.get("content")}
            calls = msg.get("tool_calls", [])
            if calls:
                for call in calls:
                    if not call.get("id"):
                        raise ValueError(
                            f"Tool call for {call['name']!r} is missing an "
                            f"'id' -- OpenAI requires a stable tool_call id "
                            f"echoed back in the paired tool result message "
                            f"on the next turn. This should never happen "
                            f"for a call this adapter itself produced (see "
                            f"call_model's response parsing); if you're "
                            f"constructing tool_calls by hand (e.g. in a "
                            f"test), include an 'id'."
                        )
                out["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": _json_dumps(call.get("args", {})),
                        },
                    }
                    for call in calls
                ]
            result.append(out)

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                raise ValueError(
                    f"Tool result for {msg['name']!r} is missing "
                    f"'tool_call_id' -- OpenAI requires the real tool_call "
                    f"id to be echoed back, not the tool name. This should "
                    f"never happen when messages come from harness.loop.run "
                    f"(which now carries the id through from the original "
                    f"tool call); if you're constructing messages by hand, "
                    f"include 'tool_call_id'."
                )
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(msg["content"]),
                }
            )

        else:
            raise ValueError(f"Unsupported message role: {msg['role']!r}")

    return result


def _json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


def _to_openai_tools(tools: dict | None) -> list[dict] | None:
    """Translate our {name: (callable, schema)} registry into OpenAI's
    tool param shape (function-wrapped, like Chat Completions expects)."""
    if not tools:
        return None
    declarations = []
    for _name, (_func, schema) in tools.items():
        declarations.append(
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": schema.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return declarations


def call_model(
    messages: list[dict],
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    tools: dict | None = None,
) -> ModelResponse:
    """Send a conversation to OpenAI and return a normalized response.
    Signature mirrors gemini.call_model / anthropic_provider.call_model."""
    client = _get_client()
    openai_messages = []
    if system_prompt:
        openai_messages.append({"role": "system", "content": system_prompt})
    openai_messages.extend(_to_openai_messages(messages))
    openai_tools = _to_openai_tools(tools)

    kwargs = dict(model=model, messages=openai_messages)
    if openai_tools:
        kwargs["tools"] = openai_tools

    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    msg = choice.message

    tool_calls = []
    for call in msg.tool_calls or []:
        import json

        tool_calls.append(
            {
                "name": call.function.name,
                "args": json.loads(call.function.arguments or "{}"),
                "id": call.id,
            }
        )

    return ModelResponse(
        text=msg.content if not tool_calls else None,
        tool_calls=tool_calls,
        stop_reason=choice.finish_reason,
    )
