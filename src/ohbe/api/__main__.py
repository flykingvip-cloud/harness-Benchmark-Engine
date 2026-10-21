"""Serve the OHBE HTTP surface on localhost.

Install the api extra first: ``pip install -e '.[api]'``.
"""

from __future__ import annotations


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the api extra: pip install -e '.[api]'") from exc
    uvicorn.run("ohbe.api.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
