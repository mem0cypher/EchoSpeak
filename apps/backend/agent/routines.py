"""
Routines module for EchoSpeak.
Provides scheduled and webhook-triggered automation routines.
"""

import os
import json
import uuid
import threading
import time
import shutil
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
from pydantic import BaseModel, Field
from loguru import logger

try:
    from croniter import croniter
except Exception:
    croniter = None


ROUTINES_DIR = Path(__file__).parent.parent / "routines"


def _default_routines_dir() -> Path:
    """Keep browser storage compatible while desktop follows its owned data root."""
    if os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop":
        from config import DATA_DIR

        return Path(DATA_DIR) / "routines"
    return ROUTINES_DIR


class Routine(BaseModel):
    """Routine schema for scheduled/webhook actions."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = ""
    enabled: bool = True
    trigger_type: str = "schedule"  # "schedule" | "webhook" | "manual"
    schedule: Optional[str] = None  # Cron expression for scheduled routines
    webhook_path: Optional[str] = None  # URL path for webhook routines
    action_type: str = "query"  # "query" | "tool" | "skill"
    action_config: Dict[str, Any] = Field(default_factory=dict)  # Query text, tool name/args, etc.
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    run_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)
    delivery_channels: List[str] = Field(default_factory=lambda: ["web"])  # discord, telegram, email, whatsapp, web
    project_id: str = ""
    session_id: str = ""
    missed_run_policy: str = "run_next"
    last_task_id: str = ""
    last_result_status: str = ""
    last_error: str = ""


class RoutineManager:
    """Manages routine storage, scheduling, and execution."""
    
    def __init__(self, routines_dir: Optional[Path] = None):
        self.routines_dir = routines_dir or _default_routines_dir()
        self.routines_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Routine] = {}
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()
        self._on_run: Optional[Callable[[Routine], Any]] = None
        self._load_all()
    
    def _routine_path(self, routine_id: str) -> Path:
        return self.routines_dir / f"{routine_id}.json"
    
    def _load_all(self) -> None:
        """Load all routines into cache."""
        try:
            for file in self.routines_dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    routine = Routine(**data)
                    self._cache[routine.id] = routine
                except Exception as e:
                    self._fail_corrupt_routine(file, e)
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Failed to load Routines from {self.routines_dir}: {e}") from e

    def _fail_corrupt_routine(self, path: Path, error: Exception) -> None:
        quarantine = self.routines_dir / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            quarantine.mkdir(parents=True, exist_ok=False)
            copy = quarantine / path.name
            shutil.copy2(path, copy)
            guide = quarantine / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak Routine recovery\n\n"
                f"Authoritative file: {path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep EchoSpeak stopped, repair or restore the authoritative JSON, then restart. "
                "The original file was not changed.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(
            f"Routine registry is unreadable at {path}; the authoritative file was not overwritten; {note}. ({error})"
        ) from error
    
    def list_routines(self, enabled_only: bool = False) -> List[Routine]:
        """List all routines."""
        routines = list(self._cache.values())
        if enabled_only:
            routines = [r for r in routines if r.enabled]
        return routines
    
    def get_routine(self, routine_id: str) -> Optional[Routine]:
        """Get a routine by ID."""
        return self._cache.get(routine_id)
    
    def get_routine_by_webhook(self, webhook_path: str) -> Optional[Routine]:
        """Get a routine by webhook path."""
        for routine in self._cache.values():
            if routine.webhook_path == webhook_path and routine.enabled:
                return routine
        return None
    
    def create_routine(
        self,
        name: str,
        trigger_type: str = "schedule",
        schedule: Optional[str] = None,
        webhook_path: Optional[str] = None,
        action_type: str = "query",
        action_config: Optional[Dict[str, Any]] = None,
        description: Optional[str] = "",
        enabled: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
        delivery_channels: Optional[List[str]] = None,
        project_id: str = "",
        session_id: str = "",
        missed_run_policy: str = "run_next",
    ) -> Routine:
        """Create a new routine."""
        routine = Routine(
            name=name.strip(),
            description=description or "",
            enabled=enabled,
            trigger_type=trigger_type,
            schedule=schedule,
            webhook_path=webhook_path,
            action_type=action_type,
            action_config=action_config or {},
            metadata=metadata or {},
            delivery_channels=list(delivery_channels or ["web"]),
            project_id=str(project_id or ""),
            session_id=str(session_id or ""),
            missed_run_policy=str(missed_run_policy or "run_next"),
        )
        
        # Calculate next run time for scheduled routines
        if routine.trigger_type == "schedule" and routine.schedule and croniter:
            try:
                cron = croniter(routine.schedule, datetime.now(timezone.utc))
                routine.next_run = cron.get_next(datetime).isoformat()
            except Exception as e:
                logger.warning(f"Invalid cron expression for routine {name}: {e}")
        
        self._save_routine(routine)
        self._cache[routine.id] = routine
        logger.info(f"Created routine: {routine.name} ({routine.id})")
        return routine
    
    def update_routine(
        self,
        routine_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        trigger_type: Optional[str] = None,
        schedule: Optional[str] = None,
        webhook_path: Optional[str] = None,
        action_type: Optional[str] = None,
        action_config: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        delivery_channels: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        missed_run_policy: Optional[str] = None,
    ) -> Optional[Routine]:
        """Update an existing routine."""
        routine = self._cache.get(routine_id)
        if not routine:
            return None
        
        if name is not None:
            routine.name = name.strip()
        if description is not None:
            routine.description = description
        if enabled is not None:
            routine.enabled = enabled
        if trigger_type is not None:
            routine.trigger_type = trigger_type
        if schedule is not None:
            routine.schedule = schedule
        if webhook_path is not None:
            routine.webhook_path = webhook_path
        if action_type is not None:
            routine.action_type = action_type
        if action_config is not None:
            routine.action_config = action_config
        if metadata is not None:
            routine.metadata = metadata
        if delivery_channels is not None:
            routine.delivery_channels = list(delivery_channels)
        if project_id is not None:
            routine.project_id = str(project_id)
        if session_id is not None:
            routine.session_id = str(session_id)
        if missed_run_policy is not None:
            routine.missed_run_policy = str(missed_run_policy)
        
        routine.updated_at = datetime.now(timezone.utc).isoformat()
        
        # Recalculate next run time
        if routine.trigger_type == "schedule" and routine.schedule and croniter:
            try:
                cron = croniter(routine.schedule, datetime.now(timezone.utc))
                routine.next_run = cron.get_next(datetime).isoformat()
            except Exception as e:
                logger.warning(f"Invalid cron expression: {e}")
        
        self._save_routine(routine)
        logger.info(f"Updated routine: {routine.name} ({routine.id})")
        return routine
    
    def delete_routine(self, routine_id: str) -> bool:
        """Delete a routine."""
        routine = self._cache.get(routine_id)
        if not routine:
            return False
        
        try:
            file_path = self._routine_path(routine_id)
            if file_path.exists():
                file_path.unlink()
            del self._cache[routine_id]
            logger.info(f"Deleted routine: {routine.name} ({routine_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to delete routine {routine_id}: {e}")
            return False
    
    def mark_run(self, routine_id: str, *, success: bool, task_id: str = "", error: str = "") -> None:
        """Mark a routine as run, updating last_run and next_run."""
        routine = self._cache.get(routine_id)
        if not routine:
            return
        
        now = datetime.now(timezone.utc)
        routine.last_run = now.isoformat()
        routine.run_count += 1
        routine.last_task_id = str(task_id or "")
        routine.last_result_status = "complete" if success else "failed"
        routine.last_error = str(error or "")[:1000]
        routine.updated_at = now.isoformat()
        
        if routine.trigger_type == "schedule" and routine.schedule and croniter:
            try:
                cron = croniter(routine.schedule, now)
                routine.next_run = cron.get_next(datetime).isoformat()
            except Exception:
                pass
        
        self._save_routine(routine)
    
    def _save_routine(self, routine: Routine) -> None:
        """Save routine to disk."""
        file_path = self._routine_path(routine.id)
        try:
            temp = file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(routine.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, file_path)
        except Exception as e:
            logger.error(f"Failed to save routine {routine.id}: {e}")
            raise
    
    def set_run_callback(self, callback: Callable[[Routine], Any]) -> None:
        """Set callback for routine execution."""
        self._on_run = callback
    
    def start_scheduler(self, interval_seconds: int = 60) -> None:
        """Start the scheduler thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        
        self._scheduler_stop.clear()
        
        def _scheduler_loop():
            while not self._scheduler_stop.is_set():
                try:
                    self._check_scheduled_routines()
                except Exception as e:
                    logger.error(f"Scheduler error: {e}")
                self._scheduler_stop.wait(interval_seconds)
        
        self._scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        logger.info("Routine scheduler started")
    
    def stop_scheduler(self) -> None:
        """Stop the scheduler thread."""
        self._scheduler_stop.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)
        logger.info("Routine scheduler stopped")
    
    def _check_scheduled_routines(self) -> None:
        """Check and run any scheduled routines that are due."""
        if not self._on_run:
            return
        
        now = datetime.now(timezone.utc)
        for routine in self.list_routines(enabled_only=True):
            if routine.trigger_type != "schedule":
                continue
            if not routine.next_run:
                continue
            
            try:
                next_run = datetime.fromisoformat(routine.next_run.replace("Z", "+00:00"))
                if next_run <= now:
                    logger.info(f"Running scheduled routine: {routine.name}")
                    result = self._on_run(routine)
                    success = isinstance(result, dict) and bool(result.get("success", False))
                    self.mark_run(
                        routine.id,
                        success=success,
                        task_id=str(result.get("task_id") or "") if isinstance(result, dict) else "",
                        error=str(result.get("error") or "") if isinstance(result, dict) else "",
                    )
            except Exception as e:
                logger.error(f"Error checking routine {routine.id}: {e}")
                self.mark_run(routine.id, success=False, error=str(e))
    
    def run_routine(self, routine_id: str) -> bool:
        """Manually run a routine."""
        routine = self._cache.get(routine_id)
        if not routine:
            return False
        
        # A Routine is a trigger/configuration owner, never an execution owner.
        # Without the API coordinator callback there is no canonical Task/Turn.
        if self._on_run:
            try:
                result = self._on_run(routine)
            except Exception as e:
                logger.error(f"Failed to run routine {routine_id}: {e}")
                self.mark_run(routine_id, success=False, error=str(e))
                return False
        else:
            error = "Routine coordinator is unavailable; no Product Task or Turn was created"
            logger.warning(f"Routine '{routine.name}' blocked: {error}")
            self.mark_run(routine_id, success=False, error=error)
            return False
        
        success = isinstance(result, dict) and bool(result.get("success", False))
        self.mark_run(
            routine_id,
            success=success,
            task_id=str(result.get("task_id") or "") if self._on_run and isinstance(result, dict) else "",
            error=str(result.get("error") or "") if self._on_run and isinstance(result, dict) else "",
        )
        return success


# Global routine manager instance
_routine_manager: Optional[RoutineManager] = None


def get_routine_manager() -> RoutineManager:
    """Get the global routine manager instance."""
    global _routine_manager
    if _routine_manager is None:
        _routine_manager = RoutineManager()
    return _routine_manager
