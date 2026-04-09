"""Device action types — the vocabulary of physical interactions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from pathfinder.contracts.common import Coordinates, ElementReference


class TapAction(BaseModel):
    action_type: Literal["tap"] = "tap"
    target: ElementReference | Coordinates
    description: str = ""


class TypeAction(BaseModel):
    action_type: Literal["type"] = "type"
    text: str
    target: ElementReference | Coordinates | None = None
    description: str = ""


class SwipeAction(BaseModel):
    action_type: Literal["swipe"] = "swipe"
    direction: Literal["up", "down", "left", "right"]
    distance: float = 0.5  # 0.0 to 1.0, fraction of screen
    description: str = ""


class BackAction(BaseModel):
    action_type: Literal["back"] = "back"
    description: str = "Navigate back"


class ScrollAction(BaseModel):
    action_type: Literal["scroll"] = "scroll"
    direction: Literal["up", "down"]
    target: ElementReference | None = None
    description: str = ""


class WaitAction(BaseModel):
    action_type: Literal["wait"] = "wait"
    condition: str = ""
    timeout_ms: int = 5000
    description: str = ""


DeviceAction = TapAction | TypeAction | SwipeAction | BackAction | ScrollAction | WaitAction
