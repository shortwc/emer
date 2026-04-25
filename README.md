# codebear-gateway

After `git clone`, do:

```bash
# 1. install
pip install -r backend/requirements.txt
pip install litellm
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/
cd frontend && yarn install && cd ..

# 2. set EMERGENT_LLM_KEY in backend/.env (get from Profile → Universal Key)
#    REACT_APP_BACKEND_URL in frontend/.env (your pod's preview URL)

# 3. start
sudo supervisorctl restart backend frontend
```

Client:
```bash
export ANTHROPIC_BASE_URL="<your_pod_url>/api"
export ANTHROPIC_API_KEY="codebear"
claude
```
