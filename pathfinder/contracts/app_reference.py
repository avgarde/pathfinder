"""AppReference: the system's entry point — how a user identifies the target app."""

from __future__ import annotations

from pydantic import BaseModel


class AppReference(BaseModel):
    """The starting point. How the user identifies the target app.
    At least one of the identification fields must be provided."""

    # Identification (at least one required)
    package_name: str | None = None
    app_store_url: str | None = None
    bundle_path: str | None = None
    web_url: str | None = None
    name: str | None = None

    # Optional user-supplied context
    description: str | None = None
    baseline: str | None = None       # What's standard about this app
    differentiation: str | None = None  # What's novel about this app
    credentials: dict[str, str] | None = None
