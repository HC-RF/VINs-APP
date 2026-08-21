"""Warm the cache with the test VINs, then print a coverage report.

Useful for a demo (results appear instantly) and as a smoke test that the whole
pipeline works against live providers:

    .venv/Scripts/python scripts/seed_demo.py
    .venv/Scripts/python scripts/seed_demo.py --file data/test_vins.txt --refresh
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.base import init_db                       # noqa: E402
from app.providers.registry import close_registry     # noqa: E402
from app.schemas.vehicle import CORE_FIELDS           # noqa: E402
from app.services.decode_service import DecodeService  # noqa: E402
from app.vin.validate import parse_vin_list           # noqa: E402

DEFAULT_FILE = ROOT / "data" / "test_vins.txt"


def read_vins(path: Path) -> list[str]:
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    vins, _ = parse_vin_list("\n".join(lines))
    return vins


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the VIN cache and report coverage.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--refresh", action="store_true", help="Bypass the cache.")
    parser.add_argument("--verify", action="store_true", help="Query every provider.")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 1

    vins = read_vins(args.file)
    if not vins:
        print("No VINs found in the file.", file=sys.stderr)
        return 1

    init_db()
    service = DecodeService()
    print(f"Decoding {len(vins)} VIN(s) from {args.file.name}...\n")
    response = await service.decode_many(vins, refresh=args.refresh, verify=args.verify)

    width = max(len(r.vin) for r in response.results) + 2
    for record in response.results:
        if not record.valid:
            reason = record.errors[0]["message"] if record.errors else "invalid"
            print(f"  {record.vin:<{width}} INVALID   {reason}")
            continue

        title = " ".join(str(p) for p in (record.year, record.make, record.model) if p)
        flags = []
        if record.cached:
            flags.append("cached")
        if record.discrepancies:
            flags.append(f"{len(record.discrepancies)} conflict(s)")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        confidence = record.confidence.get("overall", "UNKNOWN")
        print(f"  {record.vin:<{width}} {confidence:<7} {title} {record.trim or ''}{suffix}")

        for d in record.discrepancies:
            print(f"      ! {d.message}")

    summary = response.summary
    print(
        f"\n  {summary.decoded} decoded, {summary.invalid} invalid, "
        f"{summary.from_cache} from cache, {summary.provider_calls} provider calls, "
        f"${summary.total_cost:.2f} spent, {summary.elapsed_ms}ms."
    )

    # Which core fields the free tier actually managed to fill.
    decoded = [r for r in response.results if r.valid]
    if decoded:
        print("\n  Core field coverage:")
        for field in CORE_FIELDS:
            have = sum(
                1 for r in decoded
                if r.fields.get(field) is not None and r.fields[field].value is not None
            )
            bar = "#" * round(20 * have / len(decoded))
            print(f"    {field:<24} {have}/{len(decoded)}  {bar}")

    await close_registry()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
