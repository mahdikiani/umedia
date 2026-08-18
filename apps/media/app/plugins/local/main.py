"""Entrypoint for the local filesystem plugin process.

Launched by PluginProcessManager as
`python -m plugins.local.main <socket_path>` (see plugin.json).
"""

import asyncio

from plugins.sdk import run_plugin

from .backend import LocalBackend

if __name__ == "__main__":
    asyncio.run(run_plugin(LocalBackend()))
