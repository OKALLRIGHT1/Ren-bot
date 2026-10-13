"""Compatibility shim. The product GUI lives in this repo."""

from __future__ import annotations

import boot


def main() -> int:
    return int(boot.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
