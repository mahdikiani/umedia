"""Entrypoint for the Telegram plugin process.

Launched by PluginProcessManager as
`python -m plugins.telegram.main <socket_path>` (see plugin.json).
"""

import asyncio

from plugins.sdk import run_plugin

from .backend import TelegramBackend

if __name__ == "__main__":
    asyncio.run(run_plugin(TelegramBackend()))
