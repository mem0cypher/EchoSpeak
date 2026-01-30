"""
Core agent module for Echo Speak.
Implements the conversational AI agent with memory and tools.
Supports multiple LLM providers: OpenAI, Ollama, LM Studio, LocalAI, llama.cpp, vLLM.
"""

import os
import sys
import uuid
import json
import importlib.util
import re
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from loguru import logger

try:
    from langchain.agents import AgentType
except ImportError:
    try:
        from langchain.agents.agent_types import AgentType
    except ImportError:
        AgentType = None

try:
    from langchain.agents import initialize_agent
except Exception:
    try:
        from langchain.agents.initialize import initialize_agent
    except Exception:
        initialize_agent = None
try:
    from langchain.agents import AgentExecutor
except Exception:
    try:
        from langchain.agents.agent import AgentExecutor
    except Exception:
        AgentExecutor = None

try:
    from langchain.agents import create_tool_calling_agent
except ImportError:
    create_tool_calling_agent = None

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
except ImportError:
    from langchain.schema import AIMessage, HumanMessage, SystemMessage

try:
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
except ImportError:
    try:
        from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
    except ImportError:
        ChatPromptTemplate = None
        MessagesPlaceholder = None

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

try:
    from langchain_ollama import ChatOllama
except ImportError:
    ChatOllama = None

try:
    from langchain_community.llms import LlamaCpp
except ImportError:
    LlamaCpp = None

try:
    from langchain_community.llms import VLLM
except ImportError:
    VLLM = None
try:
    from langgraph.prebuilt import create_react_agent
except ImportError:
    create_react_agent = None
try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:
    InMemorySaver = None
try:
    from langchain_core.messages.utils import trim_messages, count_tokens_approximately
except ImportError:
    trim_messages = None
    count_tokens_approximately = None
from pydantic import BaseModel, Field
from typing import List, Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESEARCH_TOOL_NAMES = {
    "web_search",
    "live_web_search",
    "youtube_transcript",
    "browse_task",
}

AUTOMATION_TOOL_NAMES = {
    "desktop_list_windows",
    "desktop_find_control",
    "desktop_click",
    "desktop_type_text",
    "desktop_activate_window",
    "desktop_send_hotkey",
    "open_chrome",
    "open_application",
    "file_list",
    "file_read",
    "file_write",
    "file_move",
    "file_copy",
    "file_delete",
    "file_mkdir",
    "artifact_write",
    "analyze_screen",
    "vision_qa",
    "take_screenshot",
    "notepad_write",
    "terminal_run",
}

_TRACE_LOCK = threading.Lock()


