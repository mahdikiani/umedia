"""Runs the real Hugging Face plugin backend against `FakeBucketApi`, as a
genuine plugin process on a Unix socket, so the shared contract suite can
exercise it without a Hub account."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.huggingface.backend import HuggingFaceBackend
from plugins.sdk import run_plugin
from tests.fixtures.fake_hf_bucket import FakeBucketApi

if __name__ == "__main__":
    fake = FakeBucketApi()
    backend = HuggingFaceBackend(
        api_factory=lambda **_: fake,
        transport=fake.transport(),
    )
    asyncio.run(run_plugin(backend))
