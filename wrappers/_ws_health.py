"""Exit 0 if the Dulus Bar websocket is actually alive (not just TCP open)."""
from __future__ import annotations

import asyncio
import json
import sys


async def check(url: str = "ws://127.0.0.1:17372", timeout: float = 2.5) -> bool:
    try:
        import websockets
    except ImportError:
        print("NO_WEBSOCKETS")
        return False
    try:
        async with websockets.connect(url, open_timeout=timeout, close_timeout=1) as ws:
            # type=ping is ignored by the overlay (no fake "agent" in the pill)
            await ws.send(
                json.dumps(
                    {
                        "agent": "_health",
                        "type": "ping",
                        "session_id": "_health",
                        "payload": {},
                    }
                )
            )
            print("OK")
            return True
    except Exception as exc:
        print(f"FAIL:{type(exc).__name__}:{exc}")
        return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:17372"
    ok = asyncio.run(check(url))
    raise SystemExit(0 if ok else 1)