class _TraceHandler:
    def __init__(self, trace: Dict[str, Any]):
        self._trace = trace

    def on_tool_start(self, serialized: dict, input_str: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        tool_name = (serialized or {}).get("name") or (serialized or {}).get("id") or "tool"
        call_id = str(run_id)
        self._trace.setdefault("tool_runs", {})[call_id] = {
            "name": tool_name,
            "started_at": time.perf_counter(),
        }
        tools_used = self._trace.setdefault("tools_used", set())
        if isinstance(tools_used, set):
            tools_used.add(tool_name)

    def on_tool_end(self, output: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        runs = self._trace.get("tool_runs", {})
        info = runs.pop(call_id, None)
        if not info:
            return
        duration_ms = (time.perf_counter() - float(info.get("started_at") or 0.0)) * 1000.0
        self._trace.setdefault("tool_latencies_ms", []).append(
            {"tool": info.get("name") or "tool", "ms": round(duration_ms, 2)}
        )

    def on_tool_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        runs = self._trace.get("tool_runs", {})
        info = runs.pop(call_id, None)
        if not info:
            return
        duration_ms = (time.perf_counter() - float(info.get("started_at") or 0.0)) * 1000.0
        self._trace.setdefault("tool_latencies_ms", []).append(
            {"tool": info.get("name") or "tool", "ms": round(duration_ms, 2), "error": True}
        )

from config import config, ModelProvider, get_llm_config
from agent.memory import AgentMemory
from agent.skills_registry import (
    build_skills_prompt,
    list_skills,
    list_workspaces,
    load_skills,
    load_workspace,
    merge_tool_allowlists,
)
from agent.tools import get_available_tools

SYSTEM_PROMPT_BASE = (
    "You are Echo Speak, a conversational AI companion. "
    "Default to natural, friendly replies that feel like a quick chat. "
    "Do not add recaps, summaries, or 'next steps' unless the user explicitly asks. "
    "Keep responses concise and avoid boilerplate acknowledgments unless the user invites it. "
    "Mirror the user's tone; if they sound excited, you can open with a brief, warm reaction. "
    "Use lists or headings only when the user requests them or when needed for clarity. "
    "If you use tools, weave results into a short, conversational answer without report-style formatting. "
    "For any time-sensitive facts (news, sports, prices, schedules, ongoing events, 'this year', 'latest'), prefer using web_search/live_web_search rather than relying on memory or model knowledge. "
    "Treat memory/context as potentially stale; if it conflicts with fresh web results, trust the web results."
)


class ConversationMemory(BaseModel):
    """Simple conversation memory for agent interactions."""
    messages: List[Dict[str, str]] = Field(default_factory=list)
    memory_key: str = "chat_history"

    def load_memory_variables(self, inputs: Dict[str, Any] = None) -> Dict[str, Any]:
        return {self.memory_key: self.messages}

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        if "input" in inputs:
            self.messages.append({"role": "human", "content": inputs["input"]})
        if "output" in outputs:
            self.messages.append({"role": "ai", "content": outputs["output"]})

    def clear(self) -> None:
        self.messages = []


class LLMWrapper:
    """Wrapper class for different LLM providers."""

    def __init__(self, llm_type: ModelProvider):
        self.llm_type = llm_type
        self.llm = self._create_llm()

    def _create_llm(self) -> Any:
        llm_config = config.openai if self.llm_type == ModelProvider.OPENAI else config.local
        model_name = getattr(llm_config, "model_name", None) or getattr(llm_config, "model", None)
        base_url = getattr(llm_config, "base_url", None)
        temperature = llm_config.temperature
        max_tokens = llm_config.max_tokens

        if self.llm_type == ModelProvider.OPENAI:
            if ChatOpenAI is None:
                raise ImportError("langchain-openai is required for provider=openai")
            return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                api_key=config.openai.api_key or "not-needed",
                max_tokens=max_tokens
            )
        elif self.llm_type == ModelProvider.OLLAMA:
            if ChatOllama is None:
                raise ImportError("langchain-ollama is required for provider=ollama")
            if getattr(config, "use_tool_calling_llm", False):
                try:
                    from tool_calling_llm import ToolCallingLLM  # type: ignore

                    class OllamaWithTools(ToolCallingLLM, ChatOllama):
                        @property
                        def _llm_type(self):
                            return "ollama_with_tools"

                    try:
                        return OllamaWithTools(
                            model=model_name,
                            base_url=base_url,
                            temperature=temperature,
                            num_predict=max_tokens,
                            format="json",
                        )
                    except TypeError:
                        return OllamaWithTools(
                            model=model_name,
                            base_url=base_url,
                            temperature=temperature,
                            num_predict=max_tokens,
                        )
                except Exception as exc:
                    logger.warning(f"tool_calling_llm unavailable; falling back to ChatOllama: {exc}")

            return ChatOllama(
                model=model_name,
                base_url=base_url,
                temperature=temperature,
                num_predict=max_tokens
            )
        elif self.llm_type in (ModelProvider.LM_STUDIO, ModelProvider.LOCALAI):
            if ChatOpenAI is None:
                raise ImportError("langchain-openai is required for provider=lmstudio/localai")
            openai_compat_base = base_url or ""
            if openai_compat_base.endswith("/v1"):
                base = openai_compat_base
            else:
                base = f"{openai_compat_base}/v1" if openai_compat_base else ""
            return ChatOpenAI(
                model=model_name,
                temperature=temperature,
                base_url=base,
                api_key="not-needed",
                max_tokens=max_tokens
            )
        elif self.llm_type == ModelProvider.LLAMA_CPP:
            if LlamaCpp is None:
                raise ImportError("langchain-community + llama-cpp-python are required for provider=llama_cpp")
            return LlamaCpp(
                model_path=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                n_ctx=llm_config.context_length,
                n_gpu_layers=llm_config.gpu_layers,
                use_mmap=llm_config.use_mmap,
                use_mlock=llm_config.use_mlock,
                n_threads=llm_config.threads,
                verbose=True
            )
        elif self.llm_type == ModelProvider.VLLM:
            if VLLM is None:
                raise ImportError("langchain-community is required for provider=vllm")
            return VLLM(
                model=model_name,
                tensor_parallel_size=1,
                trust_remote_code=True,
                base_url=base_url
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {self.llm_type}")

    def invoke(self, text: str) -> str:
        response = self.llm.invoke(text)
        if hasattr(response, 'content'):
            return response.content
        return str(response)


class Tool:
    """Simple tool wrapper."""

    def __init__(self, name: str, func, description: str):
        self.name = name
        self.func = func
        self.description = description

    def run(self, **kwargs) -> str:
        try:
            result = self.func(**kwargs)
            if result is None:
                return "Tool executed successfully."
            return str(result)
        except Exception as e:
            return f"Error: {str(e)}"

    def invoke(self, **kwargs) -> str:
        return self.run(**kwargs)


class EchoSpeakAgent:
    """Main conversational agent for Echo Speak."""

    def __init__(self, memory_path: Optional[str] = None, llm_provider: ModelProvider = None):
        logger.info("Initializing Echo Speak Agent...")
        self.llm_provider = llm_provider or (ModelProvider.OLLAMA if config.use_local_models else ModelProvider.OPENAI)
        self.llm_wrapper = LLMWrapper(self.llm_provider)
        self.memory = AgentMemory(memory_path)
        self.conversation_memory = ConversationMemory()
        self._summary: str = ""
        self.document_store = None
        if getattr(config, "document_rag_enabled", False):
            if self.memory.embeddings is None:
                logger.warning("Document RAG disabled: embeddings unavailable")
            else:
                try:
                    from agent.document_store import DocumentStore

                    self.document_store = DocumentStore(
                        self.memory.embeddings,
                        str(getattr(config, "docs_index_path", "")),
                        str(getattr(config, "docs_meta_path", "")),
                    )
                except Exception as exc:
                    logger.warning(f"Document RAG disabled: {exc}")
        self._last_doc_sources: list[dict[str, Any]] = []
        self._graph_system_prompt = SYSTEM_PROMPT_BASE
        self._graph_checkpointer = InMemorySaver() if InMemorySaver is not None else None
        self._graph_trim_max_tokens = self._resolve_trim_max_tokens()
        self._graph_pre_model_hook = False
        self._pending_action: Optional[Dict[str, Any]] = None
        self._pending_detail: Optional[Dict[str, str]] = None
        self._last_tts_text: str = ""
        self._langgraph_agent_cache: Dict[frozenset[str], Dict[str, Any]] = {}
        self._tool_calling_executor_cache: Dict[frozenset[str], Any] = {}
        self._trace_enabled = bool(getattr(config, "trace_enabled", False))
        trace_path = str(getattr(config, "trace_path", "") or "").strip()
        self._trace_path = Path(trace_path) if trace_path else None
        self._last_trace_id: Optional[str] = None
        self._last_memory_mode: Optional[str] = None
        self._last_memory_thread_id: Optional[str] = None
        self._workspace_id: Optional[str] = None
        self._workspace_name: str = ""
        self._workspace_prompt: str = ""
        self._skills_prompt: str = ""
        self._tool_allowlist_override: Optional[set[str]] = None
        self.lc_tools = [
            t
            for t in get_available_tools()
            if getattr(t, "name", "")
            not in {
                "open_chrome",
                "open_application",
                "browse_task",
                "desktop_click",
                "desktop_type_text",
                "desktop_activate_window",
                "desktop_send_hotkey",
                "file_write",
                "file_move",
                "file_copy",
                "file_delete",
                "file_mkdir",
                "artifact_write",
                "notepad_write",
                "terminal_run",
            }
        ]
        self.tools = self._create_tools()
        self.graph_agent = self._create_langgraph_agent()
        if self.graph_agent is None:
            self.agent_executor = self._create_agent_executor()
            self.fallback_executor = self._create_fallback_executor()
        else:
            self.agent_executor = None
            self.fallback_executor = None

        try:
            tool_names = frozenset([str(getattr(t, "name", "")) for t in (self.lc_tools or []) if getattr(t, "name", "")])
        except Exception:
            tool_names = frozenset()
        if tool_names and self.graph_agent is not None:
            self._langgraph_agent_cache[tool_names] = {"graph": self.graph_agent, "pre_model_hook": bool(self._graph_pre_model_hook)}
        if tool_names and self.agent_executor is not None:
            self._tool_calling_executor_cache[tool_names] = self.agent_executor
        logger.info(f"Agent initialized with {len(self.lc_tools)} tools using {self.llm_provider.value}")
        self.configure_workspace(None)

    def _compose_system_prompt(self) -> str:
        parts = [SYSTEM_PROMPT_BASE]
        if self._workspace_prompt:
            parts.append(f"Workspace context:\n{self._workspace_prompt}")
        if self._skills_prompt:
            parts.append(f"Skills:\n{self._skills_prompt}")
        return "\n\n".join([p for p in parts if p.strip()]).strip() or SYSTEM_PROMPT_BASE

    def configure_workspace(self, workspace_id: Optional[str]) -> None:
        workspace_id = (workspace_id or "").strip()
        self._workspace_id = workspace_id or None
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        workspaces_dir = Path(getattr(config, "workspaces_dir", "") or "").expanduser()
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
            workspaces_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        skills = load_skills(skills_dir)
        workspace = load_workspace(workspaces_dir, workspace_id) if workspace_id else None
        skill_defs = []
        if workspace is not None:
            for skill_id in workspace.skill_ids:
                skill = skills.get(skill_id)
                if skill is not None:
                    skill_defs.append(skill)
        self._skills_prompt = build_skills_prompt(skill_defs)
        self._workspace_prompt = (workspace.prompt if workspace else "").strip()
        self._workspace_name = (workspace.name if workspace else "").strip()

        skill_allowlists = [s.tool_allowlist for s in skill_defs]
        workspace_allowlist = workspace.tool_allowlist if workspace else []
        self._tool_allowlist_override = merge_tool_allowlists(workspace_allowlist, skill_allowlists)
        self._graph_system_prompt = self._compose_system_prompt()

    def _tool_allowed(self, name: str) -> bool:
        if not name:
            return False
        allowlist = self._tool_allowlist_override
        if allowlist is None:
            return True
        return name in allowlist

    def _apply_tool_allowlist(self, tools: frozenset[str]) -> frozenset[str]:
        allowlist = self._tool_allowlist_override
        if allowlist is None:
            return tools
        filtered = {t for t in tools if t in allowlist}
        return frozenset(filtered)

    def _command_prefix(self) -> str:
        return str(getattr(config, "command_prefix", "/") or "/").strip() or "/"

    def _available_commands(self) -> dict[str, str]:
        prefix = self._command_prefix()
        base = {
            f"{prefix}commands": "list available commands",
            f"{prefix}skills": "list installed skills",
            f"{prefix}workspaces": "list workspaces",
            f"{prefix}workspace": "set or clear a workspace (ex: /workspace demo or /workspace clear)",
            f"{prefix}doctor": "run environment checks",
        }
        allowed = [c for c in getattr(config, "allowed_commands", []) if c]
        if not allowed:
            return base
        filtered: dict[str, str] = {}
        for cmd, desc in base.items():
            if cmd in allowed:
                filtered[cmd] = desc
        return filtered

    def _load_webhook_secret(self) -> str:
        secret = str(getattr(config, "webhook_secret", "") or "").strip()
        if secret:
            return secret
        path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
        if not path_val:
            return ""
        path = Path(path_val).expanduser()
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        return ""

    def get_doctor_report(self) -> Dict[str, Any]:
        provider_model = config.openai.model if self.llm_provider == ModelProvider.OPENAI else config.local.model_name
        provider_base_url = None
        if self.llm_provider not in (ModelProvider.OPENAI, ModelProvider.LLAMA_CPP):
            provider_base_url = config.local.base_url

        provider_ok = True
        provider_notes: list[str] = []
        if self.llm_provider == ModelProvider.OPENAI:
            api_key = config.openai.api_key or os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                provider_ok = False
                provider_notes.append("Missing OPENAI_API_KEY")
        elif self.llm_provider not in (ModelProvider.LLAMA_CPP,):
            if not (provider_base_url or "").strip():
                provider_ok = False
                provider_notes.append("Missing base_url for local provider")

        memory_ok = bool(getattr(self.memory, "embeddings", None)) or not bool(getattr(self.memory, "use_faiss", True))
        docs_enabled = bool(getattr(config, "document_rag_enabled", False))
        docs_ok = not docs_enabled or self.document_store is not None

        cron_enabled = bool(getattr(config, "cron_enabled", False))
        cron_available = importlib.util.find_spec("croniter") is not None
        webhook_enabled = bool(getattr(config, "webhook_enabled", False))
        webhook_secret = self._load_webhook_secret()
        webhook_ok = not webhook_enabled or bool(webhook_secret)

        allowlist = sorted(self._tool_allowlist_override) if self._tool_allowlist_override else []
        issues: list[str] = []
        if not provider_ok:
            issues.append("provider")
        if not memory_ok:
            issues.append("memory")
        if not docs_ok:
            issues.append("documents")
        if cron_enabled and not cron_available:
            issues.append("croniter")
        if not webhook_ok:
            issues.append("webhook_secret")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "provider": {
                "id": self.llm_provider.value,
                "model": provider_model,
                "base_url": provider_base_url,
                "ok": provider_ok,
                "notes": provider_notes,
            },
            "memory": {
                "path": str(getattr(self.memory, "memory_path", "")),
                "use_faiss": bool(getattr(self.memory, "use_faiss", False)),
                "file_memory_enabled": bool(getattr(self.memory, "file_memory_enabled", False)),
                "ok": memory_ok,
            },
            "documents": {
                "enabled": docs_enabled,
                "ok": docs_ok,
            },
            "workspace": {
                "id": self._workspace_id,
                "name": self._workspace_name,
            },
            "tools": {
                "count": len(self.lc_tools or []),
                "allowlist": allowlist,
            },
            "features": {
                "system_actions": bool(getattr(config, "enable_system_actions", False)),
                "cron_enabled": cron_enabled,
                "croniter_available": cron_available,
                "webhook_enabled": webhook_enabled,
                "webhook_secret_set": bool(webhook_secret),
            },
        }

    def _format_doctor_report(self, report: Dict[str, Any]) -> str:
        status = "OK" if report.get("ok") else "CHECK"
        lines = [f"Doctor report ({status})"]
        provider = report.get("provider") or {}
        prov_line = f"Provider: {provider.get('id')} ({provider.get('model')})"
        if not provider.get("ok"):
            prov_line += " [check]"
        lines.append(prov_line)
        for note in provider.get("notes") or []:
            lines.append(f"  - {note}")

        memory = report.get("memory") or {}
        mem_line = "OK" if memory.get("ok") else "CHECK"
        lines.append(
            f"Memory: {mem_line} (faiss={memory.get('use_faiss')}, file={memory.get('file_memory_enabled')})"
        )

        docs = report.get("documents") or {}
        docs_line = "OK" if docs.get("ok") else "CHECK"
        lines.append(f"Docs: {docs_line} (enabled={docs.get('enabled')})")

        workspace = report.get("workspace") or {}
        lines.append(f"Workspace: {workspace.get('id') or 'none'}")

        tools = report.get("tools") or {}
        lines.append(f"Tools: {tools.get('count', 0)} available")

        features = report.get("features") or {}
        cron_line = "enabled" if features.get("cron_enabled") else "disabled"
        cron_check = "ok" if features.get("croniter_available") else "missing"
        lines.append(f"Cron: {cron_line} (croniter={cron_check})")
        webhook_line = "enabled" if features.get("webhook_enabled") else "disabled"
        webhook_check = "set" if features.get("webhook_secret_set") else "missing"
        lines.append(f"Webhook: {webhook_line} (secret={webhook_check})")

        if report.get("issues"):
            lines.append("Issues: " + ", ".join(report["issues"]))
        return "\n".join(lines)

    def format_doctor_report(self, report: Optional[Dict[str, Any]] = None) -> str:
        if report is None:
            report = self.get_doctor_report()
        return self._format_doctor_report(report)

    def _handle_slash_command(self, user_input: str) -> Optional[str]:
        prefix = self._command_prefix()
        raw = (user_input or "").strip()
        if not raw.startswith(prefix):
            return None
        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]
        available = self._available_commands()
        if cmd not in available:
            return f"Unknown command '{cmd}'. Try {prefix}commands."

        if cmd == f"{prefix}commands":
            lines = [f"{k} - {v}" for k, v in available.items()]
            return "Available commands:\n" + "\n".join(lines)

        if cmd == f"{prefix}skills":
            skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
            items = list_skills(skills_dir)
            if not items:
                return "No skills found."
            return "Skills:\n" + "\n".join([f"- {s}" for s in items])

        if cmd == f"{prefix}workspaces":
            workspaces_dir = Path(getattr(config, "workspaces_dir", "") or "").expanduser()
            items = list_workspaces(workspaces_dir)
            if not items:
                return "No workspaces found."
            return "Workspaces:\n" + "\n".join([f"- {w}" for w in items])

        if cmd == f"{prefix}workspace":
            if not args:
                if self._workspace_id:
                    return f"Active workspace: {self._workspace_id}"
                return "No workspace set."
            target = args[0].strip()
            if target.lower() in {"clear", "none", "default"}:
                self.configure_workspace(None)
                return "Workspace cleared."
            workspaces_dir = Path(getattr(config, "workspaces_dir", "") or "").expanduser()
            workspaces = list_workspaces(workspaces_dir)
            if target not in workspaces:
                return f"Unknown workspace '{target}'."
            self.configure_workspace(target)
            return f"Workspace set to '{target}'."

        if cmd == f"{prefix}doctor":
            report = self.get_doctor_report()
            return self._format_doctor_report(report)

        return None

    def _create_tools(self) -> List[Tool]:
        from agent.tools import (
            live_web_search,
            web_search,
            analyze_screen,
            vision_qa,
            get_system_time,
            calculate,
            take_screenshot,
            open_chrome,
            open_application,
            notepad_write,
            youtube_transcript,
            browse_task,
            system_info,
            desktop_list_windows,
            desktop_find_control,
            desktop_click,
            desktop_type_text,
            desktop_activate_window,
            desktop_send_hotkey,
            file_list,
            file_read,
            file_write,
            file_move,
            file_copy,
            file_delete,
            file_mkdir,
            artifact_write,
            terminal_run,
        )

        tools = [
            Tool("live_web_search", lambda q: live_web_search.invoke({"query": q}), "Browse live/dynamic pages with Playwright for real-time info"),
            Tool("web_search", lambda q: web_search.invoke({"query": q}), "Search the web for information"),
            Tool("get_system_time", lambda: get_system_time.invoke({}), "Get current system time"),
            Tool("calculate", lambda e: calculate.invoke({"expression": e}), "Perform mathematical calculations"),
            Tool("system_info", lambda: system_info.invoke({}), "Get basic OS/CPU/GPU/RAM info"),
            Tool("youtube_transcript", lambda url, language=None: youtube_transcript.invoke({"url": url, "language": language} if language else {"url": url}), "Fetch a YouTube video's transcript"),
            Tool("browse_task", lambda url, task=None: browse_task.invoke({"url": url, "task": task} if task else {"url": url}), "Browse a website (opt-in system action)"),
            Tool("desktop_list_windows", lambda **k: desktop_list_windows.invoke(k), "List open desktop windows (Windows)"),
            Tool("desktop_find_control", lambda **k: desktop_find_control.invoke(k), "Find UI controls in a desktop window (Windows)"),
            Tool("desktop_click", lambda **k: desktop_click.invoke(k), "Click a UI control (opt-in system action)"),
            Tool("desktop_type_text", lambda **k: desktop_type_text.invoke(k), "Type text into a UI control (opt-in system action)"),
            Tool("desktop_activate_window", lambda **k: desktop_activate_window.invoke(k), "Activate a window (opt-in system action)"),
            Tool("desktop_send_hotkey", lambda **k: desktop_send_hotkey.invoke(k), "Send a hotkey (opt-in system action)"),
            Tool("file_list", lambda **k: file_list.invoke(k), "List files within a directory"),
            Tool("file_read", lambda **k: file_read.invoke(k), "Read a text file"),
            Tool("file_write", lambda **k: file_write.invoke(k), "Write text to a file (opt-in system action)"),
            Tool("file_move", lambda **k: file_move.invoke(k), "Move a file/folder (opt-in system action)"),
            Tool("file_copy", lambda **k: file_copy.invoke(k), "Copy a file/folder (opt-in system action)"),
            Tool("file_delete", lambda **k: file_delete.invoke(k), "Delete a file/folder (opt-in system action)"),
            Tool("file_mkdir", lambda **k: file_mkdir.invoke(k), "Create a folder (opt-in system action)"),
            Tool(
                "artifact_write",
                lambda filename=None, content="": artifact_write.invoke({"filename": filename, "content": content}),
                "Write text to a safe artifacts folder and return the file path",
            ),
            Tool("analyze_screen", lambda c="": analyze_screen.invoke({"context": c}), "Analyze screen content with OCR"),
            Tool("vision_qa", lambda q: vision_qa.invoke({"question": q}), "Answer questions about the current screen using a vision-language model"),
            Tool(
                "open_chrome",
                lambda url=None: open_chrome.invoke({"url": url}) if url else open_chrome.invoke({}),
                "Open Google Chrome (opt-in system action)",
            ),
            Tool(
                "open_application",
                lambda app, args=None: open_application.invoke({"app": app, "args": args} if args else {"app": app}),
                "Open/launch an application (opt-in system action; allowlisted)",
            ),
            Tool(
                "notepad_write",
                lambda content, filename=None: notepad_write.invoke({"content": content, "filename": filename} if filename else {"content": content}),
                "Open Notepad, type text, and save an artifact copy (opt-in system action)",
            ),
            Tool("terminal_run", lambda **k: terminal_run.invoke(k), "Run a terminal command (opt-in system action)"),
        ]
        return tools

    def _playwright_enabled(self) -> bool:
        return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))

    def _is_live_web_intent(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        triggers = [
            "right now",
            "currently",
            "today",
            "live",
            "score",
            "scores",
            "weather",
            "forecast",
            "price",
            "stock",
            "stocks",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "exchange rate",
            "flight status",
            "traffic",
            "is it open",
            "availability",
            "released",
        ]
        if any(t in q for t in triggers):
            return True
        if "latest" in q or "breaking" in q:
            return True
        return False

    def _needs_time_context(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        fast_triggers = [
            "right now",
            "currently",
            "today",
            "tonight",
            "tomorrow",
            "this week",
            "this weekend",
            "this month",
            "as of",
        ]
        if any(t in q for t in fast_triggers):
            return True

        schedule_terms = [
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "episode",
            "season",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
        ]
        if any(t in q for t in ["next", "upcoming"]) and any(term in q for term in schedule_terms):
            return True

        if any(t in q for t in ["when is", "when's", "when does", "start time", "starts at", "kickoff", "tipoff"]):
            if any(term in q for term in schedule_terms):
                return True

        return False

    def _is_next_upcoming_schedule_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        if not any(t in q for t in ["next", "upcoming"]):
            return False
        schedule_terms = [
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "episode",
            "season",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
            "plays",
            "play",
        ]
        return any(term in q for term in schedule_terms)

    def _parse_time_context_dt(self, time_context: str) -> Optional[datetime]:
        s = (time_context or "").strip()
        if not s:
            return None
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _extract_dates_from_text(self, text: str, default_year: int) -> list[datetime]:
        t = (text or "")
        if not t.strip():
            return []

        out: list[datetime] = []

        for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t):
            try:
                out.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except Exception:
                continue

        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", t):
            try:
                out.append(datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))))
            except Exception:
                continue

        month_map = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "sept": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        month_re = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        for m in re.finditer(rf"\b({month_re})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*)?(20\d{{2}})?\b", t, flags=re.IGNORECASE):
            mon = month_map.get(m.group(1).lower())
            if not mon:
                continue
            try:
                day = int(m.group(2))
            except Exception:
                continue
            year_s = (m.group(3) or "").strip()
            try:
                year = int(year_s) if year_s else int(default_year)
            except Exception:
                year = int(default_year)
            try:
                out.append(datetime(year, int(mon), int(day)))
            except Exception:
                continue

        return out

    def _answer_mentions_past_date(self, answer_text: str, time_context: str) -> bool:
        now_dt = self._parse_time_context_dt(time_context)
        if now_dt is None:
            return False
        dates = self._extract_dates_from_text(answer_text, default_year=now_dt.year)
        if not dates:
            return False
        now_date = now_dt.date()
        return any(d.date() < now_date for d in dates)

    def _maybe_correct_past_schedule_answer(self, user_input: str, response_text: str, time_context: str, callbacks: Optional[list]) -> str:
        low = self._strip_live_desktop_context(user_input).lower().strip()
        if not response_text or not time_context:
            return response_text
        if not self._is_next_upcoming_schedule_query(low):
            return response_text
        if not self._answer_mentions_past_date(response_text, time_context):
            return response_text

        tool = next((t for t in self.tools if t.name == "web_search"), None)
        if tool is None:
            return response_text

        today = (time_context or "").strip().split(" ", 1)[0].strip()
        qtext = self._extract_search_query(user_input)
        refined = f"{qtext} schedule next game after {today}".strip()

        run_id = str(uuid.uuid4())
        self._emit_tool_start(callbacks, tool.name, refined, run_id)
        try:
            tool_output = tool.invoke(q=refined)
            self._emit_tool_end(callbacks, tool_output, run_id)
        except Exception as exc:
            self._emit_tool_error(callbacks, exc, run_id)
            return response_text

        prompt = (
            "You are Echo Speak, a conversational assistant. "
            "Use the following web search results to answer the user's question. "
            "Be concise and conversational. Do NOT include URLs or markdown links. Do NOT cite sources in the chat reply. "
            "IMPORTANT: Today's date is provided. For 'next'/'upcoming' schedule questions, do NOT answer with a date earlier than today. "
            "If you only find past dates, say you couldn't confirm the next upcoming event and ask a clarifying question.\n\n"
            f"Current system time: {time_context}\n\n"
            f"User question: {user_input}\n\n"
            f"Search results:\n{tool_output}\n\n"
            "Answer:"
        )
        corrected = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))
        if corrected and not self._answer_mentions_past_date(corrected, time_context):
            return corrected
        return f"I’m only seeing past game/event dates in the search results as of {today}. Can you confirm the timeframe/season you mean (and your timezone if relevant)?"

    def _is_direct_time_question(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        direct_time_phrases = [
            "what time is it",
            "time is it",
            "current time",
            "what date is it",
            "what date",
            "current date",
            "today's date",
            "todays date",
            "date today",
        ]
        if not any(p in q for p in direct_time_phrases):
            return False

        schedule_markers = [
            "what time does",
            "start time",
            "starts at",
            "kickoff",
            "tipoff",
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
        ]
        if any(m in q for m in schedule_markers):
            return False

        return True

    def _is_schedule_time_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        time_ask = [
            "what time does",
            "when does",
            "when is",
            "when's",
            "start time",
            "starts at",
            "kickoff",
            "tipoff",
        ]
        if not any(t in q for t in time_ask):
            return False

        schedule_terms = [
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
            "plays",
            "play",
        ]
        return any(term in q for term in schedule_terms)

    def _is_hardware_capability_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        hardware_terms = [
            "my pc",
            "my computer",
            "my laptop",
            "my rig",
            "hardware",
            "specs",
            "cpu",
            "gpu",
            "vram",
            "ram",
            "memory",
        ]
        model_terms = [
            "model",
            "llm",
            "gguf",
            "quant",
            "q4",
            "q5",
            "q8",
            "kimi",
            "k2.5",
            "gpt-oss",
            "ollama",
            "lm studio",
        ]
        intent_terms = [
            "can i run",
            "can my",
            "will it run",
            "run it",
            "handle",
            "support",
            "fit",
            "load",
            "try",
            "testing",
            "use",
            "works on",
            "work with",
        ]

        has_hardware = any(t in q for t in hardware_terms)
        has_model = any(t in q for t in model_terms)
        has_intent = any(t in q for t in intent_terms)
        return (has_intent and (has_hardware or has_model)) or (has_hardware and has_model)

    def _allowed_lc_tool_names(self, user_input: str) -> frozenset[str]:
        text = self._strip_live_desktop_context(user_input)
        low = (text or "").lower().strip()
        if not low:
            return frozenset({"calculate", "get_system_time"})

        has_monitor_ctx = "live desktop context" in (user_input or "").lower()
        if self._has_vision_intent(low, has_monitor_ctx=has_monitor_ctx):
            return frozenset({"vision_qa", "analyze_screen"})

        if self._extract_youtube_url(text):
            return frozenset({"youtube_transcript"})

        if self._is_schedule_time_query(low):
            tools = {"live_web_search", "web_search"} if self._playwright_enabled() else {"web_search"}
            return frozenset(tools)

        if self._is_hardware_capability_query(low):
            tools = {"system_info", "web_search"}
            if self._playwright_enabled() and self._is_live_web_intent(low):
                tools.add("live_web_search")
            return frozenset(tools)

        if self._is_direct_time_question(low):
            return frozenset({"get_system_time"})

        has_calc_keyword = any(ind in low for ind in ["calculate", "compute", "evaluate", "solve", "times", "equals"])
        has_math_operator = bool(re.search(r"\d\s*[+\-*/^]\s*\d", low))
        if has_calc_keyword or has_math_operator:
            return frozenset({"calculate"})

        if any(x in low for x in ["list files", "list folder", "show files", "show folder", "list directory", "browse files"]):
            return frozenset({"file_list"})
        if any(x in low for x in ["read file", "open file", "show file", "view file", "file contents"]):
            return frozenset({"file_read"})

        if self._is_live_web_intent(low):
            tools = {"live_web_search", "web_search"} if self._playwright_enabled() else {"web_search"}
            return frozenset(tools)

        if any(x in low for x in ["search", "look up", "find out", "news", "headlines", "current events"]):
            tools = {"web_search"}
            return frozenset(tools)

        tools = {"web_search"}
        if self._playwright_enabled() and self._is_live_web_intent(low):
            tools.add("live_web_search")
        return frozenset(tools)

    def _get_langgraph_agent_for_toolset(self, tool_names: frozenset[str]) -> Optional[Any]:
        if not tool_names:
            return None
        cached = self._langgraph_agent_cache.get(tool_names)
        if cached is not None:
            try:
                self._graph_pre_model_hook = bool(cached.get("pre_model_hook"))
            except Exception:
                self._graph_pre_model_hook = False
            return cached.get("graph")
        if create_react_agent is None:
            return None

        tools = [t for t in (self.lc_tools or []) if str(getattr(t, "name", "")) in tool_names]
        if not tools:
            return None
        try:
            pre_model_hook = self._build_pre_model_hook()
            checkpointer = self._graph_checkpointer
            try:
                self._graph_pre_model_hook = True
                graph = create_react_agent(self.llm_wrapper.llm, tools, pre_model_hook=pre_model_hook, checkpointer=checkpointer)
            except TypeError:
                self._graph_pre_model_hook = False
                graph = create_react_agent(self.llm_wrapper.llm, tools)
            self._langgraph_agent_cache[tool_names] = {"graph": graph, "pre_model_hook": bool(self._graph_pre_model_hook)}
            return graph
        except Exception:
            return None

    def _get_tool_calling_executor_for_toolset(self, tool_names: frozenset[str]) -> Optional[Any]:
        if not tool_names:
            return None
        cached = self._tool_calling_executor_cache.get(tool_names)
        if cached is not None:
            return cached
        if not self._allow_llm_tool_calling():
            return None
        if AgentExecutor is None or create_tool_calling_agent is None:
            return None
        if ChatPromptTemplate is None or MessagesPlaceholder is None:
            return None

        tools = [t for t in (self.lc_tools or []) if str(getattr(t, "name", "")) in tool_names]
        if not tools:
            return None

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    f"{SYSTEM_PROMPT_BASE}\n\nContext (memory + docs, may be empty):\n{{context}}",
                ),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )
        try:
            agent = create_tool_calling_agent(self.llm_wrapper.llm, tools, prompt)
            executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=6,
            )
            self._tool_calling_executor_cache[tool_names] = executor
            return executor
        except Exception:
            return None

    def _reflect_web_tool_decision(self, user_input: str, tool_name: str, tool_output: str, draft_answer: str) -> Dict[str, Any]:
        out = (tool_output or "").strip()
        if not out:
            return {"confidence": 0.0, "action": "ask_clarifying", "clarifying_question": "What exactly should I check, and for which location or timeframe?"}

        low = out.lower()
        if any(x in low for x in ["no search results", "search failed", "live browse failed", "failed to load page", "playwright is not available"]):
            return {"confidence": 0.2, "action": "retry"}

        try:
            prompt = (
                "You are a strict reviewer. Decide whether the proposed answer adequately addresses the user's question given the tool output. "
                "Return ONLY JSON with keys: confidence (0-1 number), action (one of: accept, retry, ask_clarifying), clarifying_question (string).\n\n"
                f"User question: {user_input}\n\n"
                f"Tool used: {tool_name}\n\n"
                f"Tool output (may be messy):\n{out[:4000]}\n\n"
                f"Proposed answer:\n{(draft_answer or '')[:2000]}\n\n"
            )
            raw = self.llm_wrapper.invoke(prompt)
            m = re.search(r"\{.*\}", str(raw), flags=re.DOTALL)
            if not m:
                raise ValueError("no json")
            data = json.loads(m.group(0))
            if not isinstance(data, dict):
                raise ValueError("bad json")
            action = str(data.get("action") or "accept").strip().lower()
            if action not in {"accept", "retry", "ask_clarifying"}:
                action = "accept"
            cq = str(data.get("clarifying_question") or "").strip()
            try:
                conf = float(data.get("confidence"))
            except Exception:
                conf = 0.7
            return {"confidence": conf, "action": action, "clarifying_question": cq}
        except Exception:
            return {"confidence": 0.8, "action": "accept", "clarifying_question": ""}

    def _create_agent_executor(self) -> Optional[Any]:
        llm = self.llm_wrapper.llm
        if not self._allow_llm_tool_calling():
            logger.info("Tool-calling disabled for provider=%s; skipping AgentExecutor", self.llm_provider.value)
            return None
        if AgentExecutor is None:
            logger.warning("AgentExecutor is unavailable in this LangChain version; disabling tool-calling agent")
            return None
        if create_tool_calling_agent is None:
            logger.warning("Tool-calling agent not available in this LangChain version")
            return None
        if ChatPromptTemplate is None or MessagesPlaceholder is None:
            logger.warning("ChatPromptTemplate/MessagesPlaceholder not available; disabling tool-calling agent")
            return None
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    f"{SYSTEM_PROMPT_BASE}\n\nContext (memory + docs, may be empty):\n{{context}}",
                ),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )

        try:
            agent = create_tool_calling_agent(llm, self.lc_tools, prompt)
            return AgentExecutor(
                agent=agent,
                tools=self.lc_tools,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=6,
            )
        except Exception as exc:
            logger.warning(f"Tool-calling agent unavailable for provider={self.llm_provider.value}: {exc}")
            return None

    def _create_fallback_executor(self) -> Optional[Any]:
        llm = self.llm_wrapper.llm
        if not self._allow_llm_tool_calling():
            logger.info("Tool-calling disabled for provider=%s; skipping ReAct fallback", self.llm_provider.value)
            return None
        if initialize_agent is None:
            logger.warning("initialize_agent is unavailable in this LangChain version; disabling ReAct fallback")
            return None
        try:
            agent_type = (
                AgentType.ZERO_SHOT_REACT_DESCRIPTION
                if AgentType is not None
                else "zero-shot-react-description"
            )
            return initialize_agent(
                tools=self.lc_tools,
                llm=llm,
                agent=agent_type,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=6,
            )
        except Exception as exc:
            logger.warning(f"ReAct fallback agent unavailable for provider={self.llm_provider.value}: {exc}")
            return None

    def _allow_llm_tool_calling(self) -> bool:
        if self.llm_provider == ModelProvider.OPENAI:
            return True
        if self.llm_provider == ModelProvider.LM_STUDIO:
            return bool(getattr(config, "lmstudio_tool_calling", False) or getattr(config, "use_tool_calling_llm", False))
        return bool(getattr(config, "use_tool_calling_llm", False))

    def _resolve_trim_max_tokens(self) -> int:
        try:
            max_tokens = int(getattr(config, "llm_trim_max_tokens", 0) or 0)
        except Exception:
            max_tokens = 0
        if max_tokens > 0:
            return max_tokens
        if self.llm_provider in {ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.OLLAMA, ModelProvider.LLAMA_CPP, ModelProvider.VLLM}:
            try:
                context_len = int(getattr(config.local, "context_length", 0) or 0)
            except Exception:
                context_len = 0
            try:
                reserve = int(getattr(config, "llm_trim_reserve_tokens", 0) or 0)
            except Exception:
                reserve = 0
            if context_len > 0:
                return max(0, context_len - max(reserve, 0))
        return 0

    def _build_pre_model_hook(self):
        def pre_model_hook(state: Dict[str, Any]):
            messages = []
            if isinstance(state, dict):
                messages = state.get("messages") or []

            llm_messages = messages
            if self._graph_trim_max_tokens and trim_messages and count_tokens_approximately:
                llm_messages = trim_messages(
                    messages,
                    strategy="last",
                    token_counter=count_tokens_approximately,
                    max_tokens=self._graph_trim_max_tokens,
                    start_on="human",
                    end_on=("human", "tool"),
                )

            system_prompt = getattr(self, "_graph_system_prompt", SYSTEM_PROMPT_BASE) or SYSTEM_PROMPT_BASE
            return {"llm_input_messages": [SystemMessage(content=system_prompt), *llm_messages]}

        return pre_model_hook

    def _create_langgraph_agent(self) -> Optional[Any]:
        llm = self.llm_wrapper.llm
        if not self._allow_llm_tool_calling():
            logger.info("Tool-calling disabled for provider=%s; using heuristic tools", self.llm_provider.value)
            return None
        if create_react_agent is None:
            logger.warning("LangGraph is unavailable in this environment; using LangChain AgentExecutor")
            return None
        try:
            pre_model_hook = self._build_pre_model_hook()
            checkpointer = self._graph_checkpointer
            try:
                self._graph_pre_model_hook = True
                return create_react_agent(llm, self.lc_tools, pre_model_hook=pre_model_hook, checkpointer=checkpointer)
            except TypeError:
                self._graph_pre_model_hook = False
                return create_react_agent(llm, self.lc_tools)
        except Exception as exc:
            self._graph_pre_model_hook = False
            logger.warning(f"LangGraph agent unavailable for provider={self.llm_provider.value}: {exc}")
            return None

    def _invoke_executor(self, executor: Any, inputs: Dict[str, Any], callbacks: Optional[list]) -> Dict[str, Any]:
        if callbacks:
            try:
                return executor.invoke(inputs, config={"callbacks": callbacks})
            except TypeError:
                return executor.invoke(inputs, callbacks=callbacks)
        return executor.invoke(inputs)

    def _invoke_langgraph(self, graph: Any, messages: list, callbacks: Optional[list], thread_id: Optional[str] = None) -> Any:
        if callbacks or thread_id:
            config: Dict[str, Any] = {}
            if callbacks:
                config["callbacks"] = callbacks
            if thread_id:
                config["configurable"] = {"thread_id": thread_id}
            return graph.invoke({"messages": messages}, config=config)
        return graph.invoke({"messages": messages})

    def _system_prompt_with_context(self, context: str) -> str:
        if context:
            return f"{SYSTEM_PROMPT_BASE}\n\nContext (memory + docs, may be empty):\n{context}"
        return SYSTEM_PROMPT_BASE

    def _build_context_block(self, memory_context: str, doc_context: str) -> str:
        parts: list[str] = []
        if self._summary:
            parts.append(f"Conversation summary:\n{self._summary}")
        if memory_context:
            parts.append(f"Relevant memory:\n{memory_context}")
        if doc_context:
            parts.append(f"Document context:\n{doc_context}")
        return "\n\n".join([p for p in parts if p.strip()]).strip()

    def _get_document_context(self, query: str) -> tuple[str, list]:
        if not getattr(config, "document_rag_enabled", False):
            return "", []
        if self.document_store is None:
            return "", []
        try:
            return self.document_store.query(query, k=4)
        except Exception as exc:
            logger.warning(f"Document RAG query failed: {exc}")
            return "", []

    def _maybe_update_summary(self, mode: Optional[str] = None, thread_id: Optional[str] = None) -> None:
        try:
            trigger = int(getattr(config, "summary_trigger_turns", 18) or 18)
            keep_turns = int(getattr(config, "summary_keep_last_turns", 6) or 6)
        except Exception:
            trigger = 18
            keep_turns = 6
        if trigger <= 0:
            return
        msgs = list(self.conversation_memory.messages)
        turn_count = max(0, len(msgs) // 2)
        if turn_count <= trigger:
            return
        keep_turns = max(2, keep_turns)
        keep_msgs = msgs[-keep_turns * 2 :]
        summarize_msgs = msgs[: -keep_turns * 2]
        if not summarize_msgs:
            return

        transcript = []
        for m in summarize_msgs:
            role = (m.get("role") or "").lower()
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "User" if role in {"human", "user"} else "Assistant"
            transcript.append(f"{label}: {content}")
        if not transcript:
            return

        self._maybe_flush_memory(transcript, mode=mode, thread_id=thread_id)

        base_summary = self._summary.strip()
        prompt = (
            "Summarize the conversation so far in 5-8 bullets. "
            "Capture user preferences, tasks, decisions, and important facts. "
            "Be concise.\n\n"
        )
        if base_summary:
            prompt += f"Existing summary:\n{base_summary}\n\n"
        prompt += "Conversation to summarize:\n" + "\n".join(transcript)

        summary = self.llm_wrapper.invoke(prompt)
        if isinstance(summary, str) and summary.strip():
            self._summary = summary.strip()
            self.conversation_memory.messages = keep_msgs

    def _memory_mode_default(self) -> str:
        return str(getattr(config, "memory_default_mode", "general") or "general").strip() or "general"

    def _split_flush_lines(self, text: str) -> tuple[list[str], list[str]]:
        if not text:
            return [], []
        raw_lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
        if not raw_lines:
            return [], []
        daily: list[str] = []
        curated: list[str] = []
        for raw in raw_lines:
            line = re.sub(r"^[-*•\d\.)\s]+", "", raw).strip()
            if not line:
                continue
            low = line.lower()
            if low in {"no_reply", "noreply", "no reply"}:
                continue
            prefix_hits = ("curated:", "memory:", "long-term:", "permanent:", "keep:")
            if low.startswith(prefix_hits):
                value = line.split(":", 1)[1].strip()
                if value:
                    curated.append(value)
                continue
            daily.append(line)
        return daily, curated

    def _maybe_flush_memory(self, transcript: list[str], mode: Optional[str] = None, thread_id: Optional[str] = None) -> None:
        if not bool(getattr(config, "memory_flush_enabled", False)):
            return
        if not bool(getattr(self.memory, "file_memory_enabled", False)):
            return
        if not transcript:
            return
        system_prompt = str(getattr(config, "memory_flush_system_prompt", "") or "").strip()
        user_prompt = str(getattr(config, "memory_flush_prompt", "") or "").strip()
        prompt = f"{system_prompt}\n\n{user_prompt}\n\nConversation:\n" + "\n".join(transcript)
        try:
            raw = self.llm_wrapper.invoke(prompt)
        except Exception as exc:
            logger.warning(f"Memory flush failed: {exc}")
            return
        text = str(raw or "").strip()
        if not text or text.upper().startswith("NO_REPLY"):
            return
        daily_lines, curated_lines = self._split_flush_lines(text)
        if not daily_lines and not curated_lines:
            daily_lines = [text]
        for line in daily_lines:
            self.memory.append_daily_memory(line, mode=mode, thread_id=thread_id)
        for line in curated_lines:
            self.memory.append_curated_memory(line)
        return

    def _record_turn(self, user_input: str, response_text: str) -> None:
        mode_val = self._memory_mode_default()
        mode_val = mode_val if self._last_memory_mode is None else self._last_memory_mode
        thread_val = self._last_memory_thread_id
        self.memory.add_conversation(user_input, response_text, mode=mode_val, thread_id=thread_val)
        self.conversation_memory.save_context(
            {"input": user_input},
            {"output": response_text},
        )
        self._maybe_update_summary(mode=mode_val, thread_id=thread_val)

    def get_last_doc_sources(self) -> list:
        return list(self._last_doc_sources or [])

    def get_last_tts_text(self) -> str:
        return str(self._last_tts_text or "")

    def _build_action_plan(self, user_input: str, display: str) -> str:
        if not bool(getattr(config, "action_plan_enabled", True)):
            return ""
        try:
            prompt = (
                "Summarize the intended action as a short plan before execution. "
                "Return 2-4 concise bullets. Do NOT include warnings or disclaimers.\n\n"
                f"User request: {user_input}\n"
                f"Planned action: {display}\n"
                "Plan:"
            )
            plan = self.llm_wrapper.invoke(prompt)
            if isinstance(plan, str):
                return plan.strip()
        except Exception as exc:
            logger.warning(f"Action plan generation failed: {exc}")
        return ""

    def _action_confirm_message(self, preview: str, pending: Dict[str, Any], user_input: str) -> str:
        display = self._format_pending_action(pending)
        plan = self._build_action_plan(user_input, display)
        plan_block = f"Plan:\n{plan}\n\n" if plan else ""
        preview_block = f"{preview}\n\n" if preview else ""
        return f"{preview_block}{plan_block}I can do this: {display}. Reply 'confirm' to proceed or 'cancel' to abort."
    

    def _extract_graph_response(self, result: Any) -> str:
        if isinstance(result, dict):
            messages = result.get("messages") or []
        elif isinstance(result, list):
            messages = result
        else:
            messages = []

        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                content = getattr(msg, "content", "")
                return content if isinstance(content, str) else str(content)
            msg_type = getattr(msg, "type", None)
            if msg_type == "ai":
                content = getattr(msg, "content", "")
                return content if isinstance(content, str) else str(content)

        if isinstance(result, dict):
            output = result.get("output") or result.get("final")
            if output:
                return str(output)
        return ""

    def _history_as_messages(self) -> list:
        msgs: list = []
        for item in self.conversation_memory.messages:
            role = (item.get("role") or "").lower()
            content = item.get("content") or ""
            if not content:
                continue
            if role in ("human", "user"):
                msgs.append(HumanMessage(content=content))
            elif role in ("ai", "assistant"):
                msgs.append(AIMessage(content=content))
        return msgs

    def _strip_live_desktop_context(self, query: str) -> str:
        s = (query or "").strip()
        if not s:
            return ""
        low = s.lower()
        marker = "live desktop context:"
        idx = low.find(marker)
        if idx == -1:
            return s
        return s[:idx].strip()

    def _is_action_tool(self, tool_name: str) -> bool:
        return tool_name in {
            "open_chrome",
            "open_application",
            "browse_task",
            "desktop_click",
            "desktop_type_text",
            "desktop_activate_window",
            "desktop_send_hotkey",
            "file_write",
            "file_move",
            "file_copy",
            "file_delete",
            "file_mkdir",
            "artifact_write",
            "notepad_write",
            "terminal_run",
        }

    def _action_allowed(self, tool_name: str) -> bool:
        if tool_name == "open_chrome":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_chrome", False))
        if tool_name == "browse_task":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))
        if tool_name == "open_application":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_application", False))
        if tool_name in {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_desktop_automation", False))
        if tool_name == "file_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name in {"file_move", "file_copy", "file_delete", "file_mkdir"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "artifact_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "notepad_write":
            return bool(
                getattr(config, "enable_system_actions", False)
                and getattr(config, "allow_open_application", False)
                and getattr(config, "allow_desktop_automation", False)
                and getattr(config, "allow_file_write", False)
            )
        if tool_name == "terminal_run":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False))
        return False

    def _is_confirm_text(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return t in {"confirm", "yes", "y", "ok", "okay", "do it", "go ahead", "sure"}

    def _is_cancel_text(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return t in {"cancel", "no", "n", "stop", "never mind", "nevermind", "abort", "dismiss"}

    def _is_detail_request(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        if t in {"more", "more info", "more information", "more details", "details", "detail", "tell me more", "yes", "y", "yep", "yeah", "sure", "ok", "okay", "yes please", "continue"}:
            return True
        if t.startswith("more "):
            return True
        if "more detail" in t or "more info" in t or "tell me more" in t:
            return True
        return False

    def _ensure_more_prompt(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "Do you want more info?"
        low = t.lower()
        if "more" in low and t.rstrip().endswith("?"):
            return t
        if not t.endswith((".", "?", "!")):
            t += "."
        if "more" not in low:
            return f"{t} Do you want more info?"
        if not t.endswith("?"):
            return f"{t}"
        return t

    def _brief_summary_fallback(self, text: str, max_len: int = 160) -> str:
        t = self._strip_links_and_urls(text or "")
        t = re.sub(r"\s+", " ", t).strip()
        if max_len > 0 and len(t) > max_len:
            t = t[:max_len].rstrip(" ,;:") + "…"
        return t

    def _clamp_tts_text(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        try:
            max_chars = int(getattr(config, "tts_response_max_chars", 0) or 0)
        except Exception:
            max_chars = 0
        if max_chars > 0 and len(t) > max_chars:
            t = t[:max_chars].rstrip(" ,;:") + "…"
        return t

    def _select_tts_text(self, user_input: str, full_response: str) -> str:
        mode = str(getattr(config, "tts_response_mode", "brief") or "brief").strip().lower()
        if mode not in {"brief", "full", "auto"}:
            mode = "brief"
        text = full_response
        if mode == "brief":
            text = self._build_brief_summary(user_input, full_response)
        elif mode == "auto":
            if len(full_response) > 180 or full_response.count("\n") > 1:
                text = self._build_brief_summary(user_input, full_response)
        return self._clamp_tts_text(text)

    def _build_brief_summary(self, user_input: str, full_response: str) -> str:
        summary = ""
        try:
            if len(full_response) <= 180 and full_response.count("\n") <= 1:
                summary = self._brief_summary_fallback(full_response, 160)
            else:
                max_words = int(getattr(config, "tts_brief_words", 20) or 20)
                prompt = (
                    f"Write a single short sentence (max {max_words} words) that summarizes the answer in plain language. "
                    "No bullets, no URLs, no markdown, and no questions.\n\n"
                    f"User question: {user_input}\n\n"
                    f"Full answer: {full_response}\n\n"
                    "Short answer:"
                )
                summary = self.llm_wrapper.invoke(prompt)
        except Exception as exc:
            logger.warning(f"Brief summary generation failed: {exc}")

        if not isinstance(summary, str) or not summary.strip():
            summary = self._brief_summary_fallback(full_response, 160)
        summary = self._brief_summary_fallback(str(summary), 160)
        summary = re.sub(r"\s*(do you want|want) more (info|details)\??\s*$", "", summary, flags=re.IGNORECASE).strip()
        return summary

    def _build_quick_recap(self, user_input: str, full_response: str) -> str:
        if not full_response:
            return ""
        if len(full_response) <= 180 and full_response.count("\n") <= 1:
            return ""
        recap = ""
        try:
            prompt = (
                "Summarize the answer into 2-4 bullet points. Each bullet should be <= 12 words. "
                "No URLs, no markdown links, no disclaimers. Return only the bullets.\n\n"
                f"User question: {user_input}\n\n"
                f"Full answer: {full_response}\n\n"
                "Quick recap:"
            )
            recap = self.llm_wrapper.invoke(prompt)
        except Exception as exc:
            logger.warning(f"Quick recap generation failed: {exc}")

        if not isinstance(recap, str) or not recap.strip():
            recap = self._clamp_web_summary(full_response)
        recap = str(recap).strip()
        recap = recap.replace("More in the Research panel.", "").strip()
        return recap

    def _format_pending_action(self, pending: Dict[str, Any]) -> str:
        name = pending.get("tool") or ""
        kwargs = pending.get("kwargs") or {}
        if name == "open_chrome":
            url = (kwargs or {}).get("url")
            if url:
                return f"Open Chrome and navigate to: {url}"
            return "Open Google Chrome"
        if name == "open_application":
            app = (kwargs or {}).get("app")
            args = (kwargs or {}).get("args")
            if app and args:
                return f"Open application: {app} (args: {args})"
            if app:
                return f"Open application: {app}"
            return "Open an application"
        if name == "browse_task":
            url = (kwargs or {}).get("url")
            task = (kwargs or {}).get("task")
            if url and task:
                return f"Browse: {url} (task: {task})"
            if url:
                return f"Browse: {url}"
            return "Browse a website"
        if name == "desktop_click":
            window_title = (kwargs or {}).get("window_title")
            control_name = (kwargs or {}).get("control_name")
            automation_id = (kwargs or {}).get("automation_id")
            control_type = (kwargs or {}).get("control_type")
            parts = []
            if window_title:
                parts.append(f"window={window_title}")
            if control_name:
                parts.append(f"control_name={control_name}")
            if automation_id:
                parts.append(f"automation_id={automation_id}")
            if control_type:
                parts.append(f"control_type={control_type}")
            return "Desktop click (" + ", ".join(parts) + ")" if parts else "Desktop click"
        if name == "desktop_type_text":
            window_title = (kwargs or {}).get("window_title")
            control_name = (kwargs or {}).get("control_name")
            automation_id = (kwargs or {}).get("automation_id")
            control_type = (kwargs or {}).get("control_type")
            text = (kwargs or {}).get("text")
            preview = (text or "")
            if isinstance(preview, str) and len(preview) > 60:
                preview = preview[:60].rstrip() + "…"
            parts = []
            if window_title:
                parts.append(f"window={window_title}")
            if control_name:
                parts.append(f"control_name={control_name}")
            if automation_id:
                parts.append(f"automation_id={automation_id}")
            if control_type:
                parts.append(f"control_type={control_type}")
            if preview:
                parts.append(f"text={preview}")
            return "Desktop type (" + ", ".join(parts) + ")" if parts else "Desktop type"
        if name == "desktop_activate_window":
            window_title = (kwargs or {}).get("window_title")
            if window_title:
                return f"Activate window: {window_title}"
            return "Activate a window"
        if name == "desktop_send_hotkey":
            window_title = (kwargs or {}).get("window_title")
            hotkey = (kwargs or {}).get("hotkey")
            if window_title and hotkey:
                return f"Send hotkey {hotkey} to window: {window_title}"
            if hotkey:
                return f"Send hotkey: {hotkey}"
            return "Send a hotkey"
        if name == "file_write":
            path = (kwargs or {}).get("path")
            content = (kwargs or {}).get("content") or ""
            append = (kwargs or {}).get("append") is True
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if path:
                suffix = " (append)" if append else ""
                return f"Write {preview} to file: {path}{suffix}"
            return "Write to a file"
        if name == "file_move":
            src = (kwargs or {}).get("src")
            dst = (kwargs or {}).get("dst")
            overwrite = (kwargs or {}).get("overwrite") is True
            suffix = " (overwrite)" if overwrite else ""
            if src and dst:
                return f"Move: {src} -> {dst}{suffix}"
            return "Move a file/folder"
        if name == "file_copy":
            src = (kwargs or {}).get("src")
            dst = (kwargs or {}).get("dst")
            overwrite = (kwargs or {}).get("overwrite") is True
            suffix = " (overwrite)" if overwrite else ""
            if src and dst:
                return f"Copy: {src} -> {dst}{suffix}"
            return "Copy a file/folder"
        if name == "file_delete":
            path = (kwargs or {}).get("path")
            recursive = (kwargs or {}).get("recursive") is True
            suffix = " (recursive)" if recursive else ""
            if path:
                return f"Delete: {path}{suffix}"
            return "Delete a file/folder"
        if name == "file_mkdir":
            path = (kwargs or {}).get("path")
            if path:
                return f"Create folder: {path}"
            return "Create a folder"
        if name == "artifact_write":
            filename = (kwargs or {}).get("filename")
            content = (kwargs or {}).get("content") or ""
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if filename:
                return f"Write {preview} to artifact: {filename}"
            return f"Write {preview} to an artifact file"
        if name == "terminal_run":
            command = (kwargs or {}).get("command") or ""
            cwd = (kwargs or {}).get("cwd")
            preview = str(command)
            if isinstance(preview, str) and len(preview) > 90:
                preview = preview[:90].rstrip() + "…"
            if cwd:
                return f"Run terminal command (cwd={cwd}): {preview}"
            return f"Run terminal command: {preview}"
        if name == "notepad_write":
            filename = (kwargs or {}).get("filename")
            content = (kwargs or {}).get("content") or ""
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if filename:
                return f"Open Notepad, type {preview}, and save artifact: {filename}"
            return f"Open Notepad and type {preview}"
        return f"Run tool: {name}"

    def _has_vision_intent(self, query_lower: str, has_monitor_ctx: bool = False) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        file_nouns = ["file", "files", "folder", "folders", "directory", "directories"]
        file_verbs = ["create", "make", "new", "mkdir", "list", "show", "move", "copy", "delete", "remove", "rename"]
        if any(n in q for n in file_nouns) and any(v in q for v in file_verbs):
            return False

        strong_phrases = [
            "what do you see",
            "what am i looking at",
            "look at my screen",
            "on my screen",
            "describe the screen",
            "describe what's on",
        ]
        if any(p in q for p in strong_phrases):
            return True

        visual_nouns = [
            "video",
            "clip",
            "screen",
            "desktop",
            "monitor",
            "window",
            "tab",
            "page",
            "image",
            "picture",
            "photo",
            "screenshot",
        ]
        has_visual_noun = any(n in q for n in visual_nouns)

        deictic = ["this", "that", "here", "right here", "there"]
        has_deictic = any(d in q for d in deictic)

        visual_verbs = ["look", "see", "watch", "check", "show", "identify", "describe"]
        has_visual_verb = any(v in q for v in visual_verbs)

        if "check this out" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "look at this" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "watch this" in q and ("video" in q or "clip" in q or has_monitor_ctx):
            return True

        if ("what is this" in q or "what's this" in q or "what is that" in q or "what's that" in q) and (
            "video" in q or "clip" in q or "screen" in q or "desktop" in q or has_monitor_ctx
        ):
            return True

        if ("in this video" in q or "in the video" in q or "in this clip" in q or "in the clip" in q) and (
            has_deictic or has_visual_verb
        ):
            return True

        if has_visual_noun and (has_visual_verb or has_deictic):
            return True

        return False

    def _find_tool(self, query: str) -> Optional[Tool]:
        query_lower_full = (query or "").lower()
        query_main = self._strip_live_desktop_context(query)
        query_lower = query_main.lower()

        has_monitor_ctx = "live desktop context" in query_lower_full

        # If the UI attached live desktop context (monitor mode), prefer the vision model.
        if has_monitor_ctx and self._has_vision_intent(query_lower, has_monitor_ctx=True):
            for tool in self.tools:
                if tool.name == "vision_qa":
                    return tool
        for tool in self.tools:
            if tool.name.replace("_", " ") in query_lower:
                return tool
            if "search" in query_lower and tool.name == "web_search":
                return tool
            if ("youtube" in query_lower or "youtu.be" in query_lower or "youtube.com" in query_lower) and tool.name == "youtube_transcript":
                return tool
            if any(kw in query_lower for kw in ["browse", "read this site", "read this page", "summarize this site", "summarize this page", "open this site", "open this page"]) and tool.name == "browse_task":
                return tool
            if self._is_direct_time_question(query_lower) and tool.name == "get_system_time":
                return tool
            if any(kw in query_lower for kw in ["calculate", "compute", "evaluate", "solve", "plus", "minus", "multiply", "divide"]) and tool.name == "calculate":
                return tool
            if self._has_vision_intent(query_lower, has_monitor_ctx=has_monitor_ctx) and tool.name == "vision_qa":
                return tool
            if any(kw in query_lower for kw in ["screen", "what's on", "ocr", "read"]) and tool.name == "analyze_screen":
                return tool
        return None

    def _should_use_tool(self, query: str) -> Optional[Tool]:
        tool_indicators = {
            "live_web_search": [
                "right now",
                "currently",
                "today",
                "live",
                "score",
                "scores",
                "weather",
                "forecast",
                "price",
                "stock",
                "stocks",
                "bitcoin",
                "btc",
                "ethereum",
                "eth",
                "flight status",
                "traffic",
                "availability",
                "latest",
            ],
            "web_search": [
                "headlines",
                "current events",
                "top stories",
                "breaking news",
                "latest news",
                "recent news",
                "news about",
                "search",
                "look up",
                "find out",
                "updates on",
                "update on",
                "latest update",
            ],
            "get_system_time": ["what time is it", "time is it", "current time", "what date", "what's the date", "today's date", "todays date", "current date", "date today"],
            "calculate": ["calculate", "compute", "evaluate", "solve", "times", "equals"],
            "system_info": ["system info", "specs", "hardware", "gpu", "vram", "ram", "cpu", "my pc", "my computer", "my laptop"],
            "analyze_screen": ["screen", "what's on", "display", "visible", "ocr", "read what's"],
            "youtube_transcript": ["transcript", "caption", "captions", "subtitles", "youtube transcript"],
            "browse_task": ["browse", "read this site", "read this page", "summarize this site", "summarize this page", "open this site", "open this page"],
            "desktop_list_windows": ["list windows", "what windows", "open windows", "which windows"],
            "desktop_find_control": ["find control", "find button", "find textbox", "find text box", "find element"],
            "desktop_click": ["desktop click", "click in", "click on"],
            "desktop_type_text": ["desktop type", "type in", "type into", "enter text"],
            "desktop_activate_window": ["activate window", "focus window", "bring to front"],
            "desktop_send_hotkey": ["send hotkey", "press hotkey", "press ctrl", "press alt", "press win"],
            "file_list": ["list files", "list folder", "show files", "show folder", "list directory", "browse files"],
            "file_read": ["read file", "open file", "show file", "view file", "file contents"],
            "file_write": ["write file", "save file", "append file", "write to file"],
            "file_move": ["move file", "rename file", "move folder", "rename folder"],
            "file_copy": ["copy file", "copy folder", "duplicate file", "duplicate folder"],
            "file_delete": ["delete file", "remove file", "delete folder", "remove folder"],
            "file_mkdir": [
                "create folder",
                "create a folder",
                "create a new folder",
                "make folder",
                "make a folder",
                "new folder",
                "new folder called",
                "new folder named",
                "folder called",
                "folder named",
                "mkdir",
                "create directory",
                "create a directory",
                "make directory",
                "make a directory",
            ],
            "terminal_run": ["run command", "execute command", "terminal run", "run in terminal", "powershell:", "cmd:", "ps:"],
            "vision_qa": [
                "what am i looking at",
                "what do you see",
                "look at my screen",
                "on my screen",
                "on my desktop",
                "describe the screen",
                "describe what's on",
            ],
            "open_chrome": [
                "open chrome",
                "launch chrome",
                "start chrome",
                "open google chrome",
                "open browser",
                "launch browser",
            ],
            "open_application": [
                "open notepad",
                "launch notepad",
                "start notepad",
                "open calculator",
                "launch calculator",
                "open calc",
                "launch calc",
                "open paint",
                "launch paint",
                "open explorer",
                "open file explorer",
                "launch explorer",
                "open command prompt",
                "open cmd",
                "open powershell",
                "open terminal",
            ],
        }

        # Never let appended monitor OCR text trigger tools.
        query_main = self._strip_live_desktop_context(query)
        query_lower = query_main.lower()
        has_monitor_ctx = "live desktop context" in (query or "").lower()

        if self._is_direct_time_question(query_lower):
            for tool in self.tools:
                if tool.name == "get_system_time":
                    return tool

        if self._is_hardware_capability_query(query_lower):
            for tool in self.tools:
                if tool.name == "system_info":
                    return tool

        if self._is_schedule_time_query(query_lower):
            if self._playwright_enabled() and self._is_live_web_intent(query_lower):
                for tool in self.tools:
                    if tool.name == "live_web_search":
                        return tool
            for tool in self.tools:
                if tool.name == "web_search":
                    return tool

        if self._playwright_enabled() and self._is_live_web_intent(query_lower):
            for tool in self.tools:
                if tool.name == "live_web_search":
                    return tool

        if self._has_vision_intent(query_lower, has_monitor_ctx=has_monitor_ctx):
            for tool in self.tools:
                if tool.name == "vision_qa":
                    return tool

        yt_url = self._extract_youtube_url(query_main)
        if yt_url:
            for tool in self.tools:
                if tool.name == "youtube_transcript":
                    return tool

        creator_queries = self._creator_search_queries(query_main)
        if creator_queries:
            for tool in self.tools:
                if tool.name == "web_search":
                    return tool

        browse_url = self._extract_url(query_main)
        if browse_url and any(x in query_lower for x in tool_indicators["browse_task"]):
            for tool in self.tools:
                if tool.name == "browse_task":
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_list_windows"]):
            for tool in self.tools:
                if tool.name == "desktop_list_windows":
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_find_control"]):
            for tool in self.tools:
                if tool.name == "desktop_find_control":
                    return tool

        if ("click" in query_lower) and any(x in query_lower for x in ("window", "app", "desktop", " in ")):
            for tool in self.tools:
                if tool.name == "desktop_click":
                    return tool

        if ("type" in query_lower or "enter" in query_lower) and any(x in query_lower for x in ("window", "app", "desktop", " into ", " in ")):
            for tool in self.tools:
                if tool.name == "desktop_type_text":
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_activate_window"]):
            for tool in self.tools:
                if tool.name == "desktop_activate_window":
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_send_hotkey"]):
            for tool in self.tools:
                if tool.name == "desktop_send_hotkey":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_list"]):
            for tool in self.tools:
                if tool.name == "file_list":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_read"]):
            for tool in self.tools:
                if tool.name == "file_read":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_write"]):
            for tool in self.tools:
                if tool.name == "file_write":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_move"]):
            for tool in self.tools:
                if tool.name == "file_move":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_copy"]):
            for tool in self.tools:
                if tool.name == "file_copy":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_delete"]):
            for tool in self.tools:
                if tool.name == "file_delete":
                    return tool

        if any(x in query_lower for x in tool_indicators["file_mkdir"]):
            for tool in self.tools:
                if tool.name == "file_mkdir":
                    return tool

        if any(x in query_lower for x in tool_indicators.get("open_application") or []):
            for tool in self.tools:
                if tool.name == "open_application":
                    return tool

        if any(x in query_lower for x in tool_indicators["terminal_run"]):
            for tool in self.tools:
                if tool.name == "terminal_run":
                    return tool

        calc_keywords = tool_indicators["calculate"]
        has_calc_keyword = any(ind in query_lower for ind in calc_keywords)
        has_math_operator = bool(re.search(r"\d\s*[+\-*/^]\s*\d", query_lower))
        if has_calc_keyword or has_math_operator:
            for tool in self.tools:
                if tool.name == "calculate":
                    return tool

        for tool_name, indicators in tool_indicators.items():
            if any(ind in query_lower for ind in indicators):
                if tool_name == "live_web_search" and not self._playwright_enabled():
                    continue
                for tool in self.tools:
                    if tool.name == tool_name:
                        return tool
        return None

    def _infer_mkdir_path(self, user_input: str) -> str:
        s = (user_input or "").strip()
        low = s.lower()

        root_name = ""
        try:
            root_name = Path(getattr(config, "file_tool_root", "") or ".").expanduser().resolve().name.lower()
        except Exception:
            root_name = ""

        base = ""
        if re.search(r"\b(?:on|in)\s+(?:my\s+)?desktop\b", low):
            base = "." if root_name == "desktop" else "Desktop"

        name = ""
        m = re.search(r"\b(?:called|named|name\s+it|call\s+it)\s+[\"']([^\"']{1,80})[\"']", s, flags=re.IGNORECASE)
        if m:
            name = (m.group(1) or "").strip()
        if not name:
            m = re.search(r"\b(?:called|named|name\s+it|call\s+it)\s+([^\n\r]{1,120})", s, flags=re.IGNORECASE)
            if m:
                tail = (m.group(1) or "").strip()
                tail = re.split(r"\b(?:in|on|at|under|inside|within|and|then)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0].strip()
                name = tail.strip("\"'()[]{}<> ")
        if not name:
            m = re.search(
                r"\b(?:create|make)\s+(?:a\s+|new\s+)?(?:folder|directory)\s+[\"']([^\"']{1,80})[\"']",
                s,
                flags=re.IGNORECASE,
            )
            if m:
                name = (m.group(1) or "").strip()

        name = name.strip().rstrip(".,;!?")
        if not name:
            return ""

        if base:
            return f"{base}/{name}" if base != "." else name
        return name

    def _extract_url(self, user_input: str) -> Optional[str]:
        text = (user_input or "").strip()
        m = re.search(r"(https?://\S+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).rstrip(").,;\"]")

        low = text.lower()
        phrases = [
            "go to ",
            "visit ",
            "navigate to ",
        ]
        for ph in phrases:
            idx = low.find(ph)
            if idx == -1:
                continue
            tail = text[idx + len(ph):].strip()
            if not tail:
                continue
            # stop at conjunctions like "and" to avoid capturing the whole sentence
            tail = re.split(r"\b(and|then)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if not tail:
                continue
            token = tail.split()[0].strip("\"'()[]{}<>")
            token = token.rstrip(".,;!?")
            if token:
                return token

        m2 = re.search(
            r"\b(?:open|launch|start)\s+(?:google\s+)?chrome\b(?:\s+(?:and\s+)?)?(?:go\s+to\s+|visit\s+|navigate\s+to\s+)?(?P<target>\S+)",
            text,
            flags=re.IGNORECASE,
        )
        if m2:
            token = (m2.group("target") or "").strip("\"'()[]{}<>")
            token = token.rstrip(".,;!?")
            if token.lower() in ("chrome", "browser"):
                return None
            return token

        return None

    def _extract_youtube_url(self, user_input: str) -> Optional[str]:
        text = (user_input or "").strip()
        m = re.search(r"(https?://\S+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if not m:
            return None
        url = m.group(1).rstrip(").,;\"]")
        low = url.lower()
        if "youtube.com" in low or "youtu.be" in low:
            if url.startswith("www."):
                return "https://" + url
            return url
        return None

    def _emit_tool_start(self, callbacks: Optional[list], name: str, input_str: str, run_id: str) -> None:
        if not callbacks:
            return
        serialized = {"name": name}
        for cb in callbacks:
            fn = getattr(cb, "on_tool_start", None)
            if callable(fn):
                try:
                    fn(serialized, input_str, run_id)
                except Exception:
                    pass

    def _emit_tool_end(self, callbacks: Optional[list], output: str, run_id: str) -> None:
        if not callbacks:
            return
        for cb in callbacks:
            fn = getattr(cb, "on_tool_end", None)
            if callable(fn):
                try:
                    fn(output, run_id)
                except Exception:
                    pass

    def _emit_tool_error(self, callbacks: Optional[list], error: BaseException, run_id: str) -> None:
        if not callbacks:
            return
        for cb in callbacks:
            fn = getattr(cb, "on_tool_error", None)
            if callable(fn):
                try:
                    fn(error, run_id)
                except Exception:
                    pass

    def _extract_calc_expression(self, user_input: str) -> str:
        text = (user_input or "").strip()
        lower = text.lower()
        for prefix in ("calculate", "compute", "what is", "solve"):
            if lower.startswith(prefix):
                text = text[len(prefix):].strip(" :,-")
                break
        return text

    def _parse_kv_args(self, text: str) -> Dict[str, Any]:
        s = text or ""
        pairs = {}
        for m in re.finditer(
            r"\b(filter|window_title|window|app|title|control_name|control|name|control_type|automation_id|auto_id|id|text|value|hotkey|keys|combo|append|path|file|filepath|filename|dir|folder|limit|max_chars|src|source|from|dst|dest|destination|to|overwrite|recursive|parents|exist_ok|cwd|workdir|timeout|command|cmd|powershell|ps)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)",
            s,
            flags=re.IGNORECASE,
        ):
            key = (m.group(1) or "").lower()
            raw = (m.group(2) or "").strip()
            if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                raw = raw[1:-1]
            if key in {"window", "app", "title"}:
                key = "window_title"
            if key in {"control", "name"}:
                key = "control_name"
            if key in {"auto_id", "id"}:
                key = "automation_id"
            if key in {"value"}:
                key = "text"
            if key in {"keys", "combo"}:
                key = "hotkey"
            if key in {"file", "filepath", "filename"}:
                key = "path"
            if key in {"source", "from"}:
                key = "src"
            if key in {"dest", "destination", "to"}:
                key = "dst"
            if key in {"workdir"}:
                key = "cwd"
            if key in {"cmd", "powershell", "ps"}:
                key = "command"
            if key in {"append", "overwrite", "recursive", "parents", "exist_ok"}:
                pairs[key] = (raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}
            else:
                pairs[key] = raw
        return pairs

    def _extract_window_title_hint(self, user_input: str) -> str:
        s = user_input or ""
        m = re.search(r"\b(?:in|on)\s+(?:the\s+)?(?:window|app)?\s*[\"']([^\"']+)[\"']", s, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
        m2 = re.search(r"\b(?:window|app)\s*[\"']([^\"']+)[\"']", s, flags=re.IGNORECASE)
        if m2:
            return (m2.group(1) or "").strip()
        return ""

    def _extract_list_windows_filter_hint(self, user_input: str) -> str:
        s = (user_input or "").strip()
        low = s.lower()
        if "list windows" in low:
            tail = s[low.find("list windows") + len("list windows") :].strip()
            if tail:
                tail = tail.strip(" :,-\"'")
                if tail and len(tail) <= 80:
                    return tail
        return ""

    def _extract_click_control_name_hint(self, user_input: str) -> str:
        s = user_input or ""
        m = re.search(r"\bclick\s+[\"']([^\"']+)[\"']", s, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
        m2 = re.search(r"\bclick\s+([A-Za-z0-9][A-Za-z0-9 _-]{0,40})\b", s, flags=re.IGNORECASE)
        if m2:
            cand = (m2.group(1) or "").strip()
            if cand.lower() not in {"in", "on", "the", "a", "an"}:
                return cand
        return ""

    def _extract_type_text_hint(self, user_input: str) -> str:
        s = user_input or ""
        m = re.search(r"\b(?:type|enter)\s+[\"']([^\"']+)[\"']", s, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
        m2 = re.search(r"\b(?:type|enter)\s+(.+?)\s+\b(?:into|in)\b", s, flags=re.IGNORECASE)
        if m2:
            cand = (m2.group(1) or "").strip()
            cand = cand.strip(" :,-")
            if cand and len(cand) <= 200:
                return cand
        return ""

    def _extract_hotkey_hint(self, user_input: str) -> str:
        s = user_input or ""
        m = re.search(r"\b(?:hotkey|keys?)\s*[:=]\s*([A-Za-z0-9+ -]{2,})", s, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()

        m2 = re.search(r"\b(ctrl|alt|win|shift)\s*(?:\+|\s)\s*([a-z0-9]{1,3})\b", s, flags=re.IGNORECASE)
        if m2:
            return f"{(m2.group(1) or '').strip()}+{(m2.group(2) or '').strip()}"

        return ""

    def _infer_control_type(self, user_input: str, purpose: str) -> str:
        q = (user_input or "").lower()
        if purpose == "click":
            if "button" in q or any(w in q for w in [" ok", " cancel", " submit", " save", " next", " back", " close"]):
                return "Button"
            if "checkbox" in q or "check box" in q:
                return "CheckBox"
            if "tab" in q:
                return "TabItem"
            if "menu" in q:
                return "MenuItem"
        if purpose == "type":
            if any(w in q for w in ["textbox", "text box", "input", "field", "address bar", "search bar", "search box"]):
                return "Edit"
            return "Edit"
        return ""

    def _extract_search_query(self, user_input: str) -> str:
        text = (user_input or "").strip()
        lower = text.lower()
        for prefix in ("search", "look up", "find"):
            if lower.startswith(prefix):
                text = text[len(prefix):].strip(" :,-")
                break
        return text

    def _extract_social_handle(self, user_input: str) -> str:
        text = user_input or ""
        match = re.search(r"(?<![A-Za-z0-9])@([A-Za-z0-9_\.]{2,})", text)
        if not match:
            return ""
        return match.group(1) or ""

    def _creator_search_queries(self, user_input: str) -> list[str]:
        text = (user_input or "").strip()
        lower = text.lower()
        handle = self._extract_social_handle(text)
        if not handle:
            return []
        trigger_terms = (
            "youtube",
            "watching",
            "video",
            "channel",
            "creator",
            "stream",
            "who is",
            "who's",
            "tell me about",
            "do you know",
            "what do you know",
            "info on",
        )
        if not any(term in lower for term in trigger_terms):
            return []
        base = handle.lstrip("@").strip()
        if not base:
            return []
        return [f"{base} youtube channel", f"{base} youtube creator", f"{base} creator"]

    def _extract_browse_task(self, user_input: str) -> str:
        text = (user_input or "").strip()
        m = re.search(r"(https?://\S+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if m:
            text = (text[: m.start()] + " " + text[m.end() :]).strip()
        text = re.sub(r"^(browse|open|visit|go to|navigate to|check)\b", "", text, flags=re.IGNORECASE).strip(" :,-")
        return text.strip()

    def _strip_links_and_urls(self, text: str) -> str:
        t = text or ""
        t = re.sub(r"\[([^\]]+)\]\((https?://[^\)\s]+)\)", r"\1", t)
        t = re.sub(r"https?://\S+", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _clamp_web_summary(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""

        t = self._strip_links_and_urls(t)
        t = re.sub(r"\s+", " ", t).strip()
        t = re.sub(r"\n{3,}", "\n\n", t)

        raw_lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        filtered_lines = []
        for ln in raw_lines:
            low = ln.lower()
            if low.startswith("note") or low.startswith("disclaimer"):
                continue
            if "may not be independently verified" in low:
                continue
            if low.startswith("for the most reliable"):
                continue
            if low.startswith("let me know if"):
                continue
            filtered_lines.append(ln)

        bullet_like = []
        for ln in filtered_lines:
            if re.match(r"^(-|\*|\d+\.)\s+", ln):
                bullet_like.append(re.sub(r"^(-|\*|\d+\.)\s+", "", ln).strip())

        def smart_trunc(s: str, n: int) -> tuple[str, bool]:
            s2 = re.sub(r"\s+", " ", (s or "").strip())
            if len(s2) <= n:
                return s2, False

            suffix = "…"
            limit = max(0, n - len(suffix))
            if limit <= 0:
                return suffix, True

            head = s2[:limit]
            for sep in (". ", "; ", ": ", ", ", " "):
                cut = head.rfind(sep)
                if cut >= 30:
                    if sep == " ":
                        trimmed = head[:cut]
                    else:
                        trimmed = head[: cut + (1 if sep.strip() == "." else 0)]
                        trimmed = trimmed.rstrip()
                    trimmed = trimmed.rstrip(" ,;:")
                    return trimmed + suffix, True

            cut = head.rfind(" ")
            if cut >= 1:
                return head[:cut].rstrip(" ,;:") + suffix, True
            return head.rstrip(" ,;:") + suffix, True

        truncated_any = False

        if bullet_like:
            items: list[str] = []
            for x in bullet_like[:3]:
                if not x.strip():
                    continue
                clipped, was_trunc = smart_trunc(x, 220)
                truncated_any = truncated_any or was_trunc
                items.append(clipped)
            out = "\n".join([f"- {x}" for x in items])
        else:
            joined = " ".join(filtered_lines)
            clipped, was_trunc = smart_trunc(joined, 520)
            truncated_any = truncated_any or was_trunc
            out = clipped

        out = out.strip()
        if not out:
            clipped, _ = smart_trunc(t, 520)
            out = clipped

        if truncated_any:
            out = out.rstrip()
            out = f"{out}\n\nMore in the Research panel."

        return out

    def process_query(
        self,
        user_input: str,
        include_memory: bool = True,
        callbacks: Optional[list] = None,
        thread_id: Optional[str] = None,
    ) -> tuple:
        try:
            logger.info(f"Processing query: {user_input[:100]}...")
            self._last_memory_thread_id = thread_id if include_memory else None
            self._last_memory_mode = None

            if self._pending_action is not None:
                pending = self._pending_action
                if self._is_confirm_text(user_input):
                    tool_name = pending.get("tool") or ""
                    kwargs = pending.get("kwargs") or {}
                    original_input = str(pending.get("original_input") or "")
                    display = self._format_pending_action(pending)
                    self._pending_action = None

                    tool = next((t for t in self.tools if t.name == tool_name), None)
                    if tool is None:
                        response_text = f"Pending action failed: tool '{tool_name}' is unavailable."
                    elif not self._action_allowed(tool_name):
                        response_text = "System actions are disabled."
                    else:
                        run_id = str(uuid.uuid4())
                        self._emit_tool_start(callbacks, tool.name, str(pending.get("original_input") or ""), run_id)
                        try:
                            tool_output = tool.invoke(**kwargs)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            if tool.name == "browse_task":
                                prompt = (
                                    "You are Echo Speak, a conversational assistant. "
                                    "Use the following page content to answer the user's request. "
                                    "Be concise and conversational. Use bullets only if the user asked for a list. "
                                    "Do NOT include URLs.\n\n"
                                    f"User request: {original_input}\n\n"
                                    f"Page content:\n{tool_output}\n\n"
                                    "Answer:"
                                )
                                response_text = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))
                            else:
                                response_text = str(tool_output)
                        except Exception as exc:
                            self._emit_tool_error(callbacks, exc, run_id)
                            response_text = f"Action failed: {str(exc)}"

                    self._last_tts_text = self._clamp_tts_text(response_text)
                    self._record_turn(user_input, response_text)
                    return response_text, True

                if self._is_cancel_text(user_input):
                    display = self._format_pending_action(pending)
                    self._pending_action = None
                    response_text = f"Canceled: {display}."
                    self._last_tts_text = self._clamp_tts_text(response_text)
                    self._record_turn(user_input, response_text)
                    return response_text, True

                display = self._format_pending_action(pending)
                response_text = f"I have a pending action: {display}. Reply 'confirm' to proceed or 'cancel' to abort."
                self._last_tts_text = self._clamp_tts_text(response_text)
                self._record_turn(user_input, response_text)
                return response_text, True

            if self._pending_detail is not None:
                if self._is_detail_request(user_input):
                    detail = self._pending_detail
                    self._pending_detail = None
                    response_text = (detail or {}).get("full") or (detail or {}).get("detail") or ""
                    if not response_text:
                        response_text = "I don't have more details yet."
                    self._last_tts_text = self._clamp_tts_text(response_text)
                    self._record_turn(user_input, response_text)
                    return response_text, True
                self._pending_detail = None

            low_notepad = self._strip_live_desktop_context(user_input).lower()
            if "notepad" in low_notepad and ("write" in low_notepad or "type" in low_notepad):
                if not self._action_allowed("notepad_write"):
                    response_text = (
                        "To do that, enable system actions and desktop automation: "
                        "set ENABLE_SYSTEM_ACTIONS=true, ALLOW_OPEN_APPLICATION=true, ALLOW_DESKTOP_AUTOMATION=true, "
                        "ALLOW_FILE_WRITE=true, and add notepad to OPEN_APPLICATION_ALLOWLIST, then restart the API."
                    )
                    self._last_tts_text = self._clamp_tts_text(response_text)
                    self._record_turn(user_input, response_text)
                    return response_text, True

                filename = "notes.txt"
                if "python" in low_notepad and ("script" in low_notepad or ".py" in low_notepad):
                    filename = "script.py"
                elif "poem" in low_notepad:
                    filename = "poem.txt"

                prompt = (
                    "Write exactly what the user asked for as plain text only. "
                    "No explanations, no preamble, no markdown.\n\n"
                    f"User request: {user_input}\n\n"
                    "Content:"
                )
                content = str(self.llm_wrapper.invoke(prompt) or "").strip()
                if not content:
                    content = "(empty)"

                preview = content
                if len(preview) > 300:
                    preview = preview[:300].rstrip() + "…"
                preview_msg = f"Will open Notepad, type the content, and save artifact: {filename}.\n\nPreview:\n{preview}"

                self._pending_action = {
                    "tool": "notepad_write",
                    "kwargs": {"content": content, "filename": filename},
                    "original_input": user_input,
                }
                response_text = self._action_confirm_message(preview_msg, self._pending_action, user_input)
                self._last_tts_text = self._clamp_tts_text(response_text)
                self._record_turn(user_input, response_text)
                return response_text, True

            pre_tool = self._should_use_tool(user_input)
            if pre_tool is not None and pre_tool.name in {"open_chrome", "open_application", "file_list", "file_read", "terminal_run", "file_move", "file_copy", "file_delete", "file_mkdir"}:
                run_id = str(uuid.uuid4())
                self._emit_tool_start(callbacks, pre_tool.name, user_input, run_id)
                try:
                    if pre_tool.name == "file_list":
                        kv = self._parse_kv_args(user_input)
                        path = kv.get("path") if isinstance(kv.get("path"), str) else ""
                        if not path:
                            path = kv.get("dir") if isinstance(kv.get("dir"), str) else ""
                        if not path:
                            path = kv.get("folder") if isinstance(kv.get("folder"), str) else ""
                        if not path:
                            path = "."

                        limit = kv.get("limit")
                        try:
                            limit_i = int(limit) if limit is not None else 50
                        except Exception:
                            limit_i = 50
                        tool_output = pre_tool.invoke(path=path, limit=limit_i)
                        self._emit_tool_end(callbacks, tool_output, run_id)
                        response_text = str(tool_output)
                    elif pre_tool.name == "file_read":
                        kv = self._parse_kv_args(user_input)
                        path = kv.get("path") if isinstance(kv.get("path"), str) else ""
                        if not path:
                            path = kv.get("file") if isinstance(kv.get("file"), str) else ""
                        if not path:
                            path = kv.get("filepath") if isinstance(kv.get("filepath"), str) else ""
                        if not path:
                            self._emit_tool_end(callbacks, "Missing file path.", run_id)
                            response_text = "Please specify a file path (e.g., path=\"config.py\")."
                        else:
                            max_chars = kv.get("max_chars")
                            try:
                                max_chars_i = int(max_chars) if max_chars is not None else 4000
                            except Exception:
                                max_chars_i = 4000
                            tool_output = pre_tool.invoke(path=path, max_chars=max_chars_i)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = str(tool_output)
                    elif pre_tool.name == "open_chrome":
                        if not self._action_allowed(pre_tool.name):
                            self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                            response_text = "System actions are disabled."
                        else:
                            url = self._extract_url(user_input)
                            self._emit_tool_end(callbacks, "Action requires confirmation.", run_id)
                            self._pending_action = {
                                "tool": pre_tool.name,
                                "kwargs": {"url": url} if url else {},
                                "original_input": user_input,
                            }
                            response_text = self._action_confirm_message("", self._pending_action, user_input)
                    elif pre_tool.name == "open_application":
                        if not self._action_allowed(pre_tool.name):
                            self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                            response_text = "System actions are disabled."
                        else:
                            app = "notepad"
                            low = (user_input or "").lower()
                            if "calculator" in low or "calc" in low:
                                app = "calculator"
                            elif "paint" in low:
                                app = "paint"
                            elif "explorer" in low or "file explorer" in low:
                                app = "explorer"
                            self._emit_tool_end(callbacks, "Action requires confirmation.", run_id)
                            self._pending_action = {
                                "tool": pre_tool.name,
                                "kwargs": {"app": app},
                                "original_input": user_input,
                            }
                            response_text = self._action_confirm_message("", self._pending_action, user_input)
                    elif pre_tool.name == "terminal_run":
                        if not self._action_allowed(pre_tool.name):
                            self._emit_tool_end(callbacks, "Terminal disabled.", run_id)
                            response_text = "Terminal commands are disabled."
                        else:
                            kv = self._parse_kv_args(user_input)
                            cmd_val = kv.get("command") or kv.get("cmd") or kv.get("powershell") or kv.get("ps")
                            if not isinstance(cmd_val, str) or not cmd_val.strip():
                                m = re.search(r"`([^`]{1,20000})`", user_input)
                                if not m:
                                    m = re.search(r"\"([^\"]{1,20000})\"", user_input)
                                if not m:
                                    m = re.search(r"\b(?:powershell|ps|cmd)\s*:\s*(.+)$", user_input, flags=re.IGNORECASE)
                                cmd_val = m.group(1).strip() if m else ""
                            cwd_val = kv.get("cwd") or kv.get("dir") or kv.get("path") or kv.get("workdir")
                            if not isinstance(cwd_val, str) or not cwd_val.strip():
                                cwd_val = "."
                            timeout_val = kv.get("timeout")
                            try:
                                timeout_i = int(timeout_val) if timeout_val is not None else None
                            except Exception:
                                timeout_i = None

                            if not str(cmd_val).strip():
                                self._emit_tool_end(callbacks, "Missing command.", run_id)
                                response_text = "Please specify a command (e.g., command=\"dir\" or put it in quotes/backticks)."
                            else:
                                exec_kwargs: dict[str, Any] = {"command": str(cmd_val).strip(), "cwd": str(cwd_val).strip()}
                                if timeout_i is not None:
                                    exec_kwargs["timeout"] = timeout_i
                                preview = f"Run terminal command in {cwd_val}: {str(cmd_val).strip()}"
                                self._emit_tool_end(callbacks, preview, run_id)
                                self._pending_action = {
                                    "tool": pre_tool.name,
                                    "kwargs": exec_kwargs,
                                    "original_input": user_input,
                                }
                                response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                    elif pre_tool.name in {"file_move", "file_copy", "file_delete", "file_mkdir"}:
                        if not self._action_allowed(pre_tool.name):
                            self._emit_tool_end(callbacks, "File operations disabled.", run_id)
                            response_text = "File operations are disabled."
                        else:
                            kv = self._parse_kv_args(user_input)
                            if pre_tool.name in {"file_move", "file_copy"}:
                                src = kv.get("src") or kv.get("source") or kv.get("from")
                                dst = kv.get("dst") or kv.get("dest") or kv.get("destination") or kv.get("to")
                                overwrite = kv.get("overwrite") is True
                                if not isinstance(src, str) or not str(src).strip() or not isinstance(dst, str) or not str(dst).strip():
                                    self._emit_tool_end(callbacks, "Missing src/dst.", run_id)
                                    response_text = "Please specify src=... and dst=... (e.g., src=\"a.txt\" dst=\"b.txt\")."
                                else:
                                    exec_kwargs = {"src": str(src).strip(), "dst": str(dst).strip(), "overwrite": overwrite}
                                    verb = "Move" if pre_tool.name == "file_move" else "Copy"
                                    preview = verb + f" {src} -> {dst}" + (" (overwrite)" if overwrite else "")
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": pre_tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                            elif pre_tool.name == "file_delete":
                                path = kv.get("path") or kv.get("file") or kv.get("filepath") or kv.get("dir") or kv.get("folder")
                                recursive = kv.get("recursive") is True
                                if not isinstance(path, str) or not str(path).strip():
                                    self._emit_tool_end(callbacks, "Missing path.", run_id)
                                    response_text = "Please specify a path to delete (path=...)."
                                else:
                                    exec_kwargs = {"path": str(path).strip(), "recursive": recursive}
                                    preview = f"Delete {path}" + (" (recursive)" if recursive else "")
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": pre_tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                            elif pre_tool.name == "file_mkdir":
                                path = kv.get("path") or kv.get("dir") or kv.get("folder")
                                if not isinstance(path, str) or not str(path).strip():
                                    inferred = self._infer_mkdir_path(user_input)
                                    path = inferred
                                if not isinstance(path, str) or not str(path).strip():
                                    self._emit_tool_end(callbacks, "Missing path.", run_id)
                                    response_text = "What should the folder be named? (Example: create a folder called \"test\" on my desktop)"
                                else:
                                    exec_kwargs = {"path": str(path).strip(), "parents": True, "exist_ok": True}
                                    preview = f"Create folder: {path}"
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": pre_tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                    else:
                        self._emit_tool_end(callbacks, "Tool not supported.", run_id)
                        response_text = "Tool not supported."
                except Exception as exc:
                    self._emit_tool_error(callbacks, exc, run_id)
                    response_text = f"Tool failed: {str(exc)}"

                self._last_tts_text = self._clamp_tts_text(response_text)
                self._record_turn(user_input, response_text)
                return response_text, True
            memory_context = self.memory.get_conversation_context(
                user_input,
                thread_id=thread_id,
            ) if include_memory else ""
            doc_context, doc_sources = self._get_document_context(user_input) if include_memory else ("", [])
            self._last_doc_sources = doc_sources or []
            context = self._build_context_block(memory_context, doc_context) if include_memory else ""
            chat_history = self._history_as_messages() if include_memory else []
            graph_thread_id = thread_id if include_memory else None
            allowed_tool_names = self._allowed_lc_tool_names(user_input)
            time_context = ""
            if self._needs_time_context(self._strip_live_desktop_context(user_input).lower()):
                time_tool = next((t for t in self.tools if t.name == "get_system_time"), None)
                if time_tool is not None:
                    run_id = str(uuid.uuid4())
                    self._emit_tool_start(callbacks, time_tool.name, "current time", run_id)
                    try:
                        time_context = str(time_tool.invoke())
                        self._emit_tool_end(callbacks, time_context, run_id)
                    except Exception as exc:
                        self._emit_tool_error(callbacks, exc, run_id)
                        time_context = ""
            if time_context:
                if context:
                    context = f"Current system time: {time_context}\n\n{context}"
                else:
                    context = f"Current system time: {time_context}"

            schedule_low = self._strip_live_desktop_context(user_input).lower().strip()
            if self._is_schedule_time_query(schedule_low) or self._is_next_upcoming_schedule_query(schedule_low):
                tool = next((t for t in self.tools if t.name == "web_search"), None)
                if tool is not None:
                    run_id = str(uuid.uuid4())
                    qtext = self._extract_search_query(user_input)
                    self._emit_tool_start(callbacks, tool.name, qtext, run_id)
                    try:
                        tool_output = tool.invoke(q=qtext)
                        self._emit_tool_end(callbacks, tool_output, run_id)
                    except Exception as exc:
                        self._emit_tool_error(callbacks, exc, run_id)
                        tool_output = ""

                    time_note = f"Current system time: {time_context}\n\n" if time_context else ""
                    prompt = (
                        "You are Echo Speak, a conversational assistant. "
                        "Use the following web search results to answer the user's question. "
                        "Be concise and conversational. Use bullets only if the user asked for a list or if a list is clearly the best format. "
                        "Do NOT include URLs or markdown links. Do NOT cite sources in the chat reply. "
                        "IMPORTANT: For 'next'/'upcoming' schedule questions, do NOT answer with a date earlier than today. "
                        "If you can't confirm the next upcoming event, say so and ask a clarifying question.\n\n"
                        f"{time_note}User question: {user_input}\n\n"
                        f"Search results:\n{tool_output}\n\n"
                        "Answer:"
                    )
                    response_text = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))
                    response_text = self._maybe_correct_past_schedule_answer(user_input, response_text, time_context, callbacks)

                    self._pending_detail = None
                    self._last_tts_text = self._select_tts_text(user_input, response_text)
                    self._record_turn(user_input, response_text)
                    logger.info(f"Response generated: {response_text[:100]}...")
                    return response_text, True

            response_text = ""
            if self.graph_agent is not None:
                try:
                    system_prompt = self._system_prompt_with_context(context)
                    self._graph_system_prompt = system_prompt
                    graph = self._get_langgraph_agent_for_toolset(allowed_tool_names) if allowed_tool_names else None
                    if graph is None:
                        graph = self.graph_agent
                    if self._graph_pre_model_hook:
                        if graph_thread_id:
                            messages = [HumanMessage(content=user_input)]
                        else:
                            messages = [*chat_history, HumanMessage(content=user_input)]
                    else:
                        base = [SystemMessage(content=system_prompt)]
                        if graph_thread_id:
                            messages = [*base, HumanMessage(content=user_input)]
                        else:
                            messages = [*base, *chat_history, HumanMessage(content=user_input)]
                    result = self._invoke_langgraph(graph, messages, callbacks, thread_id=graph_thread_id)
                    response_text = self._extract_graph_response(result)
                except Exception as exc:
                    logger.warning(f"LangGraph agent failed; falling back to AgentExecutor: {exc}")

            if not response_text and self.agent_executor is not None:
                try:
                    executor = self._get_tool_calling_executor_for_toolset(allowed_tool_names) if allowed_tool_names else None
                    if executor is None:
                        executor = self.agent_executor
                    inputs = {
                        "input": user_input,
                        "context": context,
                        "chat_history": chat_history,
                    }

                    result = self._invoke_executor(executor, inputs, callbacks)
                    response_text = (result or {}).get("output") or ""
                except Exception as exc:
                    logger.warning(f"Agent executor failed; falling back to direct LLM: {exc}")

            if not response_text and self.fallback_executor is not None:
                try:
                    prefix = f"Context (memory + docs, may be empty):\n{context}\n\n" if context else ""
                    merged_input = f"{prefix}{user_input}" if prefix else user_input
                    result = self._invoke_executor(self.fallback_executor, {"input": merged_input}, callbacks)
                    response_text = (result or {}).get("output") or (result or {}).get("text") or ""
                except Exception as exc:
                    logger.warning(f"Fallback agent executor failed; falling back to direct LLM: {exc}")

            if not response_text:
                tool = self._should_use_tool(user_input)
                if tool is not None:
                    run_id = str(uuid.uuid4())
                    self._emit_tool_start(callbacks, tool.name, user_input, run_id)
                    try:
                        if tool.name == "live_web_search":
                            qtext = self._extract_search_query(user_input)
                            tool_output = tool.invoke(q=qtext)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            time_note = f"Current system time: {time_context}\n\n" if time_context else ""
                            prompt = (
                                "You are Echo Speak, a conversational assistant. "
                                "Use the following live browser output to answer the user's question. "
                                "Be concise and conversational. Use bullets only if the user asked for a list or if a list is clearly the best format. "
                                "Do NOT include URLs or markdown links. Do NOT cite sources in the chat reply. "
                                "If the content is insufficient, say so and ask a clarifying question.\n\n"
                                f"{time_note}User question: {user_input}\n\n"
                                f"Live browser output:\n{tool_output}\n\n"
                                "Answer:"
                            )
                            response_text = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))

                            decision = self._reflect_web_tool_decision(user_input, tool.name, str(tool_output), response_text)
                            if str(decision.get("action") or "accept") == "retry":
                                fallback_tool = next((t for t in self.tools if t.name == "web_search"), None)
                                if fallback_tool is not None:
                                    retry_id = str(uuid.uuid4())
                                    self._emit_tool_start(callbacks, fallback_tool.name, user_input, retry_id)
                                    retry_output = fallback_tool.invoke(q=qtext)
                                    self._emit_tool_end(callbacks, retry_output, retry_id)
                                    time_note = f"Current system time: {time_context}\n\n" if time_context else ""
                                    retry_prompt = (
                                        "You are Echo Speak, a conversational assistant. "
                                        "Use the following web search results to answer the user's question. "
                                        "Be concise and conversational. Do NOT include URLs.\n\n"
                                        f"{time_note}User question: {user_input}\n\n"
                                        f"Search results:\n{retry_output}\n\n"
                                        "Answer:"
                                    )
                                    response_text = self._clamp_web_summary(self.llm_wrapper.invoke(retry_prompt))
                            if str(decision.get("action") or "accept") == "ask_clarifying":
                                cq = str(decision.get("clarifying_question") or "").strip()
                                if cq:
                                    response_text = cq
                        elif tool.name == "web_search":
                            creator_queries = self._creator_search_queries(user_input)
                            if creator_queries:
                                qtext = " OR ".join(creator_queries)
                            else:
                                qtext = self._extract_search_query(user_input)
                            tool_output = tool.invoke(q=qtext)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            time_note = f"Current system time: {time_context}\n\n" if time_context else ""
                            prompt = (
                                "You are Echo Speak, a conversational assistant. "
                                "Use the following web search results to answer the user's question. "
                                "Be concise and conversational. Use bullets only if the user asked for a list or if a list is clearly the best format. "
                                "Do NOT include URLs or markdown links. Do NOT cite sources in the chat reply. "
                                "If the user asks for sources, say they are available in the Research panel. "
                                "If results are insufficient, say so and suggest a better query.\n\n"
                                f"{time_note}User question: {user_input}\n\n"
                                f"Search results:\n{tool_output}\n\n"
                                "Answer:"
                            )
                            response_text = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))

                            decision = self._reflect_web_tool_decision(user_input, tool.name, str(tool_output), response_text)
                            if str(decision.get("action") or "accept") == "retry" and self._playwright_enabled() and self._is_live_web_intent(self._strip_live_desktop_context(user_input).lower()):
                                live_tool = next((t for t in self.tools if t.name == "live_web_search"), None)
                                if live_tool is not None:
                                    retry_id = str(uuid.uuid4())
                                    self._emit_tool_start(callbacks, live_tool.name, user_input, retry_id)
                                    retry_output = live_tool.invoke(q=qtext)
                                    self._emit_tool_end(callbacks, retry_output, retry_id)
                                    time_note = f"Current system time: {time_context}\n\n" if time_context else ""
                                    retry_prompt = (
                                        "You are Echo Speak, a conversational assistant. "
                                        "Use the following live browser output to answer the user's question. "
                                        "Be concise and conversational. Do NOT include URLs.\n\n"
                                        f"{time_note}User question: {user_input}\n\n"
                                        f"Live browser output:\n{retry_output}\n\n"
                                        "Answer:"
                                    )
                                    response_text = self._clamp_web_summary(self.llm_wrapper.invoke(retry_prompt))
                            if str(decision.get("action") or "accept") == "ask_clarifying":
                                cq = str(decision.get("clarifying_question") or "").strip()
                                if cq:
                                    response_text = cq
                        elif tool.name == "get_system_time":
                            tool_output = tool.invoke()
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = f"Current system time: {tool_output}"
                        elif tool.name == "calculate":
                            expr = self._extract_calc_expression(user_input)
                            tool_output = tool.invoke(e=expr)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = f"Result: {tool_output}"
                        elif tool.name == "system_info":
                            tool_output = tool.invoke()
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            prompt = (
                                "You are Echo Speak, a conversational assistant. "
                                "Use the system info below to answer the user's question about model compatibility or performance. "
                                "Be honest about uncertainty. If you need model requirements, say so and suggest using a smaller quant or checking VRAM needs. "
                                "Be concise and helpful.\n\n"
                                f"System info:\n{tool_output}\n\n"
                                f"User question: {user_input}\n\n"
                                "Answer:"
                            )
                            response_text = self.llm_wrapper.invoke(prompt)
                        elif tool.name == "youtube_transcript":
                            yt_url = self._extract_youtube_url(user_input)
                            if not yt_url:
                                self._emit_tool_end(callbacks, "No YouTube URL found.", run_id)
                                response_text = "Please provide a YouTube URL."
                            else:
                                tool_output = tool.invoke(url=yt_url)
                                self._emit_tool_end(callbacks, tool_output, run_id)
                                prompt = (
                                    "You are Echo Speak, a conversational assistant. "
                                    "Summarize the following YouTube transcript in a short, natural paragraph. "
                                    "Use bullets only if the user asked for a list. Do NOT include timestamps.\n\n"
                                    f"Transcript:\n{tool_output}\n\n"
                                    "Summary:"
                                )
                                response_text = self._clamp_web_summary(self.llm_wrapper.invoke(prompt))
                        elif tool.name == "browse_task":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                url = self._extract_url(user_input)
                                task = self._extract_browse_task(user_input)
                                if not url:
                                    self._emit_tool_end(callbacks, "No URL provided.", run_id)
                                    response_text = "Please provide a URL to browse."
                                else:
                                    self._emit_tool_end(callbacks, "Action requires confirmation.", run_id)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": {"url": url, "task": task} if task else {"url": url},
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message("", self._pending_action, user_input)
                        elif tool.name == "desktop_list_windows":
                            kv = self._parse_kv_args(user_input)
                            filt = kv.get("filter")
                            if not isinstance(filt, str) or not filt.strip():
                                filt = self._extract_list_windows_filter_hint(user_input)
                            if not isinstance(filt, str) or not filt.strip():
                                filt = None
                            tool_output = tool.invoke(filter=filt)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = tool_output
                        elif tool.name == "desktop_find_control":
                            kv = self._parse_kv_args(user_input)
                            window_title = (kv.get("window_title") or "").strip() if isinstance(kv.get("window_title"), str) else ""
                            if not window_title:
                                window_title = self._extract_window_title_hint(user_input)
                            if not window_title:
                                self._emit_tool_end(callbacks, "Missing window title.", run_id)
                                response_text = "Please specify a window title (e.g., window_title=\"Notepad\")."
                            else:
                                tool_output = tool.invoke(
                                    window_title=window_title,
                                    control_name=kv.get("control_name"),
                                    control_type=kv.get("control_type"),
                                    automation_id=kv.get("automation_id"),
                                )
                                self._emit_tool_end(callbacks, tool_output, run_id)
                                response_text = tool_output
                        elif tool.name == "desktop_click":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                window_title = (kv.get("window_title") or "").strip() if isinstance(kv.get("window_title"), str) else ""
                                if not window_title:
                                    window_title = self._extract_window_title_hint(user_input)
                                control_name = kv.get("control_name")
                                if not control_name:
                                    hint = self._extract_click_control_name_hint(user_input)
                                    if hint:
                                        control_name = hint
                                kwargs = {
                                    "window_title": window_title,
                                    "control_name": control_name,
                                    "control_type": kv.get("control_type"),
                                    "automation_id": kv.get("automation_id"),
                                }
                                kwargs = {k: v for k, v in kwargs.items() if v}
                                if not kwargs.get("control_type"):
                                    inferred = self._infer_control_type(user_input, purpose="click")
                                    if inferred:
                                        kwargs["control_type"] = inferred
                                if not kwargs.get("window_title"):
                                    self._emit_tool_end(callbacks, "Missing window title.", run_id)
                                    response_text = "Please specify a window title for the click (e.g., window_title=\"Notepad\")."
                                elif not (kwargs.get("control_name") or kwargs.get("automation_id") or kwargs.get("control_type")):
                                    self._emit_tool_end(callbacks, "Missing control selector.", run_id)
                                    response_text = "Please specify a control selector (control_name=..., automation_id=..., or control_type=...)."
                                else:
                                    preview = tool.invoke(**{**kwargs, "dry_run": True})
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        elif tool.name == "desktop_type_text":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                window_title = (kv.get("window_title") or "").strip() if isinstance(kv.get("window_title"), str) else ""
                                if not window_title:
                                    window_title = self._extract_window_title_hint(user_input)
                                text_val = kv.get("text")
                                if not text_val:
                                    hint = self._extract_type_text_hint(user_input)
                                    if hint:
                                        text_val = hint
                                kwargs = {
                                    "window_title": window_title,
                                    "text": text_val,
                                    "control_name": kv.get("control_name"),
                                    "control_type": kv.get("control_type"),
                                    "automation_id": kv.get("automation_id"),
                                }
                                if kv.get("append") is True:
                                    kwargs["append"] = True
                                kwargs = {k: v for k, v in kwargs.items() if v is not None and v != ""}
                                if not kwargs.get("control_type"):
                                    inferred = self._infer_control_type(user_input, purpose="type")
                                    if inferred:
                                        kwargs["control_type"] = inferred
                                if not kwargs.get("window_title"):
                                    self._emit_tool_end(callbacks, "Missing window title.", run_id)
                                    response_text = "Please specify a window title for typing (e.g., window_title=\"Notepad\")."
                                elif not kwargs.get("text"):
                                    self._emit_tool_end(callbacks, "Missing text.", run_id)
                                    response_text = "Please specify the text to type (text=... or type \"...\")."
                                elif not (kwargs.get("control_name") or kwargs.get("automation_id") or kwargs.get("control_type")):
                                    self._emit_tool_end(callbacks, "Missing control selector.", run_id)
                                    response_text = "Please specify a target control (control_name=..., automation_id=..., or control_type=...)."
                                else:
                                    preview = tool.invoke(**{**kwargs, "dry_run": True})
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    exec_kwargs = dict(kwargs)
                                    exec_kwargs.pop("dry_run", None)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        elif tool.name == "desktop_activate_window":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                window_title = (kv.get("window_title") or "").strip() if isinstance(kv.get("window_title"), str) else ""
                                if not window_title:
                                    window_title = self._extract_window_title_hint(user_input)
                                if not window_title:
                                    self._emit_tool_end(callbacks, "Missing window title.", run_id)
                                    response_text = "Please specify a window title to activate (e.g., window_title=\"Notepad\")."
                                else:
                                    preview = tool.invoke(window_title=window_title, dry_run=True)
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": {"window_title": window_title},
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        elif tool.name == "desktop_send_hotkey":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                window_title = (kv.get("window_title") or "").strip() if isinstance(kv.get("window_title"), str) else ""
                                if not window_title:
                                    window_title = self._extract_window_title_hint(user_input)

                                hotkey = (kv.get("hotkey") or "").strip() if isinstance(kv.get("hotkey"), str) else ""
                                if not hotkey:
                                    hotkey = self._extract_hotkey_hint(user_input)
                                if not hotkey:
                                    self._emit_tool_end(callbacks, "Missing hotkey.", run_id)
                                    response_text = "Please specify a hotkey (e.g., hotkey=\"ctrl+l\" or say 'press ctrl+l')."
                                else:
                                    args = {"hotkey": hotkey}
                                    if window_title:
                                        args["window_title"] = window_title
                                    preview = tool.invoke(**{**args, "dry_run": True})
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    exec_kwargs = dict(args)
                                    exec_kwargs.pop("dry_run", None)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        elif tool.name == "analyze_screen":
                            tool_output = tool.invoke(c=user_input)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = tool_output
                        elif tool.name == "vision_qa":
                            tool_output = tool.invoke(q=user_input)
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = tool_output
                        elif tool.name == "open_chrome":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                url = self._extract_url(user_input)
                                self._emit_tool_end(callbacks, "Action requires confirmation.", run_id)
                                self._pending_action = {
                                    "tool": tool.name,
                                    "kwargs": {"url": url} if url else {},
                                    "original_input": user_input,
                                }
                                response_text = self._action_confirm_message("", self._pending_action, user_input)
                        elif tool.name == "open_application":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "System actions disabled.", run_id)
                                response_text = "System actions are disabled."
                            else:
                                low = (user_input or "").lower()
                                app = "notepad"
                                if "calculator" in low or re.search(r"\bcalc\b", low):
                                    app = "calculator"
                                elif "paint" in low:
                                    app = "paint"
                                elif "explorer" in low:
                                    app = "explorer"
                                elif "powershell" in low:
                                    app = "powershell"
                                elif "cmd" in low or "command prompt" in low:
                                    app = "cmd"
                                elif "terminal" in low:
                                    app = "terminal"
                                self._emit_tool_end(callbacks, "Action requires confirmation.", run_id)
                                self._pending_action = {
                                    "tool": tool.name,
                                    "kwargs": {"app": app},
                                    "original_input": user_input,
                                }
                                response_text = self._action_confirm_message("", self._pending_action, user_input)
                        elif tool.name == "file_write":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "File write disabled.", run_id)
                                response_text = "File write is disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                path = (kv.get("path") or "").strip() if isinstance(kv.get("path"), str) else ""
                                text_val = (kv.get("text") or "").strip() if isinstance(kv.get("text"), str) else ""
                                if not text_val:
                                    text_val = (kv.get("content") or "").strip() if isinstance(kv.get("content"), str) else ""
                                append = kv.get("append") is True
                                if not path:
                                    self._emit_tool_end(callbacks, "Missing path.", run_id)
                                    response_text = "Please specify a file path (path=...)."
                                elif not text_val:
                                    self._emit_tool_end(callbacks, "Missing content.", run_id)
                                    response_text = "Please specify text to write (text=...)."
                                else:
                                    exec_kwargs = {"path": path, "content": text_val, "append": append}
                                    preview = f"Write {len(text_val)} chars to {path}" + (" (append)" if append else "")
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        elif tool.name == "artifact_write":
                            if not self._action_allowed(tool.name):
                                self._emit_tool_end(callbacks, "File write disabled.", run_id)
                                response_text = "File write is disabled."
                            else:
                                kv = self._parse_kv_args(user_input)
                                filename = (kv.get("filename") or kv.get("name") or kv.get("file") or "").strip() if isinstance(kv.get("filename") or kv.get("name") or kv.get("file"), str) else ""
                                text_val = (kv.get("text") or kv.get("content") or "").strip() if isinstance(kv.get("text") or kv.get("content"), str) else ""
                                if not text_val:
                                    m = re.search(r"\"([^\"]{1,20000})\"", user_input)
                                    if m:
                                        text_val = m.group(1)
                                if not text_val:
                                    self._emit_tool_end(callbacks, "Missing content.", run_id)
                                    response_text = "Please specify text/content to write to an artifact (content=... or quote the text)."
                                else:
                                    exec_kwargs = {"filename": filename or None, "content": text_val}
                                    preview = f"Write {len(text_val)} chars to artifact" + (f": {filename}" if filename else "")
                                    self._emit_tool_end(callbacks, preview, run_id)
                                    self._pending_action = {
                                        "tool": tool.name,
                                        "kwargs": exec_kwargs,
                                        "original_input": user_input,
                                    }
                                    response_text = self._action_confirm_message(preview, self._pending_action, user_input)
                        else:
                            tool_output = tool.invoke()
                            self._emit_tool_end(callbacks, tool_output, run_id)
                            response_text = str(tool_output)
                    except Exception as exc:
                        self._emit_tool_error(callbacks, exc, run_id)
                        logger.warning(f"Tool router failed for tool={tool.name}: {exc}")

            if not response_text:
                context_str = f"Context (memory + docs, may be empty):\n{context}\n\n" if context else ""
                prompt = f"""{context_str}{SYSTEM_PROMPT_BASE}
Current conversation:
Human: {user_input}
AI:"""
                response_text = self.llm_wrapper.invoke(prompt)

            response_text = self._maybe_correct_past_schedule_answer(user_input, response_text, time_context, callbacks)

            full_response = response_text
            response_text = full_response
            self._pending_detail = None
            self._last_tts_text = self._select_tts_text(user_input, full_response)
            self._record_turn(user_input, response_text)
            logger.info(f"Response generated: {response_text[:100]}...")

            return response_text, True

        except Exception as e:
            logger.error(f"Error processing query: {e}")
            return f"I encountered an error: {str(e)}. Please try again.", False

    def clear_conversation(self) -> None:
        self.conversation_memory.clear()
        logger.info("Conversation memory cleared")

    def get_history(self) -> list:
        return self.conversation_memory.messages

    def switch_provider(self, provider: ModelProvider) -> None:
        self.llm_provider = provider
        self.llm_wrapper = LLMWrapper(provider)
        self._langgraph_agent_cache = {}
        self._tool_calling_executor_cache = {}
        self.graph_agent = self._create_langgraph_agent()
        if self.graph_agent is None:
            self.agent_executor = self._create_agent_executor()
            self.fallback_executor = self._create_fallback_executor()
        else:
            self.agent_executor = None
            self.fallback_executor = None

        try:
            tool_names = frozenset([str(getattr(t, "name", "")) for t in (self.lc_tools or []) if getattr(t, "name", "")])
        except Exception:
            tool_names = frozenset()
        if tool_names and self.graph_agent is not None:
            self._langgraph_agent_cache[tool_names] = {"graph": self.graph_agent, "pre_model_hook": bool(self._graph_pre_model_hook)}
        if tool_names and self.agent_executor is not None:
            self._tool_calling_executor_cache[tool_names] = self.agent_executor
        logger.info(f"Switched to {provider.value} provider")

    @property
    def provider_info(self) -> dict:
        if self.llm_provider == ModelProvider.OPENAI:
            model = config.openai.model
        elif self.llm_provider == ModelProvider.LLAMA_CPP:
            model = config.local.model_name
        else:
            model = config.local.model_name
        return {
            "provider": self.llm_provider.value,
            "model": model,
            "memory_count": self.memory.memory_count
        }

    def __repr__(self) -> str:
        return f"EchoSpeakAgent(provider={self.llm_provider.value}, tools={len(self.tools)}, memory_count={self.memory.memory_count})"


def create_agent(memory_path: Optional[str] = None, provider: ModelProvider = None) -> EchoSpeakAgent:
    return EchoSpeakAgent(memory_path, provider)


def list_available_providers() -> list:
    providers = [
        {"id": "openai", "name": "OpenAI", "local": False, "description": "OpenAI GPT models"},
        {"id": "ollama", "name": "Ollama", "local": True, "description": "Local Ollama models"},
        {"id": "lmstudio", "name": "LM Studio (GGUF direct)", "local": True, "description": "LM Studio (GGUF direct via OpenAI-compatible API)"},
        {"id": "localai", "name": "LocalAI", "local": True, "description": "LocalAI (OpenAI compatible)"},
        {"id": "llama_cpp", "name": "llama.cpp", "local": True, "description": "Direct llama.cpp models"},
        {"id": "vllm", "name": "vLLM", "local": True, "description": "vLLM server"},
    ]
    return providers


def get_provider_requirements(provider: ModelProvider) -> dict:
    requirements = {
        ModelProvider.OPENAI: {
            "env_vars": ["OPENAI_API_KEY"],
            "pip_packages": ["langchain-openai"],
            "description": "Requires OpenAI API key"
        },
        ModelProvider.OLLAMA: {
            "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
            "pip_packages": ["langchain-ollama"],
            "description": "Requires Ollama installed with models"
        },
        ModelProvider.LM_STUDIO: {
            "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
            "pip_packages": ["langchain-openai"],
            "description": "Requires LM Studio running with OpenAI-compatible server"
        },
        ModelProvider.LOCALAI: {
            "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
            "pip_packages": ["langchain-openai"],
            "description": "Requires LocalAI server running"
        },
        ModelProvider.LLAMA_CPP: {
            "env_vars": ["LOCAL_MODEL_NAME (model path)"],
            "pip_packages": ["langchain-community", "llama-cpp-python"],
            "description": "Requires llama.cpp bindings and model file (.gguf)"
        },
        ModelProvider.VLLM: {
            "env_vars": ["LOCAL_MODEL_URL", "LOCAL_MODEL_NAME"],
            "pip_packages": ["langchain-community", "vllm"],
            "description": "Requires vLLM server running"
        }
    }
    return requirements.get(provider, {})
