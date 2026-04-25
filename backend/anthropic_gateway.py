"""
Anthropic Messages API gateway.
Translates Anthropic protocol <-> OpenAI protocol, calls Emergent LiteLLM proxy.
Supports streaming, tool use, multimodal (images).
"""
import json
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import litellm

EMERGENT_PROXY_URL = "https://integrations.emergentagent.com/llm"


# ---------- Model list (exposed in /api/gateway/info) ----------
SUPPORTED_MODELS = {
    "openai": [
        "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano",
        "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-4", "o1", "o3", "o3-pro", "o4-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-6", "claude-opus-4-6",
        "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20251101",
        "claude-4-sonnet-20250514", "claude-4-opus-20250514",
    ],
    "gemini": [
        "gemini-3.1-pro-preview", "gemini-3-flash-preview",
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
    ],
}


def _all_model_names() -> List[str]:
    return [m for ms in SUPPORTED_MODELS.values() for m in ms]


def get_litellm_model(model: str) -> str:
    """Map incoming Anthropic-style model name to LiteLLM model format for the Emergent proxy."""
    if model.startswith("gemini"):
        return f"gemini/{model}"
    # OpenAI + Anthropic models pass through as-is when going through emergent proxy
    return model


# ---------- Anthropic -> OpenAI request conversion ----------
def _convert_user_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "image":
        src = block.get("source", {})
        if src.get("type") == "base64":
            url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
        else:
            url = src.get("url", "")
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def anthropic_to_openai_messages(system: Any, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # system
    if system:
        if isinstance(system, list):
            sys_text = "\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
        else:
            sys_text = str(system)
        if sys_text:
            out.append({"role": "system", "content": sys_text})

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        # plain string content (Anthropic allows this)
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            continue

        if role == "user":
            # Need to split out tool_result blocks to separate role=tool messages.
            buffer: List[Dict[str, Any]] = []

            def flush_user():
                if not buffer:
                    return
                if len(buffer) == 1 and buffer[0]["type"] == "text":
                    out.append({"role": "user", "content": buffer[0]["text"]})
                else:
                    out.append({"role": "user", "content": list(buffer)})
                buffer.clear()

            for block in content:
                btype = block.get("type")
                if btype == "tool_result":
                    flush_user()
                    tool_content = block.get("content", "")
                    if isinstance(tool_content, list):
                        parts = [b.get("text", "") for b in tool_content if isinstance(b, dict) and b.get("type") == "text"]
                        tool_text = "\n".join(parts)
                    else:
                        tool_text = str(tool_content) if tool_content is not None else ""
                    out.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": tool_text,
                    })
                else:
                    converted = _convert_user_block(block)
                    if converted:
                        buffer.append(converted)
            flush_user()

        elif role == "assistant":
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
            msg_out: Dict[str, Any] = {"role": "assistant"}
            msg_out["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            out.append(msg_out)

    return out


def anthropic_tools_to_openai(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if not tools:
        return None
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def anthropic_tool_choice_to_openai(tc: Any) -> Any:
    if tc is None:
        return None
    if isinstance(tc, dict):
        ttype = tc.get("type")
        if ttype == "auto":
            return "auto"
        if ttype == "any":
            return "required"
        if ttype == "tool":
            return {"type": "function", "function": {"name": tc.get("name", "")}}
    return None


def map_finish_reason(reason: Optional[str]) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "stop_sequence",
    }.get(reason or "", "end_turn")


# ---------- OpenAI -> Anthropic response conversion ----------
def openai_response_to_anthropic(model: str, resp: Dict[str, Any]) -> Dict[str, Any]:
    choice = resp["choices"][0]
    msg = choice.get("message", {}) or {}
    content_blocks: List[Dict[str, Any]] = []

    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})

    for tc in (msg.get("tool_calls") or []):
        try:
            tool_input = json.loads(tc["function"].get("arguments") or "{}")
        except Exception:
            tool_input = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
            "name": tc["function"].get("name", ""),
            "input": tool_input,
        })

    usage = resp.get("usage", {}) or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": map_finish_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        },
    }


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_anthropic_events(model: str, openai_stream) -> AsyncGenerator[str, None]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "content": [], "model": model,
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _sse("ping", {"type": "ping"})

    text_started = False
    text_index: Optional[int] = None
    tool_blocks: Dict[int, Dict[str, Any]] = {}
    next_index = 0
    finish_reason: Optional[str] = None
    usage = {"input_tokens": 0, "output_tokens": 0}

    async for chunk in openai_stream:
        if hasattr(chunk, "model_dump"):
            chunk = chunk.model_dump()
        if not isinstance(chunk, dict):
            continue

        if chunk.get("usage"):
            u = chunk["usage"]
            usage["input_tokens"] = u.get("prompt_tokens", 0) or 0
            usage["output_tokens"] = u.get("completion_tokens", 0) or 0

        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        # plain text delta
        if delta.get("content"):
            if not text_started:
                text_index = next_index
                next_index += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": text_index,
                    "content_block": {"type": "text", "text": ""},
                })
                text_started = True
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": delta["content"]},
            })

        # tool calls delta
        for tc in (delta.get("tool_calls") or []):
            tc_idx = tc.get("index", 0)
            if tc_idx not in tool_blocks:
                # close text block if any
                if text_started and text_index is not None:
                    yield _sse("content_block_stop", {
                        "type": "content_block_stop", "index": text_index,
                    })
                    text_started = False
                block_index = next_index
                next_index += 1
                tool_blocks[tc_idx] = {
                    "block_index": block_index,
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": ((tc.get("function") or {}).get("name") or ""),
                    "args_buffer": "",
                    "started": False,
                }
            else:
                if tc.get("id") and not tool_blocks[tc_idx].get("id"):
                    tool_blocks[tc_idx]["id"] = tc["id"]
                fname = (tc.get("function") or {}).get("name")
                if fname and not tool_blocks[tc_idx]["name"]:
                    tool_blocks[tc_idx]["name"] = fname

            tb = tool_blocks[tc_idx]
            if not tb["started"] and tb["name"]:
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": tb["block_index"],
                    "content_block": {
                        "type": "tool_use",
                        "id": tb["id"],
                        "name": tb["name"],
                        "input": {},
                    },
                })
                tb["started"] = True

            args_delta = (tc.get("function") or {}).get("arguments")
            if args_delta and tb["started"]:
                tb["args_buffer"] += args_delta
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": tb["block_index"],
                    "delta": {"type": "input_json_delta", "partial_json": args_delta},
                })

        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    # close any open blocks
    if text_started and text_index is not None:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_index})
    for tb in tool_blocks.values():
        if tb.get("started"):
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": tb["block_index"]})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason": map_finish_reason(finish_reason),
            "stop_sequence": None,
        },
        "usage": usage,
    })
    yield _sse("message_stop", {"type": "message_stop"})


# ---------- LiteLLM call ----------
def _build_extra_headers() -> Dict[str, str]:
    headers = {}
    app_url = os.getenv("APP_URL") or os.getenv("REACT_APP_BACKEND_URL")
    if app_url:
        headers["X-App-ID"] = app_url
    return headers


async def call_emergent_llm(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    stop: Any = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Any = None,
    stream: bool = False,
):
    api_key = os.environ["EMERGENT_LLM_KEY"]
    params: Dict[str, Any] = {
        "model": get_litellm_model(model),
        "messages": messages,
        "api_key": api_key,
        "api_base": EMERGENT_PROXY_URL,
        "custom_llm_provider": "openai",
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if top_p is not None:
        params["top_p"] = top_p
    if stop:
        params["stop"] = stop
    if tools:
        params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
    if stream:
        params["stream_options"] = {"include_usage": True}
    extra_headers = _build_extra_headers()
    if extra_headers:
        params["extra_headers"] = extra_headers

    return await litellm.acompletion(**params)


def is_supported_model(model: str) -> bool:
    return model in _all_model_names()


def list_supported_models() -> Dict[str, List[str]]:
    return SUPPORTED_MODELS
