"""
Anthropic (Claude) provider adapter.

This module is the ONLY place in the harness that should know anything about
Anthropic's SDK shapes (message/content-block objects, tool_use blocks,
etc.) — see gemini.py's module docstring for the general provider-isolation
rationale; the same discipline applies here.

Two ways to authenticate, checked in this order:

1. OAuth (preferred, no API key needed). If the user has already run
   `claude setup-token` (a one-time interactive step tied to their existing
   Claude subscription — see https://docs.claude.com/en/docs/claude-code),
   a long-lived bearer token is available via the `CLAUDE_CODE_OAUTH_TOKEN`
   env var or `~/.config/anthropic/credentials/<profile>.json`. This adapter
   passes it as `auth_token=` (never `api_key=`) plus the
   `anthropic-beta: oauth-2025-04-20` header the API requires to accept a
   bearer token instead of an API key. Verified against the installed
   `anthropic` Python SDK's own credential-resolution chain
   (`anthropic.lib.credentials`) before writing this -- do not "simplify"
   this to `api_key=` believing that also accepts a bearer token; it does
   not, and the API will reject it.
2. `ANTHROPIC_API_KEY` (fallback, if no OAuth token is present). Standard
   `api_key=` auth, same as any other Anthropic SDK usage.

Normalized message format, tool-calling registry format, and the
`ModelResponse` return shape are identical to gemini.py's — see that
module's docstring; this file only translates them into Anthropic's
`messages.create(...)` request/response shapes instead of Gemini's.
"""

from dataclasses import dataclass, field
import os

import anthropic


DEFAULT_MODEL = "claude-sonnet-4-5"

# The API requires this beta header on any request authenticated with a
# bearer token (auth_token=) obtained via OAuth, instead of a raw API key.
_OAUTH_BETA_HEADER = "oauth-2025-04-20"


@dataclass
class ModelResponse:
    """Normalized result of a single call to the model. Same shape as
    gemini.ModelResponse -- see that module for field semantics."""
    text: str | None
    tool_calls: list = field(default_factory=list)
    stop_reason: str | None = None


def _get_oauth_token() -> str | None:
    """Return a Claude Code OAuth bearer token if one is available, else
    None. Checked before ANTHROPIC_API_KEY so an already-authenticated
    Claude Code session (or a user who ran `claude setup-token`) is used
    automatically, without ever prompting for an API key."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN"
    )
    return token or None


def _get_client() -> anthropic.Anthropic:
    oauth_token = _get_oauth_token()
    if oauth_token:
        return anthropic.Anthropic(
            auth_token=oauth_token,
            default_headers={"anthropic-beta": _OAUTH_BETA_HEADER},
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    raise RuntimeError(
        "No Anthropic credentials found. Either run `claude setup-token` "
        "once (requires a Claude subscription; this is the preferred, "
        "no-API-key path -- see harness/providers/anthropic_provider.py "
        "docstring) and make sure CLAUDE_CODE_OAUTH_TOKEN is exported, or "
        "set ANTHROPIC_API_KEY in .env as a fallback."
    )


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Translate our normalized message list into Anthropic's message
    param shape. Anthropic has no separate "tool" role -- tool results are
    `tool_result` content blocks inside a `user` turn, and tool calls are
    `tool_use` content blocks inside an `assistant` turn.

    Anthropic's API requires ALL `tool_result` blocks answering one
    assistant turn's `tool_use` blocks to live in a SINGLE following user
    message, not one user message per result -- our loop.py appends one
    normalized {"role": "tool", ...} message per call, in turn-call order,
    so consecutive "tool" messages here are merged into one user message
    with one tool_result block each, in the same order. Splitting them
    into separate user messages (an earlier version of this function did
    that) is accepted by the SDK client-side but rejected by the API for
    any turn with more than one tool call.
    """
    result = []
    for msg in messages:
        role = msg["role"]

        if role == "user":
            result.append({"role": "user", "content": msg["content"]})

        elif role == "assistant":
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls", []):
                call_id = call.get("id")
                if not call_id:
                    raise ValueError(
                        f"Tool call for {call['name']!r} is missing an 'id' "
                        f"-- Anthropic requires a stable tool_use id echoed "
                        f"back in the paired tool_result on the next turn. "
                        f"This should never happen for a call this adapter "
                        f"itself produced (see call_model's response "
                        f"parsing); if you're constructing tool_calls by "
                        f"hand (e.g. in a test), include an 'id'."
                    )
                content.append(
                    {
                        "type": "tool_use",
                        # Anthropic requires a stable id per tool_use block,
                        # echoed back in the paired tool_result. This is the
                        # real id Anthropic itself generated for this call
                        # (see call_model's response parsing below) -- not
                        # synthesized here, since a synthesized id would not
                        # match what the API expects on the next turn.
                        "id": call_id,
                        "name": call["name"],
                        "input": call.get("args", {}),
                    }
                )
            result.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                raise ValueError(
                    f"Tool result for {msg['name']!r} is missing "
                    f"'tool_call_id' -- Anthropic requires the real "
                    f"tool_use id to be echoed back, not the tool name. "
                    f"This should never happen when messages come from "
                    f"harness.loop.run (which now carries the id through "
                    f"from the original tool call -- see loop.py's tool "
                    f"execution loop); if you're constructing messages "
                    f"by hand, include 'tool_call_id'."
                )
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": str(msg["content"]),
            }
            # Merge into the previous user message if loop.py just emitted
            # one for a prior tool result in this same turn (i.e. the
            # previous entry in `result` is itself a merged tool_result
            # user message, not an ordinary user-authored turn) -- see
            # this function's docstring for why merging is required.
            if (
                result
                and result[-1]["role"] == "user"
                and isinstance(result[-1]["content"], list)
                and result[-1]["content"]
                and result[-1]["content"][0].get("type") == "tool_result"
            ):
                result[-1]["content"].append(tool_result_block)
            else:
                result.append({"role": "user", "content": [tool_result_block]})

        else:
            raise ValueError(f"Unsupported message role: {msg['role']!r}")

    return result


def _to_anthropic_tools(tools: dict | None) -> list[dict] | None:
    """Translate our {name: (callable, schema)} registry into Anthropic's
    tool param shape (`input_schema` instead of `parameters`)."""
    if not tools:
        return None
    declarations = []
    for _name, (_func, schema) in tools.items():
        declarations.append(
            {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "input_schema": schema.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return declarations


def call_model(
    messages: list[dict],
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    tools: dict | None = None,
    max_tokens: int = 4096,
) -> ModelResponse:
    """Send a conversation to Claude and return a normalized response.

    Args mirror gemini.call_model's signature exactly (minus max_tokens,
    which Anthropic's API requires and Gemini's does not) so the two
    adapters are interchangeable behind the same call_model(...) interface.
    """
    client = _get_client()
    anthropic_messages = _to_anthropic_messages(messages)
    anthropic_tools = _to_anthropic_tools(tools)

    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=anthropic_messages,
    )
    if system_prompt:
        kwargs["system"] = system_prompt
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools

    response = client.messages.create(**kwargs)

    text_parts = []
    tool_calls = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                {
                    "name": block.name,
                    "args": dict(block.input or {}),
                    "id": block.id,
                }
            )

    return ModelResponse(
        text="\n".join(text_parts) if text_parts and not tool_calls else (
            "\n".join(text_parts) if text_parts else None
        ),
        tool_calls=tool_calls,
        stop_reason=response.stop_reason,
    )
