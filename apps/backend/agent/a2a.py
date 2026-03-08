"""
Agent-to-Agent (A2A) Protocol implementation for EchoSpeak.

Implements the Google A2A spec (JSON-RPC 2.0):
  - Data models: AgentCard, Task, Message, Part, Artifact
  - A2ATaskManager: server-side task lifecycle
  - A2AClient: outbound agent discovery and task delegation
"""

from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any
from threading import Lock

import requests
from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════

class TaskState(str, Enum):
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


# Valid state transitions
_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.SUBMITTED: {TaskState.WORKING, TaskState.CANCELED, TaskState.FAILED},
    TaskState.WORKING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.INPUT_REQUIRED},
    TaskState.INPUT_REQUIRED: {TaskState.WORKING, TaskState.CANCELED, TaskState.FAILED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELED: set(),
}


@dataclass
class TextPart:
    type: str = "text"
    text: str = ""

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}


@dataclass
class FilePart:
    type: str = "file"
    name: str = ""
    mime_type: str = ""
    data: str = ""  # base64 encoded

    def to_dict(self) -> dict:
        return {"type": self.type, "name": self.name, "mimeType": self.mime_type, "data": self.data}


@dataclass
class DataPart:
    type: str = "data"
    data: Any = None

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


def _part_from_dict(d: dict) -> TextPart | FilePart | DataPart:
    """Deserialize a Part from a dict."""
    ptype = d.get("type", "text")
    if ptype == "file":
        return FilePart(name=d.get("name", ""), mime_type=d.get("mimeType", ""), data=d.get("data", ""))
    if ptype == "data":
        return DataPart(data=d.get("data"))
    return TextPart(text=d.get("text", ""))


@dataclass
class A2AMessage:
    """A single message in an A2A conversation turn."""
    role: str = "user"  # "user" or "agent"
    parts: list = field(default_factory=list)
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "role": self.role,
            "parts": [p.to_dict() if hasattr(p, "to_dict") else p for p in self.parts],
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "A2AMessage":
        parts = [_part_from_dict(p) if isinstance(p, dict) else p for p in d.get("parts", [])]
        return cls(role=d.get("role", "user"), parts=parts, metadata=d.get("metadata"))

    @property
    def text(self) -> str:
        """Extract concatenated text from all TextParts."""
        return " ".join(p.text for p in self.parts if isinstance(p, TextPart) and p.text)


@dataclass
class A2AArtifact:
    """An output artifact produced by the agent."""
    name: str = ""
    parts: list = field(default_factory=list)
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "parts": [p.to_dict() if hasattr(p, "to_dict") else p for p in self.parts],
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class A2ATask:
    """An A2A task with lifecycle management."""
    id: str = ""
    status: TaskState = TaskState.SUBMITTED
    messages: list[A2AMessage] = field(default_factory=list)
    artifacts: list[A2AArtifact] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "messages": [m.to_dict() for m in self.messages],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class AgentSkill:
    """A skill advertised in the Agent Card."""
    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "tags": self.tags}


@dataclass
class AgentCard:
    """A2A Agent Card — describes this agent's identity and capabilities."""
    name: str = "EchoSpeak"
    description: str = "Autonomous AI agent with tool-calling, memory, and multi-platform communication."
    url: str = ""
    version: str = "6.0.0"
    protocol_version: str = "0.2.0"
    skills: list[AgentSkill] = field(default_factory=list)
    capabilities: dict = field(default_factory=lambda: {
        "streaming": True,
        "pushNotifications": False,
    })
    authentication: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "skills": [s.to_dict() for s in self.skills],
            "capabilities": self.capabilities,
        }
        if self.authentication:
            d["authentication"] = self.authentication
        return d


# ═══════════════════════════════════════════════════════════════════
# A2A Task Manager (Server-side)
# ═══════════════════════════════════════════════════════════════════

class A2ATaskManager:
    """Manages the lifecycle of inbound A2A tasks.

    Tasks are created when a remote agent sends a ``tasks/send`` request.
    The manager processes the task through EchoSpeak's pipeline and returns
    the result.
    """

    def __init__(self, max_tasks: int = 500):
        self._lock = Lock()
        self._tasks: Dict[str, A2ATask] = {}
        self._max = max_tasks

    def create_task(self, message: A2AMessage, metadata: Optional[dict] = None) -> A2ATask:
        """Create a new task from an inbound message."""
        with self._lock:
            task_id = str(uuid.uuid4())
            now = time.time()
            task = A2ATask(
                id=task_id,
                status=TaskState.SUBMITTED,
                messages=[message],
                created_at=now,
                updated_at=now,
                metadata=metadata,
            )
            self._tasks[task_id] = task
            # Evict oldest if over limit
            if len(self._tasks) > self._max:
                oldest_id = min(self._tasks, key=lambda k: self._tasks[k].created_at)
                del self._tasks[oldest_id]
            logger.info(f"A2A task created: {task_id}")
            return task

    def get_task(self, task_id: str) -> Optional[A2ATask]:
        """Get a task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def update_status(self, task_id: str, new_status: TaskState) -> Optional[A2ATask]:
        """Transition task to a new status (validates transitions)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if new_status not in _TRANSITIONS.get(task.status, set()):
                logger.warning(f"Invalid A2A state transition: {task.status} → {new_status}")
                return task  # Return without changing
            task.status = new_status
            task.updated_at = time.time()
            return task

    def add_agent_message(self, task_id: str, text: str) -> Optional[A2ATask]:
        """Add an agent response message to the task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            msg = A2AMessage(role="agent", parts=[TextPart(text=text)])
            task.messages.append(msg)
            task.updated_at = time.time()
            return task

    def add_artifact(self, task_id: str, artifact: A2AArtifact) -> Optional[A2ATask]:
        """Add an output artifact to the task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.artifacts.append(artifact)
            task.updated_at = time.time()
            return task

    def list_tasks(self, limit: int = 50) -> List[A2ATask]:
        """List recent tasks."""
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.updated_at, reverse=True)
            return tasks[:limit]

    def process_task(self, task: A2ATask) -> A2ATask:
        """Process a task through EchoSpeak's pipeline.

        Transitions: submitted → working → completed/failed.
        """
        self.update_status(task.id, TaskState.WORKING)

        user_text = ""
        for msg in task.messages:
            if msg.role == "user":
                user_text = msg.text
                break

        if not user_text:
            self.update_status(task.id, TaskState.FAILED)
            self.add_agent_message(task.id, "No user message content found in task.")
            return self.get_task(task.id) or task

        try:
            # Lazy import to avoid circular dep
            from api.server import get_agent
            agent = get_agent(task.id)
            response, success = agent.process_query(
                user_text,
                include_memory=True,
                thread_id=task.id,
                source="a2a",
            )
            self.add_agent_message(task.id, str(response))
            self.update_status(
                task.id,
                TaskState.COMPLETED if success else TaskState.FAILED,
            )
        except Exception as exc:
            logger.error(f"A2A task {task.id} failed: {exc}")
            self.add_agent_message(task.id, f"Task failed: {exc}")
            self.update_status(task.id, TaskState.FAILED)

        return self.get_task(task.id) or task


