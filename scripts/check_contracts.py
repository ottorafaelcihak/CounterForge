"""Fail if committed/generated contract bytes drift; also works before first commit."""

import subprocess
from pathlib import Path

from scripts.generate_contracts import ROOT, SCHEMAS


def snapshot() -> dict[Path, bytes]:
    paths = [*SCHEMAS.glob("*.json"), ROOT / "apps/web/lib/types/api.generated.ts"]
    return {path: path.read_bytes() for path in paths if path.exists()}


def main() -> None:
    before = snapshot()
    subprocess.run(["make", "generate-contracts"], cwd=ROOT, check=True)
    after = snapshot()
    changed = sorted(
        str(path.relative_to(ROOT))
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )
    if changed:
        raise SystemExit("Contract drift: " + ", ".join(changed))
    print("Generated contracts match existing artifacts.")


if __name__ == "__main__":
    main()
