"""Read-only live RSS smoke test; does not call a model or consume API tokens."""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.proactive import recent_headlines


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.chinanews.com.cn/rss/scroll-news.xml"
    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        items = await recent_headlines(client, url)
    print(f"Eligible headlines in the last 48 hours: {len(items)}")
    if not items:
        raise SystemExit("No recent dated HTTPS news items available")


if __name__ == "__main__":
    asyncio.run(main())