# ═══════════════════════════════════════════════════════════════════
# A2A Client (Outbound)
# ═══════════════════════════════════════════════════════════════════

class A2AClient:
    """Client for communicating with remote A2A agents."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def discover(self, base_url: str) -> Optional[dict]:
        """Fetch a remote agent's Agent Card from /.well-known/agent.json."""
        url = base_url.rstrip("/") + "/.well-known/agent.json"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error(f"A2A discover failed for {url}: {exc}")
            return None

    def send_task(self, base_url: str, message: str, auth_key: Optional[str] = None) -> Optional[dict]:
        """Send a task to a remote A2A agent via JSON-RPC."""
        rpc_url = base_url.rstrip("/") + "/a2a"
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": message}],
                },
            },
        }
        headers = {"Content-Type": "application/json"}
        if auth_key:
            headers["Authorization"] = f"Bearer {auth_key}"

        try:
            resp = requests.post(rpc_url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                logger.error(f"A2A RPC error: {result['error']}")
                return None
            return result.get("result")
        except Exception as exc:
            logger.error(f"A2A send_task failed for {rpc_url}: {exc}")
            return None

    def get_task_status(self, base_url: str, task_id: str, auth_key: Optional[str] = None) -> Optional[dict]:
        """Get the status of a remote task."""
        rpc_url = base_url.rstrip("/") + "/a2a"
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        headers = {"Content-Type": "application/json"}
        if auth_key:
            headers["Authorization"] = f"Bearer {auth_key}"

        try:
            resp = requests.post(rpc_url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            result = resp.json()
            return result.get("result")
        except Exception as exc:
            logger.error(f"A2A get_task failed: {exc}")
            return None

    def cancel_task(self, base_url: str, task_id: str, auth_key: Optional[str] = None) -> bool:
        """Cancel a remote task."""
        rpc_url = base_url.rstrip("/") + "/a2a"
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        headers = {"Content-Type": "application/json"}
        if auth_key:
            headers["Authorization"] = f"Bearer {auth_key}"

        try:
            resp = requests.post(rpc_url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error(f"A2A cancel_task failed: {exc}")
            return False


# ═══════════════════════════════════════════════════════════════════
# Agent Card Builder
# ═══════════════════════════════════════════════════════════════════

def build_agent_card(base_url: str = "") -> AgentCard:
    """Build EchoSpeak's Agent Card from config and skills registry."""
    try:
        from config import config
    except ImportError:
        config = None  # type: ignore[assignment]

    name = getattr(config, "a2a_agent_name", "EchoSpeak") if config else "EchoSpeak"
    desc = getattr(config, "a2a_agent_description", "") if config else ""
    if not desc:
        desc = "Autonomous AI agent with tool-calling, persistent memory, and multi-platform communication."

    # Auto-populate skills from skills registry
    skills: list[AgentSkill] = []
    try:
        from agent.skills_registry import SkillsRegistry
        registry = SkillsRegistry()
        for skill_meta in registry.list_skills():
            skills.append(AgentSkill(
                id=skill_meta.get("name", "").lower().replace(" ", "_"),
                name=skill_meta.get("name", ""),
                description=skill_meta.get("description", ""),
                tags=skill_meta.get("tags", []),
            ))
    except Exception:
        pass

    auth = None
    auth_key = getattr(config, "a2a_auth_key", "") if config else ""
    if auth_key:
        auth = {"schemes": [{"scheme": "bearer"}]}

    return AgentCard(
        name=name,
        description=desc,
        url=base_url,
        skills=skills,
        authentication=auth,
    )


# ═══════════════════════════════════════════════════════════════════
# Singletons
# ═══════════════════════════════════════════════════════════════════

_task_manager: Optional[A2ATaskManager] = None
_client: Optional[A2AClient] = None


def get_task_manager() -> A2ATaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = A2ATaskManager()
    return _task_manager


def get_a2a_client() -> A2AClient:
    global _client
    if _client is None:
        _client = A2AClient()
    return _client
