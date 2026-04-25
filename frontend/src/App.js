import { useEffect, useState, useCallback } from "react";
import "@/App.css";
import axios from "axios";
import { Copy, Check, Activity, AlertCircle, Loader2, Terminal, Sparkles, Code2 } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

function CopyButton({ value, label }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch (e) {
      console.error(e);
    }
  }, [value]);
  return (
    <button
      onClick={onCopy}
      data-testid={`copy-${label}-btn`}
      className="copy-btn"
      aria-label={`Copy ${label}`}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      <span>{copied ? "copied" : "copy"}</span>
    </button>
  );
}

function Field({ label, value, mono = true, testId }) {
  return (
    <div className="field" data-testid={testId}>
      <div className="field-label">{label}</div>
      <div className="field-row">
        <code className={`field-value ${mono ? "mono" : ""}`}>{value}</code>
        <CopyButton value={value} label={label} />
      </div>
    </div>
  );
}

function ProviderBlock({ name, models, accent }) {
  return (
    <div className="provider-block" data-testid={`provider-${name}`}>
      <div className="provider-head">
        <span className="provider-dot" style={{ background: accent }} />
        <span className="provider-name">{name}</span>
        <span className="provider-count">{models.length}</span>
      </div>
      <div className="model-grid">
        {models.map((m) => (
          <button
            key={m}
            className="model-chip"
            data-testid={`model-${m}`}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(m);
              } catch (e) { console.error(e); }
            }}
            title="click to copy"
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  );
}

function Tester({ messagesUrl, maskedKey }) {
  const [status, setStatus] = useState("idle"); // idle | running | ok | err
  const [out, setOut] = useState("");

  const run = async () => {
    setStatus("running");
    setOut("→ POST " + messagesUrl + "\n→ model: claude-sonnet-4-5-20250929\n");
    try {
      const r = await axios.get(`${BACKEND_URL}/api/gateway/selftest`);
      if (r.data.ok) {
        setStatus("ok");
        setOut((p) => p + `\n✓ ${r.data.model} responded in ${r.data.latency_ms}ms\n← "${r.data.text}"\n← input_tokens=${r.data.input_tokens} output_tokens=${r.data.output_tokens}`);
      } else {
        setStatus("err");
        setOut((p) => p + `\n✗ ${r.data.error}`);
      }
    } catch (e) {
      setStatus("err");
      setOut((p) => p + `\n✗ ${e.message}`);
    }
  };

  return (
    <div className="tester" data-testid="tester-section">
      <div className="tester-head">
        <Activity size={14} />
        <span>connectivity probe</span>
        <button
          onClick={run}
          disabled={status === "running"}
          className="run-btn"
          data-testid="run-test-btn"
        >
          {status === "running" ? <><Loader2 size={12} className="spin" /> running</> : "run test"}
        </button>
      </div>
      <pre className="tester-out" data-testid="tester-output">{out || "// click 'run test' to send a real request through the gateway"}</pre>
      {status === "ok" && <div className="tester-status ok"><Check size={12} /> gateway is alive</div>}
      {status === "err" && <div className="tester-status err"><AlertCircle size={12} /> failed — check upstream credit</div>}
    </div>
  );
}

function App() {
  const [info, setInfo] = useState(null);
  const [tab, setTab] = useState("env"); // env | json | curl

  useEffect(() => {
    axios.get(`${BACKEND_URL}/api/gateway/info`).then((r) => setInfo(r.data)).catch(console.error);
  }, []);

  if (!info) {
    return (
      <div className="loading-screen">
        <Loader2 className="spin" size={20} />
        <span>booting gateway…</span>
      </div>
    );
  }

  const envSnippet = `export ANTHROPIC_BASE_URL="${info.gateway_base_url}"
export ANTHROPIC_API_KEY="${info.masked_api_key}"
# then run:
claude`;

  const curlSnippet = `curl -X POST "${info.messages_endpoint}" \\
  -H "x-api-key: ${info.masked_api_key}" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "content-type: application/json" \\
  -d '{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 1024,
    "messages": [{"role":"user","content":"Hello"}]
  }'`;

  const jsonSnippet = `{
  "env": {
    "ANTHROPIC_BASE_URL": "${info.gateway_base_url}",
    "ANTHROPIC_API_KEY": "${info.masked_api_key}"
  }
}`;

  const snippet = tab === "env" ? envSnippet : tab === "json" ? jsonSnippet : curlSnippet;

  return (
    <div className="app-root" data-testid="gateway-landing">
      <div className="grid-bg" />
      <div className="noise" />

      <header className="hero">
        <div className="brand">
          <div className="brand-mark">
            <Terminal size={16} />
          </div>
          <div className="brand-text">
            <div className="brand-title">codebear<span className="brand-accent">/</span>gateway</div>
            <div className="brand-sub">anthropic-protocol bridge · all models routed</div>
          </div>
        </div>

        <div className="hero-status" data-testid="hero-status">
          <span className="pulse" /> online
        </div>
      </header>

      <main className="main">
        <section className="section">
          <div className="kicker">01 · endpoint</div>
          <h2 className="title">Drop this into <span className="hl">Claude Code</span> and start.</h2>

          <div className="card primary-card" data-testid="endpoint-card">
            <Field
              label="base_url"
              value={info.gateway_base_url}
              testId="base-url-field"
            />
            <Field
              label="messages_endpoint"
              value={info.messages_endpoint}
              testId="endpoint-field"
            />
            <Field
              label="api_key (masked)"
              value={info.masked_api_key}
              testId="key-field"
            />
            <div className="hint">
              <Sparkles size={12} /> the real key is held server-side. paste this <strong>masked</strong> token in the snippet — it&apos;s the literal key clients should send.
            </div>
          </div>
        </section>

        <section className="section">
          <div className="kicker">02 · plug into claude code</div>
          <div className="card snippet-card" data-testid="snippet-card">
            <div className="tabs" role="tablist">
              {[
                { k: "env", label: "shell env" },
                { k: "json", label: "settings.json" },
                { k: "curl", label: "raw curl" },
              ].map((t) => (
                <button
                  key={t.k}
                  className={`tab ${tab === t.k ? "active" : ""}`}
                  onClick={() => setTab(t.k)}
                  data-testid={`tab-${t.k}`}
                >
                  {t.label}
                </button>
              ))}
              <div className="tab-spacer" />
              <CopyButton value={snippet} label={`snippet-${tab}`} />
            </div>
            <pre className="snippet" data-testid="snippet-output">{snippet}</pre>
          </div>
        </section>

        <section className="section">
          <div className="kicker">03 · live probe</div>
          <Tester messagesUrl={info.messages_endpoint} maskedKey={info.masked_api_key} />
        </section>

        <section className="section">
          <div className="kicker">04 · routed models</div>
          <h3 className="subtitle">
            <Code2 size={14} /> drop any of these in the <code>model</code> field
          </h3>
          <div className="providers">
            <ProviderBlock name="anthropic" models={info.supported_models.anthropic} accent="#d97706" />
            <ProviderBlock name="openai" models={info.supported_models.openai} accent="#10a37f" />
            <ProviderBlock name="gemini" models={info.supported_models.gemini} accent="#4285f4" />
          </div>
        </section>
      </main>

      <footer className="footer">
        <span>protocol: anthropic messages · auth: x-api-key · streaming · tools · vision</span>
        <span className="footer-tag">routed via emergent universal proxy</span>
      </footer>
    </div>
  );
}

export default App;
