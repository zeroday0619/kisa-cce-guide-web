"""Generate deterministic crawler discovery files from the current build inventory."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET


def normalize_site_origin(value: str) -> str:
    """Validate an optional HTTP origin without guessing a deployment hostname."""

    if not value:
        return ""
    message = "Site origin must be an HTTP(S) origin without credentials, path, query, or fragment."
    if any(character.isspace() or not character.isprintable() for character in value):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(message) from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or "?" in value
        or "#" in value
        or any(character in parsed.netloc for character in "\\%<>")
        or (parsed.netloc.endswith(":") and port is None)
    ):
        raise ValueError(message)
    return f"{parsed.scheme}://{parsed.netloc}"


def write_crawler_discovery(
    *,
    site_root: Path,
    generated_paths: list[Path],
    base_path: str,
    site_origin: str,
) -> list[Path]:
    """Write crawler files for canonical directory pages in this build only."""

    origin = normalize_site_origin(site_origin)
    robots_path = site_root / "robots.txt"
    sitemap_path = site_root / "sitemap.xml"
    robots = "User-agent: *\nAllow: /\n"
    if not origin:
        sitemap_path.unlink(missing_ok=True)
        robots_path.write_text(robots, encoding="utf-8", newline="\n")
        return [robots_path]

    prefix = f"/{base_path.strip('/')}" if base_path.strip("/") else ""
    locations = set()
    for path in generated_paths:
        if path.name != "index.html":
            continue
        relative = path.relative_to(site_root)
        directory = relative.parent.as_posix()
        route = "" if directory == "." else f"{directory}/"
        locations.add(f"{origin}{quote(prefix + '/' + route, safe='/')}")

    document = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for location in sorted(locations):
        entry = ET.SubElement(document, "url")
        ET.SubElement(entry, "loc").text = location
    ET.indent(document, space="  ")
    sitemap_path.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True) + b"\n")
    robots += f"\nSitemap: {origin}{quote(prefix, safe='/')}/sitemap.xml\n"
    robots_path.write_text(robots, encoding="utf-8", newline="\n")
    return [robots_path, sitemap_path]
