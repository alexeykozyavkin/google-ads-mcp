#!/usr/bin/env python
"""Local stdio entry point for Google Ads MCP."""

from __future__ import annotations

import asyncio

from mcp.server.stdio import stdio_server

from google_ads_mcp.coordinator import app


async def run_server_async() -> None:
    """Run the MCP server over stdio."""

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def run_server() -> None:
    """Run the stdio server."""

    asyncio.run(run_server_async())


if __name__ == "__main__":
    run_server()
