"""扫描受控数据库/备份是否残留 runtime_config Secret 明文。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config.secret_refs import (
    SecretRefResolutionError,
    scan_artifacts_for_plaintext_secrets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--artifact",
        required=True,
        action="append",
        type=Path,
        help="显式指定要扫描的数据库、备份、WAL 或 journal；可重复",
    )
    args = parser.parse_args(argv)
    try:
        results = scan_artifacts_for_plaintext_secrets(
            args.database,
            tuple(args.artifact),
        )
    except SecretRefResolutionError as exc:
        print(
            json.dumps(
                {"outcome": "failed", "error_type": type(exc).__name__},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    passed = all(item.passed for item in results)
    print(
        json.dumps(
            {
                "outcome": "passed" if passed else "plaintext_detected",
                "artifacts": [
                    {
                        "artifact_name": item.artifact_name,
                        "exposed_secret_count": item.exposed_secret_count,
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
