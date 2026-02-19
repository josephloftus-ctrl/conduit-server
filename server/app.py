"""FastAPI app — WebSocket endpoint, message handler, fallback chain, settings API."""

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .models.base import StreamChunk, StreamDone
from .ws import ConnectionManager
from .tools import get_all as get_all_tools

log = logging.getLogger("conduit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

manager = ConnectionManager()

# Provider registry — populated at startup
providers: dict = {}
_startup_time: float = 0.0

# Agent registry — populated at startup if agents configured
agent_registry: "AgentRegistry | None" = None

# Lazy imports to avoid circular deps
router_module = None
scheduler_module = None
memory_module = None

# Telegram bot instance (initialized at startup if configured)
telegram_bot = None

# Admin token for settings mutation endpoints.
# If CONDUIT_ADMIN_TOKEN is set in .env, all PUT/POST /api/settings/* require it.
# If not set, endpoints work without auth (backwards compatible).
ADMIN_TOKEN = os.getenv("CONDUIT_ADMIN_TOKEN", "")
STATUS_DASHBOARD_HOST = "status.josephloftus.com"
STATUS_SERVICE_NAMES = [
    "conduit-server",
    "conduit-search",
    "conduit-spectre",
    "conduit-brief",
    "conduit-ntfy",
    "conduit-nginx",
    "conduit-tunnel",
    "conduit-crond",
]
STATUS_LOCAL_CHECKS = [
    {"name": "conduit-server", "url": "http://127.0.0.1:8080/api/health"},
    {"name": "conduit-search", "url": "http://127.0.0.1:8889/health"},
    {"name": "conduit-spectre", "url": "http://127.0.0.1:8000/api/health"},
    {"name": "brief", "url": "http://127.0.0.1:5050/"},
    {"name": "ntfy", "url": "http://127.0.0.1:2586/v1/health"},
    {"name": "steady-spectre", "url": "http://127.0.0.1:8090/"},
]
STATUS_PUBLIC_HOSTS = [
    "conduit.josephloftus.com",
    "status.josephloftus.com",
    "steady.josephloftus.com",
    "brief.josephloftus.com",
    "ntfy.josephloftus.com",
]


async def require_admin(authorization: str = Header(default="")) -> None:
    """Dependency that enforces admin token on settings endpoints.

    If CONDUIT_ADMIN_TOKEN is not configured, all requests pass through
    (backwards compatible). If configured, the request must include a
    matching Authorization: Bearer <token> header.
    """
    if not ADMIN_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Admin token required")
    token = authorization.removeprefix("Bearer ").strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _host_name(request: Request) -> str:
    host = request.headers.get("host", "").strip().lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _is_status_dashboard_host(request: Request) -> bool:
    return _host_name(request) == STATUS_DASHBOARD_HOST


def _run_command(args: list[str], timeout_seconds: float = 3.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 127, "", str(exc)


def _collect_service_status() -> list[dict]:
    prefix = os.getenv("PREFIX", "/data/data/com.termux/files/usr")
    sv_dir = Path(prefix) / "var" / "service"
    rows: list[dict] = []
    for name in STATUS_SERVICE_NAMES:
        rc, out, err = _run_command(["sv", "status", str(sv_dir / name)], timeout_seconds=2.0)
        raw = out or err
        state = "unknown"
        if raw.startswith("run:"):
            state = "run"
        elif raw.startswith("down:"):
            state = "down"
        elif rc != 0:
            state = "error"
        rows.append(
            {
                "service": name,
                "state": state,
                "raw": raw or "(no output)",
            }
        )
    return rows


def _probe_url(url: str, method: str = "GET", timeout_seconds: float = 3.0) -> dict:
    req = urlrequest.Request(url, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout_seconds) as res:
            status = int(getattr(res, "status", 200))
            return {
                "url": url,
                "ok": 200 <= status < 300,
                "status": status,
            }
    except urlerror.HTTPError as exc:
        return {
            "url": url,
            "ok": False,
            "status": int(exc.code),
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "url": url,
            "ok": False,
            "status": 0,
            "error": str(exc),
        }


def _collect_local_checks() -> list[dict]:
    rows = []
    for item in STATUS_LOCAL_CHECKS:
        result = _probe_url(item["url"], method="GET", timeout_seconds=3.0)
        result["name"] = item["name"]
        rows.append(result)
    return rows


def _collect_public_checks() -> list[dict]:
    rows = []
    for host in STATUS_PUBLIC_HOSTS:
        result = _probe_url(f"https://{host}", method="HEAD", timeout_seconds=4.0)
        result["host"] = host
        rows.append(result)
    return rows


def _collect_tunnel_summary() -> dict:
    rc, out, err = _run_command(["cloudflared", "tunnel", "list"], timeout_seconds=5.0)
    text = out or err
    connector_line = ""
    for line in text.splitlines():
        if "conduit-tablet" in line:
            connector_line = line.strip()
            break
    return {
        "ok": rc == 0,
        "summary": connector_line or "(conduit-tablet tunnel row not found)",
        "raw": text.strip(),
    }


def _status_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Conduit Status</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500&display=swap" rel="stylesheet">
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{
      font-family:'Outfit',sans-serif;
      background:#0a0a0f;
      background-image:radial-gradient(ellipse at 50% 0%,rgba(108,140,255,.03) 0%,transparent 60%);
      color:#e4e4ef;
      min-height:100vh;
      -webkit-font-smoothing:antialiased;
    }
    .c{
      max-width:580px;margin:0 auto;padding:60px 24px 48px;
      opacity:0;animation:up .5s ease-out forwards;
    }
    @keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}

    /* header */
    .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:48px}
    .logo{font-size:13px;font-weight:500;letter-spacing:3px;text-transform:uppercase;color:#6c8cff}
    .ov{display:flex;align-items:center;gap:8px;font-size:12px;color:#8888a0}
    .od{width:8px;height:8px;border-radius:50%}
    .od.ok{background:#4caf80;box-shadow:0 0 8px rgba(76,175,128,.5)}
    .od.degraded{background:#e09040;box-shadow:0 0 8px rgba(224,144,64,.5)}
    .od.down{background:#e05050;box-shadow:0 0 8px rgba(224,80,80,.5)}

    /* uptime hero */
    .ut{margin-bottom:56px}
    .uv{font-size:52px;font-weight:300;letter-spacing:-2px;color:#fff;line-height:1;margin-bottom:8px;font-variant-numeric:tabular-nums}
    .ul{font-size:11px;color:#555566;letter-spacing:2px;text-transform:uppercase}

    /* sections */
    .s{margin-bottom:36px;opacity:0;animation:up .4s ease-out forwards}
    .s:nth-child(3){animation-delay:.08s}
    .s:nth-child(4){animation-delay:.14s}
    .s:nth-child(5){animation-delay:.2s}
    .sl{font-size:10px;font-weight:500;letter-spacing:2.5px;text-transform:uppercase;color:#444455;margin-bottom:14px}

    /* service grid */
    .sg{display:grid;grid-template-columns:1fr 1fr;gap:1px 24px}
    .si{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid rgba(255,255,255,.04)}
    .d{width:6px;height:6px;border-radius:50%;flex-shrink:0;transition:background .3s,box-shadow .3s}
    .d.ok{background:#4caf80;box-shadow:0 0 6px rgba(76,175,128,.35)}
    .d.warn{background:#e09040;box-shadow:0 0 6px rgba(224,144,64,.35)}
    .d.bad{background:#e05050;box-shadow:0 0 6px rgba(224,80,80,.35)}
    .d.unk{background:#333344}
    .sn{font-size:13px;color:#b0b0c0}

    /* endpoints */
    .ei{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid rgba(255,255,255,.04)}
    .eh{font-size:13px;color:#b0b0c0}
    .ee{margin-left:auto;font-size:11px;color:#e05050}

    /* providers */
    .pr{display:flex;flex-wrap:wrap;gap:6px}
    .pp{font-size:11px;padding:4px 12px;border-radius:100px;background:rgba(255,255,255,.04);color:#8888a0;letter-spacing:.3px}

    /* footer */
    .ft{margin-top:48px;padding-top:20px;border-top:1px solid rgba(255,255,255,.04);display:flex;align-items:center;justify-content:space-between;font-size:11px;color:#444455}
    .pd{display:inline-block;width:4px;height:4px;border-radius:50%;background:#4caf80;margin-right:6px;animation:pulse 2.5s ease-in-out infinite}
    @keyframes pulse{0%,100%{opacity:.9}50%{opacity:.2}}

    .err{text-align:center;padding:80px 0;color:#555566;font-size:14px}
  </style>
</head>
<body>
  <div class="c" id="r"><div class="err">Loading&hellip;</div></div>
  <script>
    let bUp=0,lf=0,tt=null;
    function fmt(s){
      const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
      if(d>0)return d+'d '+h+'h '+m+'m';
      if(h>0)return h+'h '+m+'m';
      return m+'m '+Math.floor(s%60)+'s';
    }
    function cur(){return bUp+Math.floor((Date.now()-lf)/1000)}
    function dc(ok,st){
      if(ok===true||st==='run')return 'ok';
      if(ok===false||st==='down'||st==='error')return 'bad';
      return 'unk';
    }
    function hl(d){
      const sv=(d.services||[]).every(s=>s.state==='run');
      const pb=(d.public_checks||[]).every(c=>c.ok);
      return sv&&pb?'ok':sv?'degraded':'down';
    }
    function render(d){
      const h=d.health||{},st=hl(d),cc=d.connected_clients||0;
      const lb=st==='ok'?'All systems operational':st==='degraded'?'Partial degradation':'Systems down';
      document.getElementById('r').innerHTML=`
        <div class="hd">
          <div class="logo">Conduit</div>
          <div class="ov"><span class="od ${st}"></span>${lb}</div>
        </div>
        <div class="ut">
          <div class="uv" id="tk">${fmt(cur())}</div>
          <div class="ul">uptime</div>
        </div>
        <div class="s">
          <div class="sl">Services</div>
          <div class="sg">
            ${(d.services||[]).map(s=>`<div class="si"><span class="d ${dc(null,s.state)}"></span><span class="sn">${s.service.replace('conduit-','')}</span></div>`).join('')}
          </div>
        </div>
        <div class="s">
          <div class="sl">Endpoints</div>
          ${(d.public_checks||[]).map(c=>`<div class="ei"><span class="d ${dc(c.ok)}"></span><span class="eh">${c.host}</span>${c.ok?'':`<span class="ee">${c.error||'unreachable'}</span>`}</div>`).join('')}
        </div>
        <div class="s">
          <div class="sl">Providers</div>
          <div class="pr">
            ${(h.providers||[]).map(p=>`<span class="pp">${p}</span>`).join('')}
          </div>
        </div>
        <div class="ft">
          <span><span class="pd"></span>Refreshing every 30s</span>
          <span>${cc} client${cc!==1?'s':''}</span>
        </div>`;
    }
    function tick(){const e=document.getElementById('tk');if(e)e.textContent=fmt(cur())}
    async function refresh(){
      try{
        const r=await fetch('/api/server-dashboard',{cache:'no-store'});
        const d=await r.json();
        bUp=(d.health||{}).uptime_seconds||0;lf=Date.now();
        render(d);
        if(!tt)tt=setInterval(tick,1000);
      }catch(e){if(!bUp)document.getElementById('r').innerHTML='<div class="err">Unable to reach server</div>'}
    }
    refresh();setInterval(refresh,30000);
  </script>
</body>
</html>"""


def _build_providers():
    """Instantiate model providers from config."""
    from .models.openai_compat import OpenAICompatProvider
    from .models.gemini import GeminiProvider
    from .models.anthropic import AnthropicProvider
    from .models.claude_code import ClaudeCodeProvider
    from .models.chatgpt import ChatGPTProvider

    providers.clear()

    for name, prov_cfg in config.PROVIDERS.items():
        ptype = prov_cfg.get("type")
        api_key = config.get_api_key(name)

        if ptype == "openai_compat" and api_key:
            providers[name] = OpenAICompatProvider(
                name=name,
                base_url=prov_cfg["base_url"],
                api_key=api_key,
                model=prov_cfg.get("default_model", ""),
            )
        elif ptype == "gemini":
            use_vertex = prov_cfg.get("vertex", False)
            project_env = prov_cfg.get("project_env", "")
            gcp_project = os.getenv(project_env, "") if project_env else ""
            if use_vertex and gcp_project:
                providers[name] = GeminiProvider(
                    name=name,
                    model=prov_cfg.get("default_model", ""),
                    vertex=True,
                    project=gcp_project,
                    location=prov_cfg.get("location", "us-east4"),
                )
            elif not use_vertex and api_key:
                providers[name] = GeminiProvider(
                    name=name,
                    api_key=api_key,
                    model=prov_cfg.get("default_model", ""),
                )
        elif ptype == "anthropic" and api_key:
            providers[name] = AnthropicProvider(
                name=name,
                api_key=api_key,
                model=prov_cfg.get("model", "claude-opus-4-6"),
            )
        elif ptype == "chatgpt":
            from .chatgpt_auth import is_authenticated
            if is_authenticated():
                providers[name] = ChatGPTProvider(
                    name=name,
                    model=prov_cfg.get("model", "gpt-5.1-codex-mini"),
                )
            else:
                log.warning("ChatGPT provider '%s' skipped — not authenticated", name)
        elif ptype == "claude_code":
            providers[name] = ClaudeCodeProvider(
                name=name,
                model=prov_cfg.get("model", "sonnet"),
                working_dir=os.path.expanduser(prov_cfg.get("working_dir", "~")),
                max_budget_usd=prov_cfg.get("max_budget_usd", 0),
                timeout=prov_cfg.get("timeout", 600),
            )


def render_system_prompt() -> str:
    """Build the system prompt with injected context."""
    now = datetime.now()
    template = config.SYSTEM_PROMPT_TEMPLATE

    # Get memory context if available
    memory_context = ""
    # Memory will be injected asynchronously — use sync placeholder for now
    # The async version is called in handle_message

    # Get pending reminders
    pending = ""

    prompt = template.format(
        name=config.PERSONALITY_NAME,
        time=now.strftime("%I:%M %p"),
        date=now.strftime("%B %d, %Y"),
        day=now.strftime("%A"),
        memories=memory_context,
        pending_tasks=pending,
        tools_context="",
        skills_context="",
        scout_context="",
        worker_context="",
    )
    return prompt


async def render_system_prompt_async(query: str = "") -> str:
    """Build the system prompt with async context (memories, tasks)."""
    now = datetime.now()
    template = config.SYSTEM_PROMPT_TEMPLATE

    # Get memory context (query-aware semantic search)
    memory_context = ""
    if memory_module:
        try:
            memory_context = await memory_module.get_memory_context(query=query)
        except Exception as e:
            log.warning("Failed to get memory context: %s", e)

    # Get pending reminders
    pending = ""
    try:
        raw = await db.kv_get("reminders")
        if raw:
            reminders = json.loads(raw)
            active = [r for r in reminders if r["due"] > now.timestamp()]
            if active:
                lines = ["Pending reminders:"]
                for r in active:
                    due_str = datetime.fromtimestamp(r["due"]).strftime("%I:%M %p")
                    lines.append(f"- {r['text']} (due {due_str})")
                pending = "\n".join(lines)
    except Exception:
        pass

    # Build tools context
    tools_context = ""
    if config.TOOLS_ENABLED and config.ALLOWED_DIRECTORIES:
        lines = [
            "You have full access to the local filesystem via tools. You CAN and SHOULD use them proactively.",
            "",
            "Your file tools:",
            "  - list_directory: browse any allowed directory to see its contents",
            "  - glob_files: find files by pattern (e.g. '**/*.pdf', '*.xlsx')",
            "  - read_file: read file contents",
            "  - grep: search file contents by regex pattern",
            "  - load_project_index: get a pre-built map of a project's files and modules",
            "  - parse_pdf: extract text from PDF files",
            "  - run_command: execute shell commands (requires permission)",
            "",
            "Allowed directories:",
        ]
        dir_descriptions = {
            "~/.index/": "file index — read MANIFEST.md first for directory map",
            "~/Documents/Work/lockheed/": "LM100 operations (sales, inventory, purchasing, reports, catering)",
            "~/Documents/Work/": "work files (training, compliance, reference, receipts)",
            "~/Projects/spectre/": "inventory operations dashboard code",
            "~/Projects/conduit/": "this project's source code",
            "~/Documents/Sorted/": "auto-sorted downloads",
        }
        for d in config.ALLOWED_DIRECTORIES:
            desc = dir_descriptions.get(d, "")
            line = f"  - {d}"
            if desc:
                line += f" ({desc})"
            lines.append(line)
        lines.append("")
        lines.append("IMPORTANT: You can freely browse and explore these directories. Use list_directory to see what's there, glob_files to find files, and read_file to read them. Do NOT say you cannot access files — you can.")
        lines.append("Use load_project_index before exploring any project — it returns a pre-built map of files and modules.")
        if config.WEB_SEARCH_ENABLED:
            lines.append("You can search the web and fetch URL content using web_search, web_search_deep, and web_fetch tools.")
        if config.OUTLOOK_ENABLED:
            lines.append("You can read the user's Outlook inbox using read_inbox, search_email, and read_email tools.")
        tools_context = "\n".join(lines)

    # Build skills context
    skills_context = ""
    if config.MARKDOWN_SKILLS_ENABLED:
        try:
            from .skills import get_skills_context
            skills_context = get_skills_context(query, config.MARKDOWN_SKILLS_MAX_PER_TURN)
        except Exception:
            pass

    # Build scout context from Reddit Scout report
    scout_context = ""
    scout_report_path = Path.home() / "conduit" / "server" / "data" / "scout_report.json"
    try:
        if scout_report_path.exists():
            report_age = (datetime.now() - datetime.fromtimestamp(scout_report_path.stat().st_mtime))
            if report_age < timedelta(hours=48):
                with open(scout_report_path) as f:
                    report = json.load(f)
                high_findings = [
                    f for f in report.get("findings", [])
                    if f.get("relevance_score", 0) >= 7
                ][:5]
                if high_findings:
                    lines = ["Recent AI/dev findings relevant to your architecture:"]
                    for f in high_findings:
                        lines.append(
                            f"- [{f.get('component', '?')}] {f.get('summary', '')} "
                            f"({f.get('effort', '?')} effort, relevance {f.get('relevance_score', 0)}/10)"
                        )
                    lines.append("Reference these when the user asks about improvements or new features.")
                    scout_context = "\n".join(lines)
    except Exception as e:
        log.warning("Failed to load scout report: %s", e)

    # Build worker context
    worker_context = ""
    if config.WORKER_ENABLED:
        try:
            from . import worker as worker_mod
            worker_context = worker_mod.get_status_context()
        except Exception as e:
            log.warning("Failed to get worker context: %s", e)

    prompt = template.format(
        name=config.PERSONALITY_NAME,
        time=now.strftime("%I:%M %p"),
        date=now.strftime("%B %d, %Y"),
        day=now.strftime("%A"),
        memories=memory_context,
        pending_tasks=pending,
        tools_context=tools_context,
        skills_context=skills_context,
        scout_context=scout_context,
        worker_context=worker_context,
    )
    return prompt


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    global _startup_time
    _startup_time = time.time()
    await db.init_db()
    _build_providers()
    log.info("Providers loaded: %s", list(providers.keys()))

    # Build agent registry if configured
    global agent_registry
    if config.AGENTS_LIST:
        from .agents import AgentRegistry
        agent_registry = AgentRegistry.build(
            config.AGENTS_LIST, config.BINDINGS_LIST,
            config.AGENTS_COMMS, providers,
        )

    # Restore saved OpenRouter model override
    or_prov = providers.get("openrouter")
    if or_prov:
        saved_model = await db.kv_get("openrouter_model")
        if saved_model:
            or_prov.model = saved_model
            log.info("OpenRouter model restored: %s", saved_model)

    # Register tools
    if config.TOOLS_ENABLED:
        from .tools.filesystem import register_all as register_fs_tools
        register_fs_tools()
        try:
            from .tools.write import register_all as register_write_tools
            register_write_tools()
        except ImportError:
            pass
        try:
            from .tools.execute import register_all as register_exec_tools
            register_exec_tools()
        except ImportError:
            pass
        try:
            from .tools.web import register_all as register_web_tools
            register_web_tools()
        except ImportError:
            pass
        try:
            from .tools.email import register_all as register_email_tools
            register_email_tools()
        except ImportError:
            pass
        try:
            from .tools.pdf import register_all as register_pdf_tools
            register_pdf_tools()
        except ImportError:
            pass
        # Skill tools — grocery, expenses, calendar
        if getattr(config, "SKILL_GROCERY_ENABLED", True):
            try:
                from .tools.grocery import register_all as register_grocery_tools
                register_grocery_tools()
            except ImportError:
                pass
        if getattr(config, "SKILL_EXPENSES_ENABLED", True):
            try:
                from .tools.expenses import register_all as register_expense_tools
                register_expense_tools()
            except ImportError:
                pass
        if getattr(config, "SKILL_CALENDAR_ENABLED", True):
            try:
                from .tools.calendar import register_all as register_calendar_tools
                register_calendar_tools()
            except ImportError:
                pass
        log.info("Tools registered: %s", [t.name for t in get_all_tools()])

    # Load markdown skills
    if config.MARKDOWN_SKILLS_ENABLED:
        try:
            from .skills import load_skills
            load_skills(config.MARKDOWN_SKILLS_DIR)
        except Exception as e:
            log.warning("Markdown skills not available: %s", e)
        # Register skill_install tool
        try:
            from .skills import skill_install
            from .tools import register as register_tool
            from .tools.definitions import ToolDefinition
            register_tool(ToolDefinition(
                name="skill_install",
                description="Install a markdown skill from ClawHub or a URL.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (folder name)"},
                        "source": {"type": "string", "description": "'clawhub' or a URL to SKILL.md"},
                    },
                    "required": ["name"],
                },
                handler=skill_install,
                permission="write",
            ))
        except Exception as e:
            log.warning("skill_install tool not available: %s", e)

    # Load Python plugins
    if config.PLUGINS_ENABLED:
        try:
            from .plugins import load_all_plugins
            from .tools import register as register_tool
            from .skills import _skills

            plugin_tools, plugin_skills = load_all_plugins(config.PLUGINS_DIR)
            for tool in plugin_tools:
                register_tool(tool)
            # Merge plugin-registered skills into the skills store
            _skills.extend(plugin_skills)
            if plugin_tools:
                log.info("Plugin tools registered: %s", [t.name for t in plugin_tools])
            if plugin_skills:
                log.info("Plugin skills registered: %s", [s["name"] for s in plugin_skills])
        except Exception as e:
            log.warning("Plugins not available: %s", e)

    # Initialize subagent system
    if config.SUBAGENTS_ENABLED:
        try:
            from .subagents import init_registry, init_db as init_subagent_db
            init_registry(
                max_spawn_depth=config.SUBAGENTS_MAX_SPAWN_DEPTH,
                max_children=config.SUBAGENTS_MAX_CHILDREN,
                default_timeout=config.SUBAGENTS_DEFAULT_TIMEOUT,
            )
            await init_subagent_db()
            log.info("Subagent system initialized")
        except Exception as e:
            log.warning("Subagent system not available: %s", e)

    # Initialize embeddings + vectorstore
    try:
        from . import embeddings as emb_mod
        emb_mod.init()
    except Exception as e:
        log.warning("Embeddings not available: %s", e)

    try:
        from . import vectorstore as vs_mod
        vs_ok = await vs_mod.init()
        if vs_ok:
            log.info("Vectorstore initialized")
        else:
            log.warning("Vectorstore init returned False — memories will be unavailable")
    except Exception as e:
        log.warning("Vectorstore not available: %s", e)

    # Load memory module
    global memory_module
    try:
        from . import memory as mem_mod
        memory_module = mem_mod
        log.info("Memory system loaded")
    except Exception as e:
        log.warning("Memory system not available: %s", e)

    # Start scheduler if available
    global scheduler_module
    try:
        from . import scheduler as sched_mod
        scheduler_module = sched_mod
        await sched_mod.start(manager)
        log.info("Scheduler started")
    except Exception as e:
        log.warning("Scheduler not available: %s", e)

    # Sync BM25 index from Firestore
    if config.BM25_ENABLED:
        try:
            from . import memory_index
            await memory_index.sync_from_firestore()
            log.info("BM25 memory index synced from Firestore")
        except Exception as e:
            log.warning("BM25 sync failed (non-fatal): %s", e)

    # Start file watcher if configured
    watcher_observer = None
    if config.WATCHER_ENABLED:
        try:
            from . import watcher as watcher_mod
            watcher_observer = watcher_mod.start(manager)
        except Exception as e:
            log.warning("File watcher not available: %s", e)

    # Initialize Telegram bot if configured
    global telegram_bot
    if config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN:
        from .telegram import TelegramBot
        telegram_bot = TelegramBot(config.TELEGRAM_BOT_TOKEN)
        if config.TELEGRAM_WEBHOOK_URL:
            await telegram_bot.set_webhook(config.TELEGRAM_WEBHOOK_URL, config.TELEGRAM_WEBHOOK_SECRET)
        log.info("Telegram bot initialized")

    yield

    if watcher_observer:
        from . import watcher as watcher_mod
        watcher_mod.stop(watcher_observer)
    if telegram_bot:
        await telegram_bot.delete_webhook()
    if scheduler_module:
        await scheduler_module.stop()
    # Close BM25 index
    try:
        from . import memory_index
        memory_index.close()
    except Exception:
        pass
    try:
        from . import vectorstore as vs_mod
        await vs_mod.close()
    except Exception:
        pass


app = FastAPI(title="Conduit", lifespan=lifespan)


def get_provider(name: str | None = None):
    """Get a provider by name, falling back to default."""
    name = name or config.DEFAULT_PROVIDER
    prov = providers.get(name)
    if not prov:
        available = list(providers.keys())
        if available:
            return providers[available[0]]
        raise RuntimeError("No model providers configured")
    return prov


async def stream_with_fallback(messages: list[dict], system: str,
                                provider_name: str | None = None,
                                ws: WebSocket | None = None) -> tuple[str, "Usage | None", "BaseProvider"]:
    """Stream a response, walking the fallback chain on errors.

    Returns (response_text, usage, provider_that_succeeded).
    """
    from .models.base import Usage

    # Build ordered provider list: requested first, then fallback chain
    chain = []
    if provider_name and provider_name in providers:
        chain.append(provider_name)
    for name in config.FALLBACK_CHAIN:
        if name not in chain and name in providers:
            chain.append(name)
    # Add any remaining providers as last resort
    for name in providers:
        if name not in chain:
            chain.append(name)

    if not chain:
        raise RuntimeError("No model providers configured")

    last_error = None
    for pname in chain:
        provider = providers[pname]
        try:
            full_response = []
            usage = None

            async for item in provider.stream(messages, system=system):
                if isinstance(item, StreamChunk):
                    if ws:
                        await manager.send_chunk(ws, item.text)
                    full_response.append(item.text)
                elif isinstance(item, StreamDone):
                    usage = item.usage

            return "".join(full_response), usage, provider

        except Exception as e:
            last_error = e
            log.warning("Provider %s failed: %s — trying next in chain", pname, e)
            continue

    # All providers failed
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


async def _auto_title_conversation(conversation_id: str, user_msg: str, assistant_msg: str):
    """Generate a title for a new conversation using the brain model."""
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv or conv["title"] != "New Chat":
            return

        brain = providers.get(config.BRAIN_PROVIDER)
        if not brain:
            return

        prompt_messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg[:500]},
            {"role": "user", "content": "Generate a 3-5 word title for this conversation. Reply with ONLY the title, nothing else."},
        ]
        title, _ = await brain.generate(prompt_messages, system="You generate short conversation titles.")
        title = title.strip().strip('"').strip("'")
        if title and len(title) < 60:
            await db.update_conversation_title(conversation_id, title)
            await manager.broadcast({
                "type": "conversation_updated",
                "id": conversation_id,
                "title": title,
            })
    except Exception as e:
        log.warning("Auto-title failed for %s: %s", conversation_id, e)


async def handle_message(ws: WebSocket, data: dict, conversation_id: str):
    """Process an incoming chat message — classify, route, stream back."""
    content = data.get("content", "").strip()
    if not content:
        return

    # Track user activity
    await db.kv_set("last_user_activity", str(datetime.now().timestamp()))

    # Store user message
    await db.add_message(conversation_id, "user", content)

    # Worker boss response hook — intercept if worker is awaiting a response
    if config.WORKER_ENABLED:
        try:
            from . import worker as worker_mod
            if worker_mod.is_awaiting_response():
                reply = await worker_mod.handle_boss_response(content)
                if reply:
                    await db.add_message(conversation_id, "assistant", reply, source="worker")
                    await manager.send_chunk(ws, reply)
                    await manager.send_done(ws)
                    return
        except Exception as e:
            log.warning("Worker response handling failed: %s", e)

    # Drain subagent announcements
    if config.SUBAGENTS_ENABLED:
        try:
            from .subagents import drain_announcements
            session_key = f"websocket:main:{conversation_id}"
            announces = drain_announcements(session_key)
            if announces:
                lines = []
                for a in announces:
                    lines.append(
                        f'[Subagent Complete] "{a.get("label", "?")}" '
                        f'({a.get("status", "?")}). '
                        f'Result: {a.get("result_summary", "")}'
                    )
                announcement_text = "\n".join(lines)
                content = announcement_text + "\n\n" + content
                # Update the stored message with announcement context
                log.info("Injected %d subagent announcements", len(announces))
        except Exception as e:
            log.warning("Announcement drain failed: %s", e)

    # Check for commands (Tier 0)
    if content.startswith("/"):
        handled = await handle_command(ws, content, conversation_id)
        if handled:
            return

    # Build message history
    history = await db.get_messages(conversation_id, limit=50)
    conversation_length = len(history)

    # --- Agent resolution path ---
    resolved_agent = None
    if agent_registry and agent_registry.has_agents:
        from .agents import BindingContext, extract_command

        cmd = extract_command(content)
        ctx = BindingContext(channel="websocket", command=cmd, content=content)
        resolved_agent = agent_registry.resolve(ctx)

    if resolved_agent:
        # Agent path: use resolved agent's provider, tools, prompt, max_turns
        model_content = content
        if content.startswith("/"):
            parts = content.split(maxsplit=1)
            model_content = parts[1] if len(parts) > 1 else ""

        messages = []
        for msg in history[:-1]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": model_content})

        system = await render_system_prompt_async(query=model_content)
        system = resolved_agent.get_system_prompt(system)

        all_tools = get_all_tools()
        comms_tools = agent_registry.get_comms_tools(resolved_agent.id)
        tools = resolved_agent.get_tools(all_tools, extra_tools=comms_tools)
        max_turns = resolved_agent.get_max_turns()
        selected_provider = resolved_agent.provider

        # Budget-gate Opus even on agent path
        if resolved_agent.cfg.provider == config.ESCALATION_PROVIDER:
            used = await db.get_daily_opus_tokens()
            if used >= config.OPUS_DAILY_BUDGET:
                log.warning("Agent '%s' uses Opus but budget exhausted — falling back",
                            resolved_agent.id)
                fallback = agent_registry.get("default")
                if fallback and fallback.id != resolved_agent.id:
                    resolved_agent = fallback
                    selected_provider = fallback.provider
                    tools = fallback.get_tools(all_tools)
                    system = fallback.get_system_prompt(system)
                    max_turns = fallback.get_max_turns()

        # Reminder detection runs regardless of path
        try:
            from .classifier import classify_fast
            intent, _ = classify_fast(content, conversation_length)
            if intent and intent.name == "REMINDER":
                from .scheduler import parse_remind
                await parse_remind(content)
        except Exception:
            pass

        await manager.send_typing(ws)

        try:
            if getattr(selected_provider, 'manages_own_tools', False):
                session_key = resolved_agent.get_session_key("websocket", conversation_id)
                session_id = await db.kv_get(session_key)
                response_text, usage, new_session_id, cost_usd = await selected_provider.run(
                    prompt=model_content, session_id=session_id, ws=ws, manager=manager,
                )
                if new_session_id and new_session_id != session_id:
                    await db.kv_set(session_key, new_session_id)
                provider = selected_provider
            elif selected_provider.supports_tools and config.TOOLS_ENABLED and tools:
                from . import agent
                response_text, usage = await agent.run_agent_loop(
                    messages, system, selected_provider, tools, ws, manager,
                    max_turns=max_turns,
                )
                provider = selected_provider
            else:
                response_text, usage, provider = await stream_with_fallback(
                    messages, system, provider_name=resolved_agent.cfg.provider, ws=ws
                )
        except Exception as e:
            log.error("Agent '%s' failed: %s", resolved_agent.id, e)
            await manager.send_error(ws, f"Agent failed: {e}")
            return

    else:
        # --- Legacy path (no agents configured or no match) ---
        global router_module
        if router_module is None:
            try:
                from . import router as rm
                router_module = rm
            except Exception:
                pass

        provider_name = None
        intent = None
        if router_module:
            provider_name = await router_module.route(content, providers, conversation_length)
            from .classifier import classify_fast
            intent, _ = classify_fast(content, conversation_length)

        if intent and intent.name == "REMINDER":
            from .scheduler import parse_remind
            await parse_remind(content)

        model_content = content
        if router_module:
            model_content = router_module.strip_command(content)
        messages = []
        for msg in history[:-1]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": model_content})

        system = await render_system_prompt_async(query=model_content)

        await manager.send_typing(ws)

        try:
            selected_provider = get_provider(provider_name)
            tools = get_all_tools()

            if getattr(selected_provider, 'manages_own_tools', False):
                session_key = f"cc_session:{conversation_id}"
                session_id = await db.kv_get(session_key)
                response_text, usage, new_session_id, cost_usd = await selected_provider.run(
                    prompt=model_content, session_id=session_id, ws=ws, manager=manager,
                )
                if new_session_id and new_session_id != session_id:
                    await db.kv_set(session_key, new_session_id)
                provider = selected_provider
            elif selected_provider.supports_tools and config.TOOLS_ENABLED and tools:
                from . import agent
                response_text, usage = await agent.run_agent_loop(
                    messages, system, selected_provider, tools, ws, manager,
                    max_turns=config.MAX_AGENT_TURNS,
                )
                provider = selected_provider
            else:
                response_text, usage, provider = await stream_with_fallback(
                    messages, system, provider_name=provider_name, ws=ws
                )
        except Exception as e:
            log.error("All providers failed: %s", e)
            await manager.send_error(ws, f"All providers failed: {e}")
            return

    # Always send meta with model name (even if usage is None/zero)
    in_tok = usage.input_tokens if usage else 0
    out_tok = usage.output_tokens if usage else 0
    await manager.send_meta(ws, provider.model, in_tok, out_tok)

    log.info("Sending 'done' (model=%s) to client for conversation %s",
             provider.model, conversation_id)
    await manager.send_done(ws, model=provider.model)
    log.info("'done' sent successfully")

    # Log usage after done so a db error can't block the client
    if usage:
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

    # Store assistant message
    if response_text:
        await db.add_message(conversation_id, "assistant", response_text,
                             model=provider.model, source=provider.name)

    # Background: auto-title conversation if still "New Chat"
    if response_text:
        asyncio.create_task(
            _auto_title_conversation(conversation_id, content, response_text)
        )

    # Background: extract memories
    if memory_module and config.EXTRACTION_ENABLED:
        asyncio.create_task(
            memory_module.extract_memories(content, response_text, conversation_id)
        )

    # Background: check if summarization needed
    if memory_module:
        msg_count = len(history)
        if msg_count > 0 and msg_count % config.SUMMARY_THRESHOLD == 0:
            asyncio.create_task(
                memory_module.summarize_conversation(conversation_id)
            )


async def handle_command(ws: WebSocket, content: str, conversation_id: str) -> bool:
    """Handle /commands. Returns True if handled."""
    parts = content.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd == "/clear":
        new_id = await db.create_conversation()
        # Update server session to use the new conversation
        ws.conversation_id = new_id
        await manager.send_chunk(ws, "Conversation cleared.")
        await manager.send_done(ws)
        # Tell client to switch to the new conversation
        await manager.send(ws, {
            "type": "conversation_changed",
            "conversation_id": new_id,
        })
        return True

    if cmd == "/models":
        lines = ["**Available providers:**"]
        for name, prov in providers.items():
            role = config.PROVIDERS.get(name, {}).get("role", "")
            lines.append(f"- **{name}**: {prov.model} ({role})")
        await manager.send_chunk(ws, "\n".join(lines))
        await manager.send_done(ws)
        return True

    if cmd == "/usage":
        tokens = await db.get_daily_opus_tokens()
        budget = config.OPUS_DAILY_BUDGET
        # Also show haiku usage
        haiku_tokens = await db.get_daily_provider_tokens("haiku")
        cc_tokens = await db.get_daily_provider_tokens("claude_code")
        lines = [
            f"**Opus**: {tokens:,} / {budget:,} output tokens today",
            f"**Haiku**: {haiku_tokens:,} output tokens today",
            f"**Claude Code**: {cc_tokens:,} output tokens today",
        ]
        await manager.send_chunk(ws, "\n".join(lines))
        await manager.send_done(ws)
        return True

    if cmd == "/remind":
        from .scheduler import parse_remind
        result = await parse_remind(content)
        await manager.send_chunk(ws, result)
        await manager.send_done(ws)
        return True

    if cmd == "/schedule":
        tasks = await db.get_scheduled_tasks()
        if tasks:
            lines = ["**Scheduled tasks:**"]
            for t in tasks:
                status = "enabled" if t["enabled"] else "disabled"
                lines.append(f"- **{t['name']}** -- `{t['cron']}` ({status})")
            await manager.send_chunk(ws, "\n".join(lines))
        else:
            await manager.send_chunk(ws, "No scheduled tasks.")
        await manager.send_done(ws)
        return True

    if cmd == "/memories":
        if memory_module:
            memories = await memory_module.get_all_memories()
            if memories:
                lines = [f"**Memories ({len(memories)}):**"]
                for m in memories[:20]:
                    lines.append(f"- [{m['category']}] {m['content']}")
                if len(memories) > 20:
                    lines.append(f"*...and {len(memories) - 20} more*")
                await manager.send_chunk(ws, "\n".join(lines))
            else:
                await manager.send_chunk(ws, "No memories stored yet.")
        else:
            await manager.send_chunk(ws, "Memory system not available.")
        await manager.send_done(ws)
        return True

    if cmd == "/permissions":
        override = await db.kv_get("auto_approve_tools")
        if override is not None:
            is_on = override == "true"
        else:
            is_on = config.AUTO_APPROVE_ALL
        new_val = not is_on
        await db.kv_set("auto_approve_tools", "true" if new_val else "false")
        status = "**ON** -- all tools auto-approved (no permission prompts)" if new_val else "**OFF** -- write/execute tools require approval"
        await manager.send_chunk(ws, f"Tool auto-approve: {status}")
        await manager.send_done(ws)
        return True

    if cmd == "/model":
        arg = parts[1].strip() if len(parts) > 1 else ""
        or_provider = providers.get("openrouter")
        if not or_provider:
            await manager.send_chunk(ws, "OpenRouter provider not configured.")
            await manager.send_done(ws)
            return True
        if not arg or arg == "list":
            current = or_provider.model
            saved = await db.kv_get("openrouter_model")
            default = config.PROVIDERS.get("openrouter", {}).get("default_model", "openrouter/free")
            lines = [
                f"**Active model:** `{current}`",
                f"**Config default:** `{default}`",
                "",
                "**Quick picks:**",
                "- `openrouter/free` -- auto-route free models",
                "- `google/gemini-2.5-flash` -- fast, free",
                "- `x-ai/grok-4.1-fast` -- free",
                "- `deepseek/deepseek-chat-v3.2` -- free",
                "- `openai/gpt-oss-120b` -- free",
                "",
                "Usage: `/model <model-id>` or `/model reset`",
            ]
            await manager.send_chunk(ws, "\n".join(lines))
        elif arg == "reset":
            default = config.PROVIDERS.get("openrouter", {}).get("default_model", "openrouter/free")
            or_provider.model = default
            await db.kv_set("openrouter_model", "")
            await manager.send_chunk(ws, f"OpenRouter model reset to `{default}`")
        else:
            or_provider.model = arg
            await db.kv_set("openrouter_model", arg)
            await manager.send_chunk(ws, f"OpenRouter model set to `{arg}`")
        await manager.send_done(ws)
        return True

    if cmd == "/agents":
        if agent_registry and agent_registry.has_agents:
            agents = agent_registry.list_agents()
            lines = ["**Configured agents:**"]
            for a in agents:
                cmds = ", ".join(f"`{c}`" for c in a["commands"]) if a["commands"] else "(no bindings)"
                default_tag = " **(default)**" if a["default"] else ""
                lines.append(f"- **{a['id']}**{default_tag}: {a['model']} via {a['provider']} — {cmds}")
            await manager.send_chunk(ws, "\n".join(lines))
        else:
            await manager.send_chunk(ws, "No agents configured.")
        await manager.send_done(ws)
        return True

    if cmd == "/help":
        lines = [
            "**Commands:**",
            "- `/clear` -- new conversation",
            "- `/models` -- list providers",
            "- `/model <id>` -- switch OpenRouter model",
            "- `/usage` -- token budget status",
            "- `/memories` -- view stored memories",
            "- `/permissions` -- toggle tool auto-approve",
            "- `/remind <task> at <time>` -- set a reminder",
            "- `/remind <task> in <N> minutes/hours`",
            "- `/schedule` -- list scheduled tasks",
            "- `/agents` -- list configured agents",
        ]
        # Add agent bindings if configured
        if agent_registry and agent_registry.has_agents:
            lines.append("")
            lines.append("**Agent commands:**")
            for a in agent_registry.list_agents():
                for c in a["commands"]:
                    lines.append(f"- `{c} <query>` -- use {a['id']} agent ({a['provider']})")
        else:
            lines.extend([
                "- `/or <query>` -- use OpenRouter",
                "- `/research <query>` -- use Gemini",
                "- `/opus <query>` -- use Opus (budget-capped)",
                "- `/think <query>` -- use Opus for deep thinking",
                "- `/code <query>` -- use Claude Code (CLI with tools)",
            ])
        lines.extend(["", "Or just talk naturally -- I'll figure out the rest."])
        await manager.send_chunk(ws, "\n".join(lines))
        await manager.send_done(ws)
        return True

    return False


# --- Health check (no auth) ---

@app.get("/api/health")
async def api_health():
    worker_phase = "IDLE"
    try:
        from . import worker as worker_mod
        state = worker_mod._load_state()
        worker_phase = state.get("phase", "IDLE")
    except Exception:
        pass
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _startup_time),
        "providers": list(providers.keys()),
        "worker_phase": worker_phase,
    }


# --- REST endpoints for web UI ---

@app.get("/api/conversations")
async def api_conversations():
    return await db.list_conversations()


@app.get("/api/conversations/{cid}/messages")
async def api_messages(cid: str):
    return await db.get_messages(cid)


@app.post("/api/conversations")
async def api_new_conversation():
    cid = await db.create_conversation()
    return {"id": cid}


@app.put("/api/conversations/{cid}")
async def api_rename_conversation(cid: str, body: dict):
    title = body.get("title", "").strip()
    if not title:
        return {"ok": False, "error": "Title required"}
    await db.update_conversation_title(cid, title)
    await manager.broadcast({"type": "conversation_updated", "id": cid, "title": title})
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
async def api_delete_conversation(cid: str):
    await db.delete_conversation(cid)
    await manager.broadcast({"type": "conversation_deleted", "id": cid})
    return {"ok": True}


# --- Settings API ---

@app.get("/api/settings")
async def api_get_settings():
    from .settings import get_full_settings
    return get_full_settings()


@app.get("/api/server-dashboard")
async def api_server_dashboard():
    health = await api_health()
    services = await asyncio.to_thread(_collect_service_status)
    local_checks = await asyncio.to_thread(_collect_local_checks)
    public_checks = await asyncio.to_thread(_collect_public_checks)
    tunnel = await asyncio.to_thread(_collect_tunnel_summary)
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(),
        "health": health,
        "services": services,
        "local_checks": local_checks,
        "public_checks": public_checks,
        "tunnel": tunnel,
        "connected_clients": len(manager.active),
    }


@app.get("/server-dashboard")
async def api_server_dashboard_page():
    return HTMLResponse(content=_status_dashboard_html())


@app.get("/api/settings/system")
async def api_get_system_settings():
    from .settings import CONFIG_PATH, ENV_PATH

    plugins = []
    try:
        from .plugins import get_loaded_plugins
        plugins = get_loaded_plugins()
    except Exception:
        plugins = []

    scheduled = await db.get_scheduled_tasks()

    outlook_state = {
        "enabled": bool(config.OUTLOOK_ENABLED),
        "configured": False,
        "authenticated": False,
        "poll_interval_minutes": int(config.OUTLOOK_POLL_INTERVAL),
    }
    try:
        from . import outlook
        outlook_state["configured"] = bool(outlook.is_configured())
        outlook_state["authenticated"] = bool(outlook.get_access_token())
    except Exception:
        pass

    return {
        "health": await api_health(),
        "plugins": plugins,
        "scheduled_tasks": scheduled,
        "paths": {
            "config": str(CONFIG_PATH),
            "env": str(ENV_PATH),
            "plugins_dir": config.PLUGINS_DIR,
        },
        "features": {
            "tools_enabled": bool(config.TOOLS_ENABLED),
            "watcher_enabled": bool(config.WATCHER_ENABLED),
            "worker_enabled": bool(config.WORKER_ENABLED),
            "markdown_skills_enabled": bool(config.MARKDOWN_SKILLS_ENABLED),
            "plugins_enabled": bool(config.PLUGINS_ENABLED),
            "subagents_enabled": bool(config.SUBAGENTS_ENABLED),
            "voice_enabled": bool(config.VOICE_ENABLED),
            "outlook": outlook_state,
            "telegram_enabled": bool(config.TELEGRAM_ENABLED),
            "ntfy_enabled": bool(config.NTFY_ENABLED),
        },
    }


@app.get("/api/settings/raw")
async def api_get_settings_raw():
    from .settings import CONFIG_PATH
    return {
        "ok": True,
        "path": str(CONFIG_PATH),
        "yaml": CONFIG_PATH.read_text(),
    }


@app.put("/api/settings/raw")
async def api_set_settings_raw(body: dict, _admin=Depends(require_admin)):
    from .settings import save_config
    import yaml

    yaml_text = body.get("yaml", "")
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        raise HTTPException(status_code=400, detail="Missing yaml text")

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Top-level YAML must be a mapping/object")

    save_config(parsed)
    return {"ok": True}


def _body_string_list(value: object) -> list[str]:
    if value is None:
        return []
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = [chunk.strip() for chunk in value.replace(",", "\n").splitlines()]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        return []
    return [item for item in raw_items if item]


def _body_store_locations(value: object) -> list[dict[str, str]] | None:
    if value is None:
        return None
    locations: list[dict[str, str]] = []
    if isinstance(value, str):
        for line in value.splitlines():
            text = line.strip()
            if not text:
                continue
            if ":" in text:
                store, _, zip_hint = text.partition(":")
            elif "," in text:
                store, _, zip_hint = text.partition(",")
            else:
                store, zip_hint = text, ""
            if store.strip():
                locations.append({"store": store.strip(), "zip_hint": zip_hint.strip()})
        return locations
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                store = str(row.get("store", "")).strip()
                zip_hint = str(row.get("zip_hint", "")).strip()
                if store:
                    locations.append({"store": store, "zip_hint": zip_hint})
            elif isinstance(row, str):
                parts = row.split(":", 1)
                store = parts[0].strip()
                zip_hint = parts[1].strip() if len(parts) == 2 else ""
                if store:
                    locations.append({"store": store, "zip_hint": zip_hint})
        return locations
    return None


@app.get("/api/settings/cost-hunter")
async def api_get_cost_hunter():
    from . import receipt_cost_hunter as rch

    payload = rch.get_dashboard_snapshot()
    outlook_state = {
        "enabled": bool(config.OUTLOOK_ENABLED),
        "configured": False,
        "authenticated": False,
    }
    try:
        from . import outlook
        outlook_state["configured"] = bool(outlook.is_configured())
        outlook_state["authenticated"] = bool(outlook.get_access_token())
    except Exception:
        pass

    return {"ok": True, **payload, "outlook": outlook_state}


@app.put("/api/settings/cost-hunter/setup")
async def api_set_cost_hunter_setup(body: dict, _admin=Depends(require_admin)):
    from . import receipt_cost_hunter as rch

    result = rch.run_setup_wizard(
        mode="set",
        primary_stores=_body_string_list(body.get("primary_stores")) if "primary_stores" in body else None,
        challenger_stores=_body_string_list(body.get("challenger_stores")) if "challenger_stores" in body else None,
        diaper_brands=_body_string_list(body.get("diaper_brands")) if "diaper_brands" in body else None,
        formula_brands=_body_string_list(body.get("formula_brands")) if "formula_brands" in body else None,
        zip_code=str(body.get("zip_code", "")).strip() if "zip_code" in body else None,
        store_locations=_body_store_locations(body.get("store_locations")) if "store_locations" in body else None,
    )
    settings = rch.get_settings()
    report = rch.build_cost_report(days=int(settings.get("report_window_days", 30)))
    return {
        "ok": True,
        "result": result,
        "report": report,
        "report_text": rch.format_report_text(report),
    }


@app.put("/api/settings/cost-hunter/tuning")
async def api_set_cost_hunter_tuning(body: dict, _admin=Depends(require_admin)):
    from . import receipt_cost_hunter as rch

    settings = rch.update_settings(body or {})
    report = rch.build_cost_report(days=int(settings.get("report_window_days", 30)))
    return {
        "ok": True,
        "settings": settings,
        "report": report,
        "report_text": rch.format_report_text(report),
    }


@app.post("/api/settings/cost-hunter/sync")
async def api_sync_cost_hunter(body: dict | None = None, _admin=Depends(require_admin)):
    from . import receipt_cost_hunter as rch

    body = body or {}
    settings = rch.get_settings()
    default_scan = int(settings.get("heartbeat_scan_count", 60))
    try:
        scan_count = int(body.get("scan_count", default_scan))
    except (TypeError, ValueError):
        scan_count = default_scan

    ingest = await rch.ingest_outlook_receipts(scan_count=scan_count)
    auto_setup = None
    if bool(body.get("force_auto_setup")):
        auto_setup = rch.run_setup_wizard(mode="auto", force=True)

    report_days = int(settings.get("report_window_days", 30))
    report = rch.build_cost_report(days=report_days)
    return {
        "ok": True,
        "ingest": ingest,
        "auto_setup": auto_setup,
        "report": report,
        "report_text": rch.format_report_text(report),
    }


@app.post("/api/settings/cost-hunter/auto-setup")
async def api_auto_setup_cost_hunter(body: dict | None = None, _admin=Depends(require_admin)):
    from . import receipt_cost_hunter as rch

    body = body or {}
    result = rch.run_setup_wizard(mode="auto", force=bool(body.get("force")))
    settings = rch.get_settings()
    report = rch.build_cost_report(days=int(settings.get("report_window_days", 30)))
    return {
        "ok": True,
        "result": result,
        "report": report,
        "report_text": rch.format_report_text(report),
    }


@app.get("/api/settings/cost-hunter/report")
async def api_get_cost_hunter_report(days: int = 0):
    from . import receipt_cost_hunter as rch

    settings = rch.get_settings()
    if days <= 0:
        days = int(settings.get("report_window_days", 30))
    report = rch.build_cost_report(days=days)
    return {
        "ok": True,
        "days": days,
        "report": report,
        "text": rch.format_report_text(report),
    }


@app.put("/api/settings/personality")
async def api_set_personality(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config
    cfg = get_config()
    personality = cfg.setdefault("personality", {})
    if "name" in body:
        personality["name"] = body["name"]
    if "system_prompt" in body:
        personality["system_prompt"] = body["system_prompt"]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/providers/{name}")
async def api_set_provider(name: str, body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config, set_env_var
    cfg = get_config()
    providers_cfg = cfg.setdefault("models", {}).setdefault("providers", {})

    if name not in providers_cfg:
        providers_cfg[name] = {}

    prov = providers_cfg[name]
    for key in ("base_url", "model", "default_model", "type", "role", "vertex", "location"):
        if key in body:
            prov[key] = body[key]

    if "enabled" in body and not body["enabled"]:
        # Disable by removing
        providers_cfg.pop(name, None)
    else:
        providers_cfg[name] = prov

    # Handle API key — write to .env
    if "api_key" in body and body["api_key"]:
        env_var = prov.get("api_key_env", f"{name.upper()}_API_KEY")
        set_env_var(env_var, body["api_key"])

    save_config(cfg)

    # Rebuild providers
    _build_providers()
    return {"ok": True}


@app.put("/api/settings/routing")
async def api_set_routing(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config
    cfg = get_config()
    routing = cfg.setdefault("models", {}).setdefault("routing", {})
    for key in ("default", "fallback_chain", "long_context", "escalation", "brain", "opus_daily_budget_tokens"):
        if key in body:
            routing[key] = body[key]
    # Classifier fields are stored under a separate YAML section but exposed on the routing tab
    classifier_keys = ("complexity_threshold", "long_context_chars")
    if any(k in body for k in classifier_keys):
        classifier = cfg.setdefault("classifier", {})
        for key in classifier_keys:
            if key in body:
                classifier[key] = body[key]
    save_config(cfg)
    config.reload()
    return {"ok": True}


@app.put("/api/settings/scheduler")
async def api_set_scheduler(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config
    cfg = get_config()
    sched = cfg.setdefault("scheduler", {})
    for key in ("active_hours", "heartbeat_interval_minutes", "idle_checkin_minutes", "reminder_check_minutes"):
        if key in body:
            sched[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/memory")
async def api_set_memory(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config
    cfg = get_config()
    mem = cfg.setdefault("memory", {})
    for key in ("max_memories", "summary_threshold", "extraction_enabled"):
        if key in body:
            mem[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/tools")
async def api_set_tools(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config
    # Security-sensitive keys are excluded — these can only be changed via
    # config.yaml directly or the /permissions WebSocket command (runtime toggle).
    DANGEROUS_KEYS = {"enabled", "allowed_directories"}
    rejected = [k for k in body if k in DANGEROUS_KEYS]
    if rejected:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot modify protected settings via API: {', '.join(rejected)}. Edit config.yaml directly.",
        )
    cfg = get_config()
    tools_cfg = cfg.setdefault("tools", {})
    for key in ("max_agent_turns", "command_timeout_seconds", "auto_approve_reads", "auto_approve_all"):
        if key in body:
            tools_cfg[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/ntfy")
async def api_set_ntfy(body: dict, _admin=Depends(require_admin)):
    from .settings import get_config, save_config, set_env_var
    cfg = get_config()
    ntfy_cfg = cfg.setdefault("ntfy", {})
    if "enabled" in body:
        ntfy_cfg["enabled"] = body["enabled"]
    # Write env vars
    if "server" in body:
        set_env_var("NTFY_SERVER", body["server"])
    if "topic" in body:
        set_env_var("NTFY_TOPIC", body["topic"])
    if "token" in body:
        set_env_var("NTFY_TOKEN", body["token"])
    save_config(cfg)
    return {"ok": True}


@app.get("/api/settings/usage")
async def api_get_usage():
    daily = await db.get_usage_by_provider(days=1)
    weekly = await db.get_usage_by_provider(days=7)
    opus_today = await db.get_daily_opus_tokens()
    return {
        "daily": daily,
        "weekly": weekly,
        "opus_today": opus_today,
        "opus_budget": config.OPUS_DAILY_BUDGET,
    }


@app.get("/api/memories")
async def api_get_memories():
    if memory_module:
        return await memory_module.get_all_memories()
    return []


@app.delete("/api/memories/{memory_id}")
async def api_delete_memory(memory_id: str):
    from . import vectorstore
    await vectorstore.delete(memory_id)
    return {"ok": True}


@app.post("/api/settings/test-ntfy")
async def api_test_ntfy():
    from . import ntfy as ntfy_mod
    await ntfy_mod.push(
        title="Test Notification",
        body="If you're seeing this, ntfy is working!",
        tags=["white_check_mark"],
        priority=3,
    )
    return {"ok": True}


@app.post("/api/settings/test-provider/{name}")
async def api_test_provider(name: str):
    if name not in providers:
        return {"ok": False, "error": f"Provider '{name}' not available"}
    prov = providers[name]
    try:
        response, usage = await prov.generate(
            [{"role": "user", "content": "Say hello in one sentence."}],
            system="You are a helpful assistant. Be brief.",
        )
        return {"ok": True, "response": response, "model": prov.model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- ChatGPT OAuth endpoints ---

@app.get("/api/chatgpt/auth/status")
async def api_chatgpt_auth_status():
    from .chatgpt_auth import get_auth_info
    return get_auth_info()


@app.post("/api/chatgpt/auth/start")
async def api_chatgpt_auth_start():
    from .chatgpt_auth import initiate_device_flow
    flow = initiate_device_flow()
    if not flow:
        return {"ok": False, "error": "Failed to initiate device flow"}
    return {
        "ok": True,
        "user_code": flow.get("user_code", ""),
        "verification_uri": flow.get("verification_uri", ""),
        "verification_uri_complete": flow.get("verification_uri_complete", ""),
        "device_code": flow.get("device_code", ""),
        "interval": flow.get("interval", 5),
        "expires_in": flow.get("expires_in", 900),
    }


@app.post("/api/chatgpt/auth/poll")
async def api_chatgpt_auth_poll(body: dict):
    from .chatgpt_auth import poll_device_flow
    device_code = body.get("device_code", "")
    if not device_code:
        return {"ok": False, "error": "Missing device_code"}
    result = poll_device_flow(device_code)
    if result["status"] == "complete":
        _build_providers()
    return result


# --- Voice endpoints ---

@app.post("/api/voice/transcribe")
async def api_voice_transcribe(file: UploadFile):
    """Transcribe uploaded audio to text via Whisper."""
    if not config.VOICE_ENABLED or not config.OPENAI_API_KEY:
        return {"ok": False, "error": "Voice not configured"}
    try:
        from . import voice
        audio_bytes = await file.read()
        text = await voice.transcribe(audio_bytes, filename=file.filename or "audio.webm")
        return {"ok": True, "text": text}
    except Exception as e:
        log.error("Transcribe error: %s", e)
        return {"ok": False, "error": str(e)}


@app.post("/api/voice/speak")
async def api_voice_speak(body: dict):
    """Convert text to speech, returns OGG/Opus audio."""
    if not config.VOICE_ENABLED or not config.OPENAI_API_KEY:
        return Response(status_code=503, content="Voice not configured")
    text = body.get("text", "").strip()
    if not text:
        return Response(status_code=400, content="No text provided")
    try:
        from . import voice
        audio_bytes = await voice.speak(text)
        return Response(content=audio_bytes, media_type="audio/ogg")
    except Exception as e:
        log.error("TTS error: %s", e)
        return Response(status_code=500, content=str(e))


# --- Telegram webhook ---

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive incoming Telegram messages via webhook."""
    if not telegram_bot:
        return {"ok": False, "error": "Telegram not configured"}

    # Verify webhook secret if configured
    if config.TELEGRAM_WEBHOOK_SECRET:
        token = request.headers.get("x-telegram-bot-api-secret-token", "")
        if token != config.TELEGRAM_WEBHOOK_SECRET:
            log.warning("Telegram webhook: invalid secret token")
            return {"ok": False}

    data = await request.json()
    msg = data.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")
    voice_obj = msg.get("voice")

    if voice_obj and chat_id:
        # Voice message — transcribe, process, respond with voice
        from . import telegram as tg_module
        file_id = voice_obj.get("file_id", "")
        task = asyncio.create_task(tg_module.handle_telegram_voice(telegram_bot, chat_id, file_id))
        task.add_done_callback(lambda t: log.error("Telegram voice handler error: %s", t.exception()) if not t.cancelled() and t.exception() else None)
    elif text and chat_id:
        from . import telegram as tg_module
        task = asyncio.create_task(tg_module.handle_telegram_message(telegram_bot, chat_id, text))
        task.add_done_callback(lambda t: log.error("Telegram handler error: %s", t.exception()) if not t.cancelled() and t.exception() else None)

    return {"ok": True}


# --- WebSocket endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    await manager.send_hello(ws)

    # Each WS connection gets a conversation — stored on ws for shared mutation
    ws.conversation_id = await db.create_conversation()
    # Track active message task so the receive loop stays free for
    # permission_response and other control messages.
    active_task: asyncio.Task | None = None

    def _on_message_done(task: asyncio.Task):
        nonlocal active_task
        active_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("Message handler error: %s", exc)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_error(ws, "Invalid JSON")
                continue

            msg_type = data.get("type")

            if msg_type == "message":
                # Run in a task so the receive loop stays free to handle
                # permission_response, ping, etc. while the agent loop runs.
                if active_task and not active_task.done():
                    await manager.send_error(ws, "Still processing previous message")
                    continue
                active_task = asyncio.create_task(
                    handle_message(ws, data, ws.conversation_id)
                )
                active_task.add_done_callback(_on_message_done)

            elif msg_type == "set_conversation":
                new_cid = data.get("conversation_id")
                if new_cid:
                    ws.conversation_id = new_cid

            elif msg_type == "permission_response":
                manager.resolve_permission(
                    data.get("id", ""),
                    data.get("granted", False),
                )

            elif msg_type == "ping":
                pass  # keep-alive, no response needed

    except WebSocketDisconnect:
        if active_task and not active_task.done():
            active_task.cancel()
        manager.disconnect(ws)
    except Exception as e:
        log.error("WS error: %s", e)
        if active_task and not active_task.done():
            active_task.cancel()
        manager.disconnect(ws)


# --- Static file serving (Svelte build) ---

WEB_DIST = Path(__file__).parent.parent / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    """Prevent browser caching of index.html so new builds are picked up immediately."""
    if _is_status_dashboard_host(request) and request.url.path in {"/", "/index.html"}:
        response = HTMLResponse(content=_status_dashboard_html())
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response
