"""CLI entry point for mlx-task-router."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Force unbuffered output for launchd compatibility
os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from mlx_task_router import __version__


def main():
    parser = argparse.ArgumentParser(
        prog="mlx-router",
        description="Smart proxy routing mundane CLI tasks to local MLX models",
    )
    parser.add_argument(
        "--version", action="version", version=f"mlx-router {__version__}"
    )

    sub = parser.add_subparsers(dest="command")

    # --- serve (default) ---
    serve_parser = sub.add_parser("serve", help="Start the proxy server")
    serve_parser.add_argument("--host", default=None, help="Bind address")
    serve_parser.add_argument("--port", type=int, default=None, help="Bind port")
    serve_parser.add_argument("--model", default=None, help="MLX model to load (HuggingFace path)")

    # --- init ---
    sub.add_parser("init", help="Create config directory and example .env")

    args = parser.parse_args()

    if args.command == "init":
        _init_config()
    else:
        _serve(args)


def _serve(args):
    from mlx_task_router.config import config

    if hasattr(args, "host") and args.host:
        config.host = args.host
    if hasattr(args, "port") and args.port:
        config.port = args.port
    if hasattr(args, "model") and args.model:
        config.model_name = args.model

    import uvicorn

    from mlx_task_router.server import app

    print(f"MLX Task Router v{__version__}")
    print(f"  Listening on {config.host}:{config.port}")
    print(f"  Model: {config.model_name}")
    print(f"  Upstream API: {config.anthropic_api_url}")
    print()
    print("Configure Claude Code:")
    print(f"  export ANTHROPIC_BASE_URL=http://localhost:{config.port}")
    print()

    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")


def _init_config():
    config_dir = Path.home() / ".config" / "mlx-task-router"
    env_file = config_dir / ".env"

    config_dir.mkdir(parents=True, exist_ok=True)

    if env_file.exists():
        print(f"Config already exists at {env_file}")
        print("Edit it manually or delete it to reinitialize.")
        return

    example = Path(__file__).parent.parent.parent / ".env.example"
    if example.exists():
        env_file.write_text(example.read_text())
    else:
        env_file.write_text(
            "# MLX Task Router Config\n"
            "# See: https://github.com/sealmindset/mlx-task-router\n\n"
            "ANTHROPIC_API_KEY=sk-ant-...\n"
            "MLX_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit\n"
            "PORT=8888\n"
        )

    print(f"Created config at {env_file}")
    print(f"Edit {env_file} to add your ANTHROPIC_API_KEY")


if __name__ == "__main__":
    main()
