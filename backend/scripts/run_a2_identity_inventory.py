from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from app.composition.identity_remediation import run_identity_inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-a2-identity-inventory",
        description="Run anonymous A2 identity inventory against an approved disposable database.",
    )
    parser.add_argument("--format", choices=("json",), default="json")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    arguments = list(sys.argv[1:] if argv is None else argv)
    allowed = (
        arguments == []
        or arguments == ["--format", "json"]
        or arguments == ["--format=json"]
        or arguments == ["-h"]
        or arguments == ["--help"]
    )
    if not allowed:
        _parser().error("unsupported argument")
    return _parser().parse_args(arguments)


def main(argv: Sequence[str] | None = None) -> int:
    parse_args(argv)
    report = asyncio.run(run_identity_inventory())
    print(json.dumps(report.to_public_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
