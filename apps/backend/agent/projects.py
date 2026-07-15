"""
Projects module for EchoSpeak.
Provides project-scoped memory and context management.
"""

import json
import os
import uuid
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field
from loguru import logger


PROJECTS_DIR = Path(__file__).parent.parent / "projects"


def _default_projects_dir() -> Path:
    """Keep browser storage compatible while desktop follows its owned data root."""
    if os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop":
        from config import DATA_DIR

        return Path(DATA_DIR) / "projects"
    return PROJECTS_DIR


class Project(BaseModel):
    """Project schema for structured memory and context."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    memory_type: str = "project"  # Memory type for project-scoped memories
    context_prompt: Optional[str] = ""  # Injected into agent context when active
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    workspace_root: str = ""
    trust_state: str = "untrusted"
    git_root: str = ""
    git_metadata: Dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""
    verified_facts: List[Dict[str, Any]] = Field(default_factory=list)
    archived: bool = False
    preferred_model_profile: Optional[Dict[str, Any]] = None


class ProjectManager:
    """Manages project storage and retrieval."""
    
    def __init__(self, projects_dir: Optional[Path] = None):
        self.projects_dir = projects_dir or _default_projects_dir()
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Project] = {}
        self._load_all()
    
    def _project_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"
    
    def _load_all(self) -> None:
        """Load all projects into cache."""
        try:
            for file in self.projects_dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    project = Project(**data)
                    self._cache[project.id] = project
                except Exception as e:
                    self._fail_corrupt_project(file, e)
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Failed to load Projects from {self.projects_dir}: {e}") from e

    def _fail_corrupt_project(self, path: Path, error: Exception) -> None:
        quarantine = self.projects_dir / "corrupt-state" / f"{int(datetime.now(timezone.utc).timestamp())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            quarantine.mkdir(parents=True, exist_ok=False)
            copy = quarantine / path.name
            shutil.copy2(path, copy)
            recovery = quarantine / "RECOVERY.txt"
            recovery.write_text(
                "EchoSpeak Project recovery\n\n"
                f"Authoritative file: {path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep EchoSpeak stopped, repair or restore the authoritative JSON, then restart. "
                "The original file was not changed.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {recovery}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(
            f"Project registry is unreadable at {path}; the authoritative file was not overwritten; {note}. ({error})"
        ) from error
    
    def list_projects(self) -> List[Project]:
        """List all projects."""
        return list(self._cache.values())
    
    def get_project(self, project_id: str) -> Optional[Project]:
        """Get a project by ID."""
        return self._cache.get(project_id)
    
    def get_project_by_name(self, name: str) -> Optional[Project]:
        """Get a project by name (case-insensitive)."""
        name_lower = name.lower().strip()
        for project in self._cache.values():
            if project.name.lower().strip() == name_lower:
                return project
        return None
    
    def create_project(
        self,
        name: str,
        description: Optional[str] = "",
        context_prompt: Optional[str] = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        workspace_root: str = "",
        trust_state: str = "untrusted",
    ) -> Project:
        """Create a new project."""
        normalized_root = self.normalize_workspace_root(workspace_root) if workspace_root else ""
        git_metadata = self.read_git_metadata(normalized_root) if normalized_root else {}
        project = Project(
            name=name.strip(),
            description=description or "",
            context_prompt=context_prompt or "",
            tags=tags or [],
            metadata=metadata or {},
            workspace_root=normalized_root,
            trust_state=trust_state,
            git_root=str(git_metadata.get("root") or ""),
            git_metadata=git_metadata,
        )
        self._save_project(project)
        self._cache[project.id] = project
        logger.info(f"Created project: {project.name} ({project.id})")
        return project

    @staticmethod
    def normalize_workspace_root(value: str) -> str:
        path = Path(str(value or "")).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"Workspace root is not a directory: {path}")
        return os.path.normcase(str(path))

    @staticmethod
    def read_git_metadata(workspace_root: str) -> Dict[str, Any]:
        """Read-only Git awareness; never mutates the repository."""
        if not workspace_root:
            return {}
        cwd = Path(workspace_root)
        def run(*args: str) -> str:
            try:
                result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=3, check=False)
                return result.stdout.strip() if result.returncode == 0 else ""
            except Exception:
                return ""
        root = run("rev-parse", "--show-toplevel")
        if not root:
            return {"is_repository": False}
        branch = run("branch", "--show-current") or run("rev-parse", "--short", "HEAD")
        porcelain = run("status", "--porcelain")
        remotes = [line for line in run("remote", "-v").splitlines() if line]
        ahead = behind = 0
        counts = run("rev-list", "--left-right", "--count", "@{upstream}...HEAD").split()
        if len(counts) == 2 and all(part.isdigit() for part in counts):
            behind, ahead = int(counts[0]), int(counts[1])
        return {"is_repository": True, "root": os.path.normcase(str(Path(root).resolve())),
                "branch": branch, "remotes": remotes, "dirty": bool(porcelain),
                "ahead": ahead, "behind": behind}

    def attach_folder(self, path: str, *, name: str = "", trust_state: str = "trusted") -> Project:
        root = self.normalize_workspace_root(path)
        for project in self._cache.values():
            if project.workspace_root and os.path.normcase(project.workspace_root) == root:
                project.archived = False
                project.git_metadata = self.read_git_metadata(root)
                project.git_root = str(project.git_metadata.get("root") or "")
                project.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_project(project)
                return project
        return self.create_project(name=name.strip() or Path(root).name, workspace_root=root, trust_state=trust_state)

    def archive_project(self, project_id: str) -> Optional[Project]:
        project = self._cache.get(project_id)
        if not project:
            return None
        project.archived = True
        project.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_project(project)
        return project
    
    def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        context_prompt: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Project]:
        """Update an existing project."""
        project = self._cache.get(project_id)
        if not project:
            return None
        
        if name is not None:
            project.name = name.strip()
        if description is not None:
            project.description = description
        if context_prompt is not None:
            project.context_prompt = context_prompt
        if tags is not None:
            project.tags = tags
        if metadata is not None:
            project.metadata = metadata
        
        project.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_project(project)
        logger.info(f"Updated project: {project.name} ({project.id})")
        return project
    
    def delete_project(self, project_id: str) -> bool:
        """Delete a project."""
        project = self._cache.get(project_id)
        if not project:
            return False
        
        try:
            file_path = self._project_path(project_id)
            if file_path.exists():
                file_path.unlink()
            del self._cache[project_id]
            logger.info(f"Deleted project: {project.name} ({project_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to delete project {project_id}: {e}")
            return False
    
    def _save_project(self, project: Project) -> None:
        """Save project to disk."""
        file_path = self._project_path(project.id)
        try:
            temp = file_path.with_suffix(file_path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(project.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, file_path)
        except Exception as e:
            logger.error(f"Failed to save project {project.id}: {e}")
            raise


# Global project manager instance
_project_manager: Optional[ProjectManager] = None


def get_project_manager() -> ProjectManager:
    """Get the global project manager instance."""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager()
    return _project_manager
