from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from app.composition.identity_remediation_runner import run_identity_remediation
from app.modules.auth.identity_remediation_application import (
    IdentityRemediationContractError,
)


class _SafeArgumentError(SystemExit):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _SafeArgumentError(2)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="run-a2-identity-remediation",
        description="Run controlled A2 identity remediation against an approved disposable database.",
        allow_abbrev=False,
    )
    parser.add_argument("--format", choices=("json",), default="json")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return _parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parse_args(argv)
    except _SafeArgumentError:
        print(
            json.dumps(
                {"status": "FAILED", "code": "A2_REMEDIATION_ARGUMENT_INVALID"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        summary = asyncio.run(run_identity_remediation())
    except asyncio.CancelledError:
        raise
    except IdentityRemediationContractError as error:
        code = str(error)
        if not code.startswith("A2_REMEDIATION_"):
            code = "A2_REMEDIATION_CONTROLLED_FAILURE"
        print(
            json.dumps({"status": "FAILED", "code": code}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {"status": "FAILED", "code": "A2_REMEDIATION_INTERNAL_FAILURE"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    else:
        print(json.dumps(summary.to_public_dict(), ensure_ascii=False, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
