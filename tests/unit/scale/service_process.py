"""A tool service to run in a process of its own, for tests that kill it."""

import asyncio

from flowgentic.tools.service import serve


class HangingPool:
    """A pool whose calls never return."""

    servers = ["fake"]

    async def start(self, wait=1, timeout=None, *, all_servers=False):
        pass

    async def call(self, tool, args, *, timeout=None, idempotent=False):
        await asyncio.sleep(3600)

    def stats(self):
        return {}

    async def close(self, timeout=10.0):
        pass


def serve_hanging(requests, replies) -> None:
    asyncio.run(serve(requests, replies, HangingPool()))
