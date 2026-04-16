"""
OpenLucid MCP CLI — stdio transport entry point.

Usage:
    python -m app.mcp_cli                        # reads .env by default
    DATABASE_URL=... python -m app.mcp_cli       # explicit env var
"""
from dotenv import load_dotenv

load_dotenv()

from app.mcp_server import mcp  # noqa: E402


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
