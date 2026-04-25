from fastapi import FastAPI, APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
import uuid
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from anthropic_gateway import (
    anthropic_to_openai_messages,
    anthropic_tools_to_openai,
    anthropic_tool_choice_to_openai,
    openai_response_to_anthropic,
    stream_anthropic_events,
    call_emergent_llm,
    is_supported_model,
    list_supported_models,
)

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

GATEWAY_API_KEY = "codebear"  # hardcoded by design — see README

app = FastAPI(title="CodeBear Anthropic Gateway")

api_router = APIRouter(prefix="/api")
gateway_router = APIRouter(prefix="/api/v1")


# ---------- Status check (legacy) ----------
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/")
async def root():
    return {"message": "CodeBear Anthropic Gateway"}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.model_dump())
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    rows = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for r in rows:
        if isinstance(r.get('timestamp'), str):
            r['timestamp'] = datetime.fromisoformat(r['timestamp'])
    return rows


# ---------- Gateway info (for public landing page) ----------
def _mask_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 4:
        return "*" * len(k)
    visible = max(2, min(4, len(k) // 3))
    return k[:visible] + "*" * (len(k) - visible * 2) + k[-visible:]


@api_router.get("/gateway/info")
async def gateway_info(request: Request):
    base = os.environ.get('REACT_APP_BACKEND_URL') or os.environ.get('APP_URL') or str(request.base_url).rstrip('/')
    base = base.rstrip('/')
    return {
        "gateway_base_url": f"{base}/api",
        "messages_endpoint": f"{base}/api/v1/messages",
        "masked_api_key": _mask_key(GATEWAY_API_KEY),
        "auth_header": "x-api-key",
        "supported_models": list_supported_models(),
    }


# ---------- Anthropic Messages API ----------
def _verify_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> None:
    """Accept either x-api-key or Bearer token. Both must equal GATEWAY_API_KEY."""
    candidates: List[str] = []
    if x_api_key:
        candidates.append(x_api_key.strip())
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        candidates.append(token)
    if not any(c == GATEWAY_API_KEY for c in candidates):
        raise HTTPException(
            status_code=401,
            detail={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        )


def _anthropic_error(status: int, err_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


@gateway_router.post("/messages")
async def anthropic_messages(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    _verify_api_key(x_api_key, authorization)

    try:
        body = await request.json()
    except Exception as e:
        return _anthropic_error(400, "invalid_request_error", f"invalid JSON: {e}")

    model = body.get("model")
    if not model:
        return _anthropic_error(400, "invalid_request_error", "missing 'model'")
    if not is_supported_model(model):
        # Still attempt — allow forward-compatibility for new models the proxy supports.
        logger.warning(f"Model not in supported list (will attempt anyway): {model}")

    messages = body.get("messages") or []
    if not messages:
        return _anthropic_error(400, "invalid_request_error", "missing 'messages'")

    system = body.get("system")
    max_tokens = int(body.get("max_tokens") or 4096)
    temperature = body.get("temperature")
    top_p = body.get("top_p")
    stop = body.get("stop_sequences")
    stream = bool(body.get("stream", False))
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")

    try:
        openai_messages = anthropic_to_openai_messages(system, messages)
        openai_tools = anthropic_tools_to_openai(tools)
        openai_tool_choice = anthropic_tool_choice_to_openai(tool_choice)
    except Exception as e:
        logger.exception("conversion error")
        return _anthropic_error(400, "invalid_request_error", f"request conversion failed: {e}")

    try:
        result = await call_emergent_llm(
            model=model,
            messages=openai_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            tools=openai_tools,
            tool_choice=openai_tool_choice,
            stream=stream,
        )
    except Exception as e:
        logger.exception("upstream error")
        return _anthropic_error(502, "api_error", f"upstream error: {e}")

    if stream:
        async def event_gen():
            try:
                async for chunk in stream_anthropic_events(model, result):
                    yield chunk
            except Exception as e:
                logger.exception("stream error")
                err = {
                    "type": "error",
                    "error": {"type": "api_error", "message": f"stream error: {e}"},
                }
                yield f"event: error\ndata: {__import__('json').dumps(err)}\n\n"
        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # non-stream
    if hasattr(result, "model_dump"):
        resp_dict = result.model_dump()
    elif isinstance(result, dict):
        resp_dict = result
    else:
        resp_dict = dict(result)
    return JSONResponse(content=openai_response_to_anthropic(model, resp_dict))


app.include_router(api_router)
app.include_router(gateway_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
