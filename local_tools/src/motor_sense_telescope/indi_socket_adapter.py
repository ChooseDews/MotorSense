"""Small Unix-socket client used by an INDI custom-mount driver.

This intentionally contains no INDI transport implementation: it is the thin
adapter boundary for a native INDI driver, which forwards mount properties as
the documented socket commands. It is also useful to exercise that boundary
without KStars.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .app import default_socket_path


async def request(path: Path, command: str) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((command + "\n").encode()); await writer.drain()
    answer = json.loads((await reader.readline()).decode())
    writer.close(); await writer.wait_closed()
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description="INDI custom-mount socket adapter")
    parser.add_argument("command", nargs="+", help="Socket command, e.g. goto_radec 12.5 42.2")
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    args = parser.parse_args()
    print(json.dumps(asyncio.run(request(args.socket, " ".join(args.command))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
