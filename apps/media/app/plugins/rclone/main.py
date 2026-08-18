"""Entrypoint for the rclone plugin process.

Launched by PluginProcessManager as
`python -m plugins.rclone.main <socket_path>` (see manifests/*.json).
One process serves every rclone-backed remote type (s3, google_drive, ...).
"""

import asyncio

from plugins.sdk import run_plugin

from .backend import RcloneBackend

if __name__ == "__main__":
    asyncio.run(run_plugin(RcloneBackend()))
