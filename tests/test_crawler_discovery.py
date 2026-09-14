"""Crawler discovery contracts for public origins and generated page inventories."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from conversion.crawler_discovery import normalize_site_origin, write_crawler_discovery


@pytest.mark.parametrize("base_path", ["", "/guide"])
def test_sitemap_covers_both_editions_deterministically(tmp_path: Path, base_path: str) -> None:
    """Include current directory pages once while excluding noncanonical artifacts."""

    routes = ["index.html", "unix/u-01/index.html", "revised/unix/u-01/index.html"]
    paths = [tmp_path / route for route in routes]
    paths += [tmp_path / name for name in ["404.html", "assets/site.css", "dataset.json"]]
    (tmp_path / "stale").mkdir()
    (tmp_path / "stale/index.html").write_text("old", encoding="utf-8")
    result = write_crawler_discovery(
        site_root=tmp_path,
        generated_paths=[*paths, paths[0]],
        base_path=base_path,
        site_origin="https://example.com/",
    )
    assert result == [tmp_path / "robots.txt", tmp_path / "sitemap.xml"]
    before = {path: path.read_bytes() for path in result}
    root = ET.fromstring(before[tmp_path / "sitemap.xml"])  # noqa: S314
    locations = [element.text for element in root.findall("{*}url/{*}loc")]
    assert locations == sorted(
        f"https://example.com{base_path}/{route.removesuffix('index.html')}" for route in routes
    )
    assert b"lastmod" not in before[tmp_path / "sitemap.xml"]
    assert (tmp_path / "robots.txt").read_text() == (
        f"User-agent: *\nAllow: /\n\nSitemap: https://example.com{base_path}/sitemap.xml\n"
    )
    write_crawler_discovery(
        site_root=tmp_path,
        generated_paths=list(reversed(paths)),
        base_path=base_path,
        site_origin="https://example.com",
    )
    assert before == {path: path.read_bytes() for path in result}


def test_missing_origin_removes_stale_sitemap(tmp_path: Path) -> None:
    """Avoid advertising an invented hostname or a previous deployment origin."""

    sitemap_path = tmp_path / "sitemap.xml"
    sitemap_path.write_text("stale", encoding="utf-8")
    for _ in range(2):
        assert write_crawler_discovery(
            site_root=tmp_path,
            generated_paths=[tmp_path / "index.html"],
            base_path="/guide",
            site_origin="",
        ) == [tmp_path / "robots.txt"]
        assert not sitemap_path.exists()
        assert (tmp_path / "robots.txt").read_bytes() == b"User-agent: *\nAllow: /\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("https://example.com/", "https://example.com"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://[::1]:8000/", "http://[::1]:8000"),
    ],
)
def test_normalize_site_origin(value: str, expected: str) -> None:
    """Accept explicit deployment origins and the unset default."""

    assert normalize_site_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "example.com",
        "//example.com",
        "ftp://example.com",
        "https://",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?query",
        "https://example.com?",
        "https://example.com#fragment",
        "https://example.com#",
        " https://example.com",
        "https://exam ple.com",
        "https://example.com\n",
        "https://example.com:bad",
        "https://example.com:65536",
        "https://example.com:",
        "http://[invalid]",
        "https://example.com\\path",
        "https://%65xample.com",
    ],
)
def test_reject_malformed_origins(value: str) -> None:
    """Reject URLs that cannot safely identify the public site origin."""

    with pytest.raises(ValueError, match="Site origin"):
        normalize_site_origin(value)
