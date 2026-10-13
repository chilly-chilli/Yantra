"""Read-only local MCP example; does not provide research or market data."""
from mcp.server.fastmcp import FastMCP

server = FastMCP("yantra-example")


@server.tool()
def echo(text: str) -> dict:
    """Return the supplied text to verify the MCP connection."""
    return {"text": text}


if __name__ == "__main__":
    server.run(transport="stdio")
