import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bleak import BleakClient, BleakScanner

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout

    _HAS_PROMPT_TOOLKIT = True
except Exception:
    PromptSession = None  # type: ignore[assignment]
    FileHistory = None  # type: ignore[assignment]
    patch_stdout = None  # type: ignore[assignment]
    _HAS_PROMPT_TOOLKIT = False

# Nordic UART Service (NUS) UUIDs in canonical string form.
# In bt_main.c they are expressed as little-endian byte arrays.
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # client writes here
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device notifies here


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str
    address: str
    rssi: Optional[int]
    advertises_nus: bool


def _normalize_uuid(u: str) -> str:
    return u.strip().lower()


async def _discover(timeout_s: float) -> list[DiscoveredDevice]:
    devices = await BleakScanner.discover(timeout=timeout_s)

    out: list[DiscoveredDevice] = []
    for d in devices:
        name = d.name or "(unknown)"
        address = d.address
        rssi = getattr(d, "rssi", None)

        uuids = []
        md = getattr(d, "metadata", None) or {}
        if isinstance(md, dict):
            uuids = md.get("uuids") or []

        advertises_nus = any(_normalize_uuid(u) == NUS_SERVICE_UUID for u in uuids)
        out.append(
            DiscoveredDevice(
                name=name,
                address=address,
                rssi=rssi,
                advertises_nus=advertises_nus,
            )
        )

    # Prefer likely matches first.
    out.sort(key=lambda x: (not x.advertises_nus, x.name.lower(), x.address))
    return out


def _filter_by_name(devs: list[DiscoveredDevice], name_contains: str) -> list[DiscoveredDevice]:
    needle = (name_contains or "").strip().lower()
    if not needle:
        return devs
    return [d for d in devs if needle in d.name.lower()]


def _print_devices(devs: list[DiscoveredDevice]) -> None:
    if not devs:
        print("No BLE devices found.")
        return

    print("Discovered BLE devices:")
    for i, d in enumerate(devs):
        tag = " NUS" if d.advertises_nus else ""
        rssi = "" if d.rssi is None else f" RSSI={d.rssi}"
        print(f"[{i}] {d.name} ({d.address}){rssi}{tag}")


def _history_file() -> Path:
    return Path.home() / ".ble-serial-history"


async def _ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def _prompt(session: Optional["PromptSession"], prompt: str) -> str:
    if session is None:
        return await _ainput(prompt)
    return await session.prompt_async(prompt)


async def _serial_session(address: str, rx_uuid: str, tx_uuid: str) -> None:
    rx_uuid = _normalize_uuid(rx_uuid)
    tx_uuid = _normalize_uuid(tx_uuid)

    loop = asyncio.get_running_loop()

    def on_notify(_, data: bytearray) -> None:
        # Print raw bytes as UTF-8 with replacement; NUS typically sends text.
        # Notifications may arrive from a different thread depending on backend.
        b = bytes(data)

        def _emit() -> None:
            text = b.decode("utf-8", errors="replace")
            sys.stdout.write(text)
            sys.stdout.flush()

        loop.call_soon_threadsafe(_emit)

    async with BleakClient(address) as client:
        if not client.is_connected:
            raise RuntimeError("Failed to connect")

        # Bleak v2.x removed BleakClient.get_services().
        # Use UUID strings directly to avoid backend-specific service discovery APIs.
        await client.start_notify(tx_uuid, on_notify)
        print("Connected.")
        print("- Type commands (newline terminated)")
        print("- Up/Down for history")
        print("- /exit to disconnect")

        session: Optional[PromptSession]
        if _HAS_PROMPT_TOOLKIT and sys.stdin.isatty():
            session = PromptSession(history=FileHistory(str(_history_file())))
        else:
            session = None

        patch_ctx = patch_stdout() if patch_stdout is not None else None

        try:
            if patch_ctx is not None:
                patch_ctx.__enter__()

            while True:
                line = await _prompt(session, "> ")
                if line.strip() == "/exit":
                    break

                # Device expects newline-terminated commands.
                payload = (line + "\n").encode("utf-8")
                # NUS RX supports Write Without Response.
                await client.write_gatt_char(rx_uuid, payload, response=False)
        finally:
            if patch_ctx is not None:
                try:
                    patch_ctx.__exit__(None, None, None)
                except Exception:
                    pass
            try:
                await client.stop_notify(tx_uuid)
            except Exception:
                # Best-effort during disconnect.
                pass


async def _run(args: argparse.Namespace) -> int:
    session: Optional[PromptSession]
    if _HAS_PROMPT_TOOLKIT and sys.stdin.isatty():
        session = PromptSession(history=FileHistory(str(_history_file())))
    else:
        session = None

    while True:
        print(f"Scanning for BLE devices ({args.scan_timeout:.1f}s)...")
        devs = await _discover(timeout_s=args.scan_timeout)
        devs = _filter_by_name(devs, args.name_contains)
        _print_devices(devs)

        if not devs:
            choice = (await _prompt(session, "Rescan? [y/N] ")).strip().lower()
            if choice == "y":
                continue
            return 1

        if len(devs) == 1 and not args.no_auto_connect:
            target = devs[0]
            print(f"Auto-connecting to {target.name} ({target.address})...")
            try:
                await _serial_session(target.address, args.rx_uuid, args.tx_uuid)
            except Exception as e:
                print(f"Error: {e}")

            again = (await _prompt(session, "Scan again? [y/N] ")).strip().lower()
            if again == "y":
                continue
            return 0

        raw = (await _prompt(session, "Select device index (or 'r' to rescan): ")).strip().lower()
        if raw == "r":
            continue

        try:
            idx = int(raw)
        except ValueError:
            print("Invalid selection.")
            continue

        if idx < 0 or idx >= len(devs):
            print("Index out of range.")
            continue

        target = devs[idx]
        print(f"Connecting to {target.name} ({target.address})...")

        try:
            await _serial_session(target.address, args.rx_uuid, args.tx_uuid)
        except Exception as e:
            print(f"Error: {e}")

        again = (await _prompt(session, "Scan again? [y/N] ")).strip().lower()
        if again == "y":
            continue
        return 0


def main() -> None:
    p = argparse.ArgumentParser(description="BLE NUS (serial) client")
    p.add_argument("--scan-timeout", type=float, default=5.0, help="Scan duration in seconds")
    p.add_argument(
        "--name-contains",
        default="MotorSense",
        help="Only show devices whose name contains this substring (default: MotorSense). Use '' to disable.",
    )
    p.add_argument(
        "--no-auto-connect",
        action="store_true",
        help="Disable auto-connect when exactly one matching device is found.",
    )
    p.add_argument("--rx-uuid", default=NUS_RX_UUID, help="RX characteristic UUID (write)")
    p.add_argument("--tx-uuid", default=NUS_TX_UUID, help="TX characteristic UUID (notify)")

    args = p.parse_args()

    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)
