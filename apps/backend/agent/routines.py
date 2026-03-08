"""
Routines module for EchoSpeak.
Provides scheduled and webhook-triggered automation routines.
"""

import os
import json
import uuid
import threading
import time
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


class RoutineManager:
    """Manages routine storage, scheduling, and execution."""
    
    def __init__(self, routines_dir: Optional[Path] = None):
        self.routines_dir = routines_dir or ROUTINES_DIR
        self.routines_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Routine] = {}
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()
        self._on_run: Optional[Callable[[Routine], None]] = None
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
                    logger.warning(f"Failed to load routine {file}: {e}")
        except Exception as e:
            logger.error(f"Failed to load routines: {e}")
    
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
    
    def mark_run(self, routine_id: str) -> None:
        """Mark a routine as run, updating last_run and next_run."""
        routine = self._cache.get(routine_id)
        if not routine:
            return
        
        now = datetime.now(timezone.utc)
        routine.last_run = now.isoformat()
        routine.run_count += 1
        
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
            file_path.write_text(
                routine.model_dump_json(indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to save routine {routine.id}: {e}")
            raise
    
    def set_run_callback(self, callback: Callable[[Routine], None]) -> None:
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
                    self._on_run(routine)
                    self.mark_run(routine.id)
            except Exception as e:
                logger.error(f"Error checking routine {routine.id}: {e}")
    
    def run_routine(self, routine_id: str) -> bool:
        """Manually run a routine."""
        routine = self._cache.get(routine_id)
        if not routine:
            return False
        
        # Execute callback if set, otherwise just log the action
        if self._on_run:
            try:
                self._on_run(routine)
            except Exception as e:
                logger.error(f"Failed to run routine {routine_id}: {e}")
                return False
        else:
            # No callback set - log the action that would have been taken
            logger.info(f"Routine '{routine.name}' triggered (no callback set): action_type={routine.action_type}, config={routine.action_config}")
        
        self.mark_run(routine_id)
        return True


# Global routine manager instance
_routine_manager: Optional[RoutineManager] = None


def get_routine_manager() -> RoutineManager:
    """Get the global routine manager instance."""
    global _routine_manager
    if _routine_manager is None:
        _routine_manager = RoutineManager()
    return _routine_manager
