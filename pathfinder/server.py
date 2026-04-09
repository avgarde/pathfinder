"""WebSocket server that bridges the Pathfinder exploration engine to external UIs.

This server accepts WebSocket connections and handles exploration commands from
clients (e.g., Electron IDE, web dashboard). It manages the AI setup, device
adapter, and exploration loop, broadcasting events to connected clients in real time.

Usage:
    from pathfinder.server import PathfinderServer

    server = PathfinderServer(host="localhost", port=9720)
    await server.start()
    # Now clients can connect to ws://localhost:9720

Or via CLI:
    pathfinder serve --host localhost --port 9720
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

try:
    import websockets
    import websockets.server
except ImportError:
    raise ImportError("websockets is required: pip install websockets")

from pathfinder.ai.config import AIConfig
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.prior_context import PriorContext
from pathfinder.device.web.adapter import WebDeviceAdapter
from pathfinder.events import Event, EventBus, PerceptionComplete
from pathfinder.orchestrator.agent_loop import AgentLoop, ExplorationConfig, generate_run_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helpers (mirrors cli.py)
# ---------------------------------------------------------------------------

def _load_env_file() -> dict[str, str]:
    """Load key=value pairs from .env if it exists."""
    env_path = Path.cwd() / ".env"
    result: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def get_ai_config() -> AIConfig:
    """Build AIConfig from environment variables.

    Provider selection (PATHFINDER_AI_PROVIDER):
      "anthropic"          — Anthropic Claude API (default)
      "openai_compatible"  — Any OpenAI-compatible endpoint

    Environment variables:
      ANTHROPIC_API_KEY     — API key for Anthropic (required for provider=anthropic)
      PATHFINDER_AI_PROVIDER — "anthropic" or "openai_compatible"
      PATHFINDER_AI_MODEL    — Model name (default varies by provider)
      PATHFINDER_AI_BASE_URL — Base URL for the API endpoint
      PATHFINDER_AI_API_KEY  — API key for non-Anthropic providers
      PATHFINDER_AI_MAX_TOKENS — Max tokens for responses (default: 4096)
      PATHFINDER_AI_TIMEOUT — HTTP request timeout in seconds (default: 600)
    """
    env_file = _load_env_file()

    def env(key: str) -> str | None:
        """Check os.environ first, then .env file."""
        return os.environ.get(key) or env_file.get(key)

    provider = env("PATHFINDER_AI_PROVIDER") or "anthropic"

    if provider == "anthropic":
        api_key = env("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error(
                "ANTHROPIC_API_KEY not set. "
                "Set it in your environment or in a .env file. "
                "Or set PATHFINDER_AI_PROVIDER=openai_compatible to use a local model."
            )
            raise ValueError("ANTHROPIC_API_KEY not set")

        return AIConfig(
            provider="anthropic",
            api_key=api_key,
            model=env("PATHFINDER_AI_MODEL") or "claude-sonnet-4-20250514",
            base_url=env("PATHFINDER_AI_BASE_URL"),
            max_tokens=int(env("PATHFINDER_AI_MAX_TOKENS") or "4096"),
            timeout=float(env("PATHFINDER_AI_TIMEOUT") or "600"),
        )

    elif provider == "openai_compatible":
        base_url = env("PATHFINDER_AI_BASE_URL")
        if not base_url:
            logger.error(
                "PATHFINDER_AI_BASE_URL not set. "
                "For Ollama: http://localhost:11434/v1\n"
                "For vLLM: http://localhost:8000/v1\n"
                "For LM Studio: http://localhost:1234/v1"
            )
            raise ValueError("PATHFINDER_AI_BASE_URL not set")

        return AIConfig(
            provider="openai_compatible",
            api_key=env("PATHFINDER_AI_API_KEY") or "not-needed",
            model=env("PATHFINDER_AI_MODEL") or "qwen2.5-vl:32b",
            base_url=base_url,
            max_tokens=int(env("PATHFINDER_AI_MAX_TOKENS") or "4096"),
            timeout=float(env("PATHFINDER_AI_TIMEOUT") or "600"),
        )

    else:
        logger.error(f"Unknown provider '{provider}'. Supported: anthropic, openai_compatible")
        raise ValueError(f"Unknown provider '{provider}'")


def get_ai(config: AIConfig):
    """Instantiate the right AI backend based on config.provider."""
    if config.provider == "openai_compatible":
        from pathfinder.ai.openai_compatible import OpenAICompatibleAI

        return OpenAICompatibleAI(config)
    else:
        from pathfinder.ai.anthropic_ai import AnthropicAI

        return AnthropicAI(config)


# ---------------------------------------------------------------------------
# WebSocket Server
# ---------------------------------------------------------------------------

class PathfinderServer:
    """WebSocket server that bridges Pathfinder to external UIs.

    Manages connections, handles incoming commands, runs explorations,
    and broadcasts events to all connected clients.
    """

    def __init__(self, host: str = "localhost", port: int = 9720):
        self.host = host
        self.port = port
        self._clients: set = set()
        self._server = None
        self._exploration_task: asyncio.Task | None = None
        self._stop_flag: asyncio.Event = asyncio.Event()
        self._event_bus: EventBus | None = None
        self._agent_loop: AgentLoop | None = None
        self._current_device: WebDeviceAdapter | None = None
        self._input_cache: dict[str, str] = {}

    async def start(self) -> None:
        """Start the WebSocket server."""
        logger.info(f"Starting Pathfinder WebSocket server on {self.host}:{self.port}")

        self._server = await websockets.serve(
            self._handle_client, self.host, self.port
        )
        logger.info(f"Server listening on ws://{self.host}:{self.port}")
        await asyncio.Event().wait()  # Run forever

    async def stop(self) -> None:
        """Stop the server and clean up resources."""
        logger.info("Stopping Pathfinder server")
        if self._exploration_task:
            self._stop_flag.set()
            try:
                await asyncio.wait_for(self._exploration_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._exploration_task.cancel()

        if self._current_device:
            try:
                await self._current_device.stop()
            except Exception as e:
                logger.error(f"Error stopping device: {e}")

        # Close all client connections
        for client in list(self._clients):
            await client.close()

        logger.info("Server stopped")

    async def _handle_client(self, client) -> None:
        """Handle a new WebSocket client connection."""
        self._clients.add(client)
        remote = getattr(client, "remote_address", "unknown")
        logger.info(f"Client connected: {remote}")

        try:
            async for message in client:
                try:
                    command = json.loads(message)
                    await self._handle_command(client, command)
                except json.JSONDecodeError as e:
                    await self._send_error(client, f"Invalid JSON: {e}")
                except Exception as e:
                    logger.exception(f"Error handling command: {e}")
                    await self._send_error(client, f"Command error: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(client)
            logger.info(f"Client disconnected: {remote}")

    async def _handle_command(self, client, command: dict[str, Any]) -> None:
        """Process an incoming command from a client."""
        cmd_type = command.get("command")

        if cmd_type == "start_exploration":
            await self._cmd_start_exploration(command)
        elif cmd_type == "stop_exploration":
            await self._cmd_stop_exploration()
        elif cmd_type == "supply_input":
            await self._cmd_supply_input(command)
        elif cmd_type == "get_status":
            await self._cmd_get_status(client)
        else:
            await self._send_error(client, f"Unknown command: {cmd_type}")

    async def _cmd_start_exploration(self, command: dict[str, Any]) -> None:
        """Start an exploration run."""
        try:
            # Extract parameters
            url = command.get("url", "")
            name = command.get("name", "Exploration")
            max_actions = command.get("max_actions", 50)
            run_prefix = command.get("run_prefix", "")
            headless = command.get("headless", True)
            browser = command.get("browser", "chromium")
            viewport = command.get("viewport", "1280x800")
            inputs_path = command.get("inputs_path")
            deep_trace = command.get("deep_trace", False)

            if not url:
                await self._broadcast_error("start_exploration: url is required")
                return

            # Parse viewport
            try:
                width, height = map(int, viewport.split("x"))
            except (ValueError, AttributeError):
                width, height = 1280, 800

            # Clear stop flag and input cache
            self._stop_flag.clear()
            self._input_cache.clear()

            # Set up AI and device
            ai_config = get_ai_config()
            ai = get_ai(ai_config)

            self._current_device = WebDeviceAdapter(
                headless=headless,
                browser_type=browser,
                viewport_width=width,
                viewport_height=height,
            )
            await self._current_device.start()

            # Set up exploration config
            exploration_config = ExplorationConfig(
                max_actions=max_actions,
                output_dir="./exploration",
                save_screenshots=True,
                save_observations=True,
                save_model_snapshots=False,
                settle_time=1.0,
                capture_ui_structure=True,
                stuck_threshold=2,
                interactive=True,
                generate_flows=True,
                deep_trace=deep_trace,
            )

            # Load input specs if provided
            if inputs_path and Path(inputs_path).exists():
                try:
                    import json as json_module
                    with open(inputs_path) as f:
                        # Parse input specs from JSON
                        pass  # Would load InputSpec objects here
                except Exception as e:
                    logger.warning(f"Could not load inputs from {inputs_path}: {e}")

            # Create event bus and subscribe to broadcast events
            self._event_bus = EventBus()
            self._event_bus.subscribe(self._on_event)

            # Create agent loop
            self._agent_loop = AgentLoop(
                ai=ai,
                device=self._current_device,
                config=exploration_config,
                event_bus=self._event_bus,
            )

            # Start exploration in background
            run_id = run_prefix + generate_run_id() if run_prefix else generate_run_id()
            app_ref = AppReference(
                web_url=url,
                name=name,
            )

            self._exploration_task = asyncio.create_task(
                self._run_exploration(app_ref, run_id, url)
            )

        except Exception as e:
            logger.exception(f"Error starting exploration: {e}")
            await self._broadcast_error(f"Failed to start exploration: {e}")

    async def _run_exploration(self, app_ref: AppReference, run_id: str, start_url: str) -> None:
        """Run the exploration loop."""
        try:
            result = await self._agent_loop.explore(
                app_ref=app_ref,
                prior_context=None,
                start_url=start_url,
                run_id=run_id,
            )
            logger.info(f"Exploration completed: {run_id}")
        except asyncio.CancelledError:
            logger.info("Exploration cancelled")
        except Exception as e:
            logger.exception(f"Exploration failed: {e}")
            await self._broadcast_error(f"Exploration error: {e}")
        finally:
            if self._current_device:
                try:
                    await self._current_device.stop()
                except Exception as e:
                    logger.error(f"Error stopping device: {e}")
            self._current_device = None
            self._exploration_task = None

    async def _cmd_stop_exploration(self) -> None:
        """Stop the current exploration."""
        logger.info("Stop exploration requested")
        self._stop_flag.set()

        # The agent loop should check this flag and stop gracefully
        # Optionally, cancel the task if it takes too long
        if self._exploration_task and not self._exploration_task.done():
            try:
                await asyncio.wait_for(self._exploration_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._exploration_task.cancel()

    async def _cmd_supply_input(self, command: dict[str, Any]) -> None:
        """Supply an input value for an interactive field."""
        field = command.get("field")
        value = command.get("value")

        if field and value is not None:
            self._input_cache[field] = str(value)
            logger.info(f"Input supplied for field: {field}")
            await self._broadcast({
                "event_type": "input_supplied",
                "field": field,
                "value": value,
            })

    async def _cmd_get_status(self, client) -> None:
        """Get current server status."""
        status = {
            "connected_clients": len(self._clients),
            "exploration_running": self._exploration_task is not None and not self._exploration_task.done(),
            "stop_requested": self._stop_flag.is_set(),
        }
        await client.send(json.dumps({"status": status}))

    async def _on_event(self, event: Event) -> None:
        """Listen to events from the EventBus and broadcast to clients."""
        event_dict = event.to_dict()

        # For PerceptionComplete events, load and encode the screenshot
        if isinstance(event, PerceptionComplete) and event.screenshot_path:
            try:
                screenshot_path = Path(event.screenshot_path)
                if screenshot_path.exists():
                    with open(screenshot_path, "rb") as f:
                        image_data = f.read()
                        b64_data = base64.b64encode(image_data).decode("utf-8")
                        # Determine MIME type from extension
                        mime_type = "image/png" if screenshot_path.suffix.lower() == ".png" else "image/jpeg"
                        event_dict["screenshot_base64"] = f"data:{mime_type};base64,{b64_data}"
            except Exception as e:
                logger.warning(f"Could not load screenshot {event.screenshot_path}: {e}")

        # Check stop flag
        if self._stop_flag.is_set():
            # Signal to exploration loop to stop (this is checked by the agent loop)
            pass

        await self._broadcast(event_dict)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        if not self._clients:
            return

        message_json = json.dumps(message)
        disconnected = set()

        for client in self._clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                logger.warning(f"Error sending to client: {e}")
                disconnected.add(client)

        # Remove disconnected clients
        self._clients -= disconnected

    async def _broadcast_error(self, message: str) -> None:
        """Broadcast an error message to all clients."""
        await self._broadcast({
            "event_type": "error",
            "message": message,
        })

    async def _send_error(self, client, message: str) -> None:
        """Send an error message to a specific client."""
        try:
            await client.send(json.dumps({
                "event_type": "error",
                "message": message,
            }))
        except Exception as e:
            logger.warning(f"Error sending error message: {e}")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def run_server(host: str = "localhost", port: int = 9720) -> None:
    """CLI entry point for the server.

    This function is called by the 'pathfinder serve' command.

    Usage:
        pathfinder serve --host localhost --port 9720
    """
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    server = PathfinderServer(host=host, port=port)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pathfinder WebSocket Server")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=9720, help="Port to bind to")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
