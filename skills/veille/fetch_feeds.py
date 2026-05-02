#!/usr/bin/env python3
"""Fetch and parse RSS feeds from sources.yml, output TSV sorted by date."""

import gzip
import html
import io
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib

# Force UTF-8 stdout on Windows (avoids cp1252 encoding errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


def load_sources():
    """Load sources from sources.yml next to this script (no PyYAML needed)."""
    sources_path = Path(__file__).parent / "sources.yml"
    sources = []
    current = None
    with open(sources_path) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped or stripped == "sources:":
                continue
            if stripped.startswith("- name:"):
                if current:
                    sources.append(current)
                current = {"name": stripped.split(":", 1)[1].strip()}
            elif current and ":" in stripped:
                key, val = stripped.split(":", 1)
                val = val.split("#")[0].strip()
                current[key.strip()] = val
    if current:
        sources.append(current)
    return sources


def strip_html(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = " ".join(text.split())
    return text[:150]


def parse_rss_date(date_str):
    """Parse RFC 2822 or ISO 8601 date string to datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str.strip())
    except Exception:
        pass
    # Try ISO 8601 (Atom feeds)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


_UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"
_UA_FEEDLY = "Feedly/1.0 (+http://www.feedly.com/fetcher.html; 100 subscribers)"
_UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_COMMON_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
}


def _http_get(url, ua):
    req = urllib.request.Request(url, headers={**_COMMON_HEADERS, "User-Agent": ua})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        encoding = resp.getheader("Content-Encoding", "")
        ct = resp.getheader("Content-Type", "")
    if encoding == "gzip" or (not encoding and raw[:2] == b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    elif encoding in ("deflate", "zlib"):
        raw = zlib.decompress(raw)
    return raw, ct


def fetch_feed(source, cutoff_date):
    """Fetch a single RSS/Atom feed and return parsed articles."""
    url = source["url"]
    name = source["name"]
    articles = []
    data = None
    last_err = None

    for ua in (_UA_FEEDLY, _UA_BROWSER, _UA_CHROME):
        try:
            data, ct = _http_get(url, ua)
            # If server returned HTML instead of XML (e.g. login wall), treat as error
            if "text/html" in ct and b"<rss" not in data[:500] and b"<feed" not in data[:500]:
                last_err = f"ERROR: {name} - got HTML response (bot wall?)"
                data = None
                continue
            break
        except urllib.error.HTTPError as e:
            last_err = f"ERROR: {name} - HTTP Error {e.code}: {e.reason}"
            if e.code != 403:
                break  # don't retry non-403 errors
        except Exception as e:
            last_err = f"ERROR: {name} - {e}"
            break

    if data is None:
        return articles, last_err

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return articles, f"ERROR: {name} - XML parse error: {e}"

    # Handle RSS 2.0
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")

    if items:
        # RSS 2.0
        for item in items:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            date_str = item.findtext("pubDate", "")
            raw_desc = strip_html(item.findtext("description", ""))
            # JDH uses description for comment links — filter those out
            desc = "" if raw_desc.startswith("Comments") or raw_desc.startswith("http") else raw_desc
            categories = [c.text for c in item.findall("category") if c.text]
            category = categories[0] if categories else "N/A"

            pub_date = parse_rss_date(date_str)
            if pub_date and pub_date.date() >= cutoff_date:
                articles.append({
                    "date": pub_date,
                    "title": title,
                    "link": link,
                    "category": source.get("category_override") or category,
                    "description": desc,
                    "source": name,
                })
    else:
        # Try Atom format
        atom_ns = "http://www.w3.org/2005/Atom"
        entries = root.findall(f".//{{{atom_ns}}}entry")
        for entry in entries:
            title = (entry.findtext(f"{{{atom_ns}}}title") or "").strip()
            link_el = entry.find(f"{{{atom_ns}}}link[@href]")
            link = link_el.get("href", "") if link_el is not None else ""
            date_str = entry.findtext(f"{{{atom_ns}}}updated") or entry.findtext(f"{{{atom_ns}}}published") or ""
            raw_content = entry.findtext(f"{{{atom_ns}}}summary") or entry.findtext(f"{{{atom_ns}}}content") or ""
            desc = strip_html(raw_content[:2000])
            cat_el = entry.find(f"{{{atom_ns}}}category")
            category = cat_el.get("term", "N/A") if cat_el is not None else "N/A"

            pub_date = parse_rss_date(date_str)
            if pub_date and pub_date.date() >= cutoff_date:
                articles.append({
                    "date": pub_date,
                    "title": title,
                    "link": link,
                    "category": source.get("category_override") or category,
                    "description": desc,
                    "source": name,
                })

    return articles, None


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    cutoff_date = (datetime.now().date() - timedelta(days=days))

    sources = load_sources()
    all_articles = []
    errors = []

    # Fetch all feeds in parallel
    with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as pool:
        futures = {pool.submit(fetch_feed, s, cutoff_date): s for s in sources}
        for future in as_completed(futures):
            articles, error = future.result()
            all_articles.extend(articles)
            if error:
                errors.append(error)

    # Deduplicate by URL (keep first occurrence, merge source names)
    seen = {}
    for a in all_articles:
        url = a["link"]
        if url in seen:
            if a["source"] not in seen[url]["source"]:
                seen[url]["source"] += f", {a['source']}"
        else:
            seen[url] = a
    unique_articles = sorted(seen.values(), key=lambda a: a["date"], reverse=True)

    # Output errors to stderr so stdout stays clean TSV
    for err in errors:
        print(err, file=sys.stderr)

    # Output articles as TSV
    for a in unique_articles:
        date_str = a["date"].strftime("%Y-%m-%d")
        desc = a["description"] or "N/A"
        # Sanitize tabs/newlines in fields
        title = a["title"].replace("\t", " ").replace("\n", " ")
        desc = desc.replace("\t", " ").replace("\n", " ")
        cat = a["category"].replace("\t", " ")
        src = a["source"].replace("\t", " ")
        print(f"{date_str}\t{title}\t{a['link']}\t{cat}\t{desc}\t{src}")

    # Output sources section
    print("SOURCES:")
    for s in sources:
        print(f"  {s['name']}\t{s.get('site', s['url'])}\t{s.get('description', '')}")


if __name__ == "__main__":
    main()
