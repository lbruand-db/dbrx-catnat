"""End-to-end probe for `/api/chat`.

Drives the FastAPI app in-process (no uvicorn, no Apps gateway, no
browser) so we can see exactly what the agent loop is doing on a real
warehouse + real FMAPI call. Useful for both manual debugging and as a
template for an integration test.

Usage:

    cd packages/app
    uv run python scripts/probe_agent.py \
        --profile fevm-stable-po64og \
        --warehouse 1c97ee257092c2b3 \
        --catalog serverless_stable_po64og_catalog \
        "Affiche les communes du Rhône sur la carte."

It prints every SSE event as it arrives, with elapsed time, plus a
summary at the end. Tool results are truncated to keep the output
readable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

import httpx


def _truncate(value: Any, max_chars: int = 200) -> str:
    s = json.dumps(value, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"… ({len(s) - max_chars} more chars)"


async def probe(message: str) -> int:
    # Lazy import so env vars are set first.
    from catnat_app.backend.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
        t0 = time.monotonic()
        print(f"[+0.00s] POST /api/chat — {message!r}")

        async with client.stream(
            "POST",
            "/api/chat",
            json={"messages": [{"role": "user", "content": message}]},
            timeout=300.0,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                print(f"[!] HTTP {resp.status_code}: {body[:500]}")
                return 1

            buffer = ""
            stats = {
                "delta": 0,
                "tool_call": 0,
                "tool_result": 0,
                "map_op": 0,
                "done": 0,
                "error": 0,
            }
            tool_names: list[str] = []
            final_text = ""
            error_message = ""

            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    event = ""
                    data = ""
                    for line in frame.split("\n"):
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                    if not event:
                        continue
                    try:
                        payload = json.loads(data) if data else {}
                    except json.JSONDecodeError:
                        payload = {"_raw": data}

                    stats[event] = stats.get(event, 0) + 1
                    elapsed = time.monotonic() - t0

                    if event == "delta":
                        # Print elapsed time for the first delta only — otherwise
                        # the stream of tokens gets too noisy. After that just
                        # write the text inline.
                        if stats["delta"] == 1:
                            print(
                                f"\n[+{elapsed:5.2f}s] delta (first)",
                                flush=True,
                            )
                        sys.stdout.write(payload.get("text", ""))
                        sys.stdout.flush()
                    elif event == "tool_call":
                        name = payload.get("name", "?")
                        tool_names.append(name)
                        print(
                            f"\n[+{elapsed:5.2f}s] tool_call #{stats['tool_call']:>2} "
                            f"{name}({_truncate(payload.get('arguments', {}), 120)})"
                        )
                    elif event == "tool_result":
                        is_err = bool(payload.get("is_error"))
                        marker = "ERROR" if is_err else "ok"
                        print(
                            f"[+{elapsed:5.2f}s] tool_result      [{marker}] "
                            f"{payload.get('name', '?')} → {_truncate(payload.get('result'), 240)}"
                        )
                    elif event == "map_op":
                        op = payload.get("op", "?")
                        details = {
                            k: v for k, v in payload.items() if k not in {"geojson", "geom_geojson"}
                        }
                        if "geojson" in payload:
                            details["geojson_features"] = len(
                                payload["geojson"].get("features", [])
                            )
                        if "geom_geojson" in payload:
                            details["geom_geojson_type"] = payload["geom_geojson"].get("type")
                        print(
                            f"[+{elapsed:5.2f}s] map_op           {op}: {_truncate(details, 200)}"
                        )
                    elif event == "done":
                        final_text = payload.get("final_text", "")
                        print(f"\n[+{elapsed:5.2f}s] done — final_text len={len(final_text)}")
                    elif event == "error":
                        error_message = payload.get("message", "?")
                        print(f"\n[+{elapsed:5.2f}s] ERROR: {error_message}")

            print()
            print("=" * 60)
            print(f"Stats: {stats}")
            if tool_names:
                print(f"Tools: {tool_names}")
            if final_text:
                print(f"Final text ({len(final_text)} chars):")
                print(final_text)
            if error_message:
                print(f"Error: {error_message}")
                return 1
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="User message to send to the agent")
    parser.add_argument(
        "--profile",
        default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "fevm-stable-po64og"),
        help="Databricks CLI profile to authenticate against (default: env or fevm-stable-po64og)",
    )
    parser.add_argument(
        "--warehouse",
        default=os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "1c97ee257092c2b3"),
        help="Warehouse id (default: env or the dev workspace's)",
    )
    parser.add_argument(
        "--catalog",
        default=os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog"),
        help="Catalog name (default: env or the dev workspace's)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CATNAT_AGENT_MODEL", "databricks-claude-sonnet-4-6"),
    )
    args = parser.parse_args()

    os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    os.environ["DATABRICKS_SQL_WAREHOUSE_ID"] = args.warehouse
    os.environ["CATNAT_CATALOG"] = args.catalog
    os.environ["CATNAT_AGENT_MODEL"] = args.model

    return asyncio.run(probe(args.message))


if __name__ == "__main__":
    sys.exit(main())
