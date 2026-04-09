"""Event system for the Pathfinder exploration engine.

This module defines the event types emitted by the agent loop and the
EventBus that distributes them to listeners. The event stream is the
primary integration surface for UIs (IDE, CLI, web dashboard) — the
core loop emits events, consumers subscribe and render.

Design principles:
- Events are immutable data (frozen dataclasses). They describe what
  happened, not what to do.
- The EventBus is async: listeners are coroutines called in order.
- Events carry enough data to render a complete UI without polling files.
- Screenshots are sent as file paths, not bytes — the consumer decides
  whether/how to load them.

Usage:
    bus = EventBus()
    bus.subscribe(my_listener)        # async def my_listener(event): ...
    await bus.emit(ExplorationStarted(...))
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Enumeration of all event kinds."""
    EXPLORATION_STARTED = "exploration_started"
    STEP_STARTED = "step_started"
    PERCEPTION_COMPLETE = "perception_complete"
    MODEL_UPDATED = "model_updated"
    ACTION_PLANNED = "action_planned"
    ACTION_EXECUTED = "action_executed"
    INPUT_REQUIRED = "input_required"
    INPUT_SUPPLIED = "input_supplied"
    FLOW_DETECTED = "flow_detected"
    EXPLORATION_COMPLETE = "exploration_complete"
    LOG = "log"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    """Base event. All events carry a type, timestamp, and step number."""
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    step: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict for WebSocket transmission."""
        d: dict[str, Any] = {"event_type": self.event_type.value, "timestamp": self.timestamp, "step": self.step}
        # Add all subclass fields
        for k, v in self.__dict__.items():
            if k not in d:
                d[k] = v
        return d


@dataclass(frozen=True)
class ExplorationStarted(Event):
    """Emitted once when the exploration begins."""
    event_type: EventType = field(default=EventType.EXPLORATION_STARTED, init=False)
    run_id: str = ""
    target_url: str = ""
    target_name: str = ""
    max_actions: int = 0
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepStarted(Event):
    """Emitted at the beginning of each exploration step."""
    event_type: EventType = field(default=EventType.STEP_STARTED, init=False)


@dataclass(frozen=True)
class PerceptionComplete(Event):
    """Emitted after Layer 1 perceives the current screen."""
    event_type: EventType = field(default=EventType.PERCEPTION_COMPLETE, init=False)
    screenshot_path: str = ""
    page_url: str = ""
    page_title: str = ""
    screen_id: str = ""
    screen_description: str = ""
    ui_summary: str = ""


@dataclass(frozen=True)
class ModelUpdated(Event):
    """Emitted after Layer 2 updates the application model."""
    event_type: EventType = field(default=EventType.MODEL_UPDATED, init=False)
    screens_count: int = 0
    transitions_count: int = 0
    capabilities_count: int = 0
    coverage_estimate: float = 0.0
    # Incremental: what changed this step
    new_screen: bool = False
    new_transitions: int = 0
    screen_id: str = ""


@dataclass(frozen=True)
class ActionPlanned(Event):
    """Emitted after the planner decides what to do next."""
    event_type: EventType = field(default=EventType.ACTION_PLANNED, init=False)
    action_type: str = ""
    action_description: str = ""
    reasoning: str = ""
    raw_plan: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionExecuted(Event):
    """Emitted after an action has been performed on the device."""
    event_type: EventType = field(default=EventType.ACTION_EXECUTED, init=False)
    action_type: str = ""
    action_description: str = ""
    success: bool = True
    result_message: str = ""


@dataclass(frozen=True)
class InputRequired(Event):
    """Emitted when the exploration encounters an input it can't fill."""
    event_type: EventType = field(default=EventType.INPUT_REQUIRED, init=False)
    field_name: str = ""
    field_type: str = ""
    context: str = ""


@dataclass(frozen=True)
class InputSupplied(Event):
    """Emitted when an input value is resolved (from spec, generated, or user)."""
    event_type: EventType = field(default=EventType.INPUT_SUPPLIED, init=False)
    field_name: str = ""
    strategy: str = ""
    value_preview: str = ""  # First N chars — don't leak passwords


@dataclass(frozen=True)
class FlowDetected(Event):
    """Emitted when Layer 3 identifies a user flow."""
    event_type: EventType = field(default=EventType.FLOW_DETECTED, init=False)
    flow_name: str = ""
    flow_category: str = ""
    flow_type: str = ""  # "observed" or "hypothetical"
    importance: float = 0.0
    step_count: int = 0
    description: str = ""


@dataclass(frozen=True)
class ExplorationComplete(Event):
    """Emitted once when the exploration finishes."""
    event_type: EventType = field(default=EventType.EXPLORATION_COMPLETE, init=False)
    run_id: str = ""
    stop_reason: str = ""
    total_actions: int = 0
    duration_seconds: float = 0.0
    screens_count: int = 0
    transitions_count: int = 0
    flows_count: int = 0
    model_path: str = ""
    summary_path: str = ""
    flows_path: str = ""


@dataclass(frozen=True)
class LogEvent(Event):
    """General-purpose log message for the UI."""
    event_type: EventType = field(default=EventType.LOG, init=False)
    level: str = "info"  # "debug", "info", "warning", "error"
    message: str = ""
    source: str = ""  # e.g. "perception", "planner", "device"


@dataclass(frozen=True)
class ErrorEvent(Event):
    """Emitted when a non-fatal error occurs during exploration."""
    event_type: EventType = field(default=EventType.ERROR, init=False)
    message: str = ""
    source: str = ""
    recoverable: bool = True


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

# Type alias for listener callables
Listener = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async event distribution hub.

    Listeners are async callables that receive every event. They are
    called in subscription order. A failing listener logs the error
    and does not block other listeners.

    Usage:
        bus = EventBus()
        async def on_event(event):
            print(event)
        bus.subscribe(on_event)
        await bus.emit(StepStarted(step=1))
    """

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._history: list[Event] = []
        self._record_history = False

    def subscribe(self, listener: Listener) -> None:
        """Add a listener. It will receive all future events."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        """Remove a listener."""
        self._listeners = [l for l in self._listeners if l is not listener]

    def enable_history(self) -> None:
        """Start recording events for late-joining consumers."""
        self._record_history = True

    @property
    def history(self) -> list[Event]:
        """All events emitted since enable_history() was called."""
        return list(self._history)

    async def emit(self, event: Event) -> None:
        """Dispatch an event to all listeners."""
        if self._record_history:
            self._history.append(event)

        for listener in self._listeners:
            try:
                await listener(event)
            except Exception:
                logger.exception("Event listener %s failed on %s", listener, event.event_type)

    def clear(self) -> None:
        """Remove all listeners and history."""
        self._listeners.clear()
        self._history.clear()
