"""
Projects module for EchoSpeak.
Provides project-scoped memory and context management.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field
from loguru import logger


PROJECTS_DIR = Path(__file__).parent.parent / "projects"


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


class ProjectManager:
    """Manages project storage and retrieval."""
    
    def __init__(self, projects_dir: Optional[Path] = None):
        self.projects_dir = projects_dir or PROJECTS_DIR
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
                    logger.warning(f"Failed to load project {file}: {e}")
        except Exception as e:
            logger.error(f"Failed to load projects: {e}")
    
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
    ) -> Project:
        """Create a new project."""
        project = Project(
            name=name.strip(),
            description=description or "",
            context_prompt=context_prompt or "",
            tags=tags or [],
            metadata=metadata or {},
        )
        self._save_project(project)
        self._cache[project.id] = project
        logger.info(f"Created project: {project.name} ({project.id})")
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
            file_path.write_text(
                project.model_dump_json(indent=2),
                encoding="utf-8"
            )
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
