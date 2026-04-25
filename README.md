# codebear-gateway · migration notes (for AI)

Anthropic-protocol gateway. Routes `/api/v1/messages` → LiteLLM → Emergent proxy.
Stack: FastAPI (backend:8001) + React CRA (frontend:3000). Ingress: `/api/*`→8001, rest→3000.

## files to copy (only these matter)
```
backend/server.py
backend/anthropic_gateway.py
backend/requirements.txt
backend/.env
frontend/src/App.js
frontend/src/App.css
frontend/.env
```

## env to set on new account

`backend/.env`
```
MONGO_URL="mongodb://localhost:27017"
DB_NAME="test_database"
CORS_ORIGINS="*"
EMERGENT_LLM_KEY=<new account universal key, format sk-emergent-xxx>
GATEWAY_API_KEY=<chosen client key, e.g. codebear>
```

`frontend/.env` — only this var matters, replace with new pod's preview URL:
```
REACT_APP_BACKEND_URL=https://<new-pod-subdomain>.preview.emergentagent.com
```

## install
```bash
# backend
pip install -r /app/backend/requirements.txt
pip install litellm
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

# frontend
cd /app/frontend && yarn install

# restart
sudo supervisorctl restart backend frontend
```

## verify
```bash
BASE=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
curl -s "$BASE/api/gateway/info"
curl -s -X POST "$BASE/api/v1/messages" \
  -H "x-api-key: $(grep GATEWAY_API_KEY /app/backend/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":40,"messages":[{"role":"user","content":"ping"}]}'
```

## client usage (Claude Code)
```bash
export ANTHROPIC_BASE_URL="$BASE/api"
export ANTHROPIC_API_KEY=<GATEWAY_API_KEY>
claude
```

## notes
- emergentintegrations + EMERGENT_LLM_KEY is account-bound: new account = new key. Get from Profile → Universal Key.
- Gemini models need `gemini/` prefix when sent to litellm — already handled in `anthropic_gateway.get_litellm_model`.
- No DB migration needed; gateway is stateless. MongoDB only stores legacy `/api/status` records (unused by gateway).
- If `Budget exceeded` upstream error: top up universal key balance.
