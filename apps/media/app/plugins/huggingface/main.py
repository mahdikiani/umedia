"""Entrypoint for the Hugging Face Buckets plugin process.

Launched by PluginProcessManager as
`python -m plugins.huggingface.main <socket_path>` (see plugin.json).
"""

import asyncio

from plugins.sdk import run_plugin

from .backend import HuggingFaceBackend

if __name__ == "__main__":
    asyncio.run(run_plugin(HuggingFaceBackend()))
