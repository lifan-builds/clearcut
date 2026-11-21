from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from deal_common.portal.definition import default_portal_catalogs
from deal_common.portal.web import create_portal_app


@click.group()
def cli() -> None:
    """Serve the Clearcut multi-catalog front-door portal."""


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8760, type=int, show_default=True)
@click.option("--public-url", default=None, help="Canonical HTTPS origin for Clearcut.")
@click.option("--snow-url", default="https://freshpowder.clifan.com", show_default=True)
@click.option("--cycling-url", default="https://bigring.clifan.com", show_default=True)
@click.option("--tools-url", default="https://benchdrop.clifan.com", show_default=True)
@click.option("--snow-db", type=click.Path(path_type=Path), default=None)
@click.option("--cycling-db", type=click.Path(path_type=Path), default=None)
@click.option("--tools-db", type=click.Path(path_type=Path), default=None)
def serve(
    host: str,
    port: int,
    public_url: str | None,
    snow_url: str,
    cycling_url: str,
    tools_url: str,
    snow_db: Path | None,
    cycling_db: Path | None,
    tools_db: Path | None,
) -> None:
    catalogs = default_portal_catalogs(
        snow_url=snow_url,
        cycling_url=cycling_url,
        tools_url=tools_url,
        snow_db=snow_db,
        cycling_db=cycling_db,
        tools_db=tools_db,
    )
    uvicorn.run(create_portal_app(catalogs, public_url=public_url), host=host, port=port, log_level="info")


if __name__ == "__main__":
    cli()
