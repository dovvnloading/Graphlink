"""Best-effort page provenance extracted without additional network requests."""

from __future__ import annotations

from datetime import date
import json
import re
from urllib.parse import urlsplit


def _date(value: object) -> str:
    """Return an actual ISO calendar date, never a guessed publication date."""
    if not isinstance(value, str):
        return ""
    match = re.match(r"^\s*(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$", value)
    if not match:
        return ""
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return ""


def html_provenance(soup, url: str) -> tuple[str, str]:
    """Publisher and publication date from HTML metadata (or host/unknown)."""
    publisher = urlsplit(url).hostname or "Unknown"
    published = ""
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or tag.get("itemprop") or "").lower()
        content = str(tag.get("content") or "").strip()
        if key in {"og:site_name", "application-name", "citation_publisher", "publisher"} and content:
            publisher = content[:200]
        if key in {"article:published_time", "datepublished", "date", "dc.date", "citation_date", "pubdate"}:
            published = published or _date(content)
    # Common schema.org NewsArticle/Article publisher metadata.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            nodes_to_visit = graph if isinstance(graph, list) else [node]
            for item in nodes_to_visit:
                if not isinstance(item, dict):
                    continue
                named = item.get("publisher")
                if isinstance(named, dict):
                    named = named.get("name")
                if isinstance(named, str) and named.strip() and publisher == (urlsplit(url).hostname or "Unknown"):
                    publisher = named.strip()[:200]
                published = published or _date(item.get("datePublished"))
    if not published:
        for time_tag in soup.find_all("time", datetime=True):
            published = _date(time_tag.get("datetime"))
            if published:
                break
    return publisher, published
