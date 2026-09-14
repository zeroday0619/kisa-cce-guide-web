"""Build the static guide into the deployment layout required by Sites."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import rfc8785
from PIL import Image

from conversion.build_content import build
from conversion.common import repository_root
from conversion.crawler_discovery import normalize_site_origin
from conversion.parallel import default_worker_count, parse_worker_count, validate_worker_count
from conversion.paths import BUILD_DIRECTORY, DISTRIBUTION_DIRECTORY, SITE_HOSTING_DIRECTORY
from conversion.runtime_logging import add_logging_arguments, configure_runtime_logging

HOSTING_IMAGE_SCALE_DIVISOR = 2
HOSTING_IMAGE_COLOR_COUNT = 128
HOSTING_RENDERING_PROFILE = "sites-preview-png-indexed-half-v1"


def _optimize_hosting_images(client_directory: Path) -> dict[str, tuple[int, int]]:
    """Create smaller deployment derivatives without changing canonical assets."""

    dimensions_by_public_path: dict[str, tuple[int, int]] = {}
    for image_path in sorted((client_directory / "assets").rglob("*.png")):
        with Image.open(image_path) as image:
            output_width = max(1, image.width // HOSTING_IMAGE_SCALE_DIVISOR)
            output_height = max(1, image.height // HOSTING_IMAGE_SCALE_DIVISOR)
            resized_image = image.resize(
                (output_width, output_height),
                Image.Resampling.LANCZOS,
            )
            indexed_image = resized_image.convert("RGB").quantize(
                colors=HOSTING_IMAGE_COLOR_COUNT,
                method=Image.Quantize.FASTOCTREE,
            )
            indexed_image.save(
                image_path,
                format="PNG",
                optimize=False,
                compress_level=9,
            )
        public_path = "/" + image_path.relative_to(client_directory).as_posix()
        dimensions_by_public_path[public_path] = (output_width, output_height)
    return dimensions_by_public_path


def _update_hosted_html(
    client_directory: Path,
    dimensions_by_public_path: dict[str, tuple[int, int]],
) -> None:
    """Keep hosted HTML dimensions aligned with optimized image bytes."""

    replacement_count = 0
    for html_path in sorted(client_directory.rglob("*.html")):
        source = html_path.read_text(encoding="utf-8")
        for public_path, (output_width, output_height) in dimensions_by_public_path.items():
            pattern = re.compile(
                rf'(<img src="{re.escape(public_path)}"[^>]* width=")\d+(" height=")\d+("[^>]*>)'
            )
            source, count = pattern.subn(
                rf"\g<1>{output_width}\g<2>{output_height}\g<3>",
                source,
            )
            replacement_count += count
        html_path.write_text(source, encoding="utf-8")
    if replacement_count != len(dimensions_by_public_path):
        msg = (
            "hosted HTML image count differs from optimized image count: "
            f"{replacement_count} != {len(dimensions_by_public_path)}"
        )
        raise ValueError(msg)


def _update_hosted_datasets(
    client_directory: Path,
    dimensions_by_public_path: dict[str, tuple[int, int]],
) -> None:
    """Mark hosted image records as deployment-specific derivatives."""

    updated_image_count = 0
    dataset_directory = client_directory / "dataset" / "criteria"
    for dataset_path in sorted(dataset_directory.rglob("*.json")):
        document = json.loads(dataset_path.read_text(encoding="utf-8"))
        for block in document["blocks"]:
            if block.get("blockType") != "image":
                continue
            public_path = "/" + block["assetPath"].lstrip("/")
            output_dimensions = dimensions_by_public_path.get(public_path)
            if output_dimensions is None:
                msg = f"hosted dataset references an unknown image: {public_path}"
                raise ValueError(msg)
            block["assetType"] = "webOptimizedDerivative"
            block["renderingProfileIdentifier"] = HOSTING_RENDERING_PROFILE
            block["outputPixelDimensions"] = list(output_dimensions)
            updated_image_count += 1
        dataset_path.write_bytes(rfc8785.dumps(document))
    if updated_image_count != len(dimensions_by_public_path):
        msg = (
            "hosted dataset image count differs from optimized image count: "
            f"{updated_image_count} != {len(dimensions_by_public_path)}"
        )
        raise ValueError(msg)


def build_sites_bundle(
    *, root: Path | None = None, workers: int = 1, site_origin: str = ""
) -> list[Path]:
    """Build and stage the validated static site and Worker entry point."""

    repository = root or repository_root()
    build(root=repository, workers=validate_worker_count(workers), site_origin=site_origin)
    distribution_directory = repository / DISTRIBUTION_DIRECTORY
    shutil.rmtree(distribution_directory, ignore_errors=True)
    client_directory = distribution_directory / "client"
    server_directory = distribution_directory / "server"
    shutil.copytree(repository / BUILD_DIRECTORY / "site", client_directory)
    dimensions_by_public_path = _optimize_hosting_images(client_directory)
    _update_hosted_html(client_directory, dimensions_by_public_path)
    _update_hosted_datasets(client_directory, dimensions_by_public_path)
    server_directory.mkdir(parents=True)
    worker_source = repository / SITE_HOSTING_DIRECTORY / "worker.js"
    worker_target = server_directory / "index.js"
    shutil.copy2(worker_source, worker_target)
    return [
        worker_target,
        *sorted(path for path in client_directory.rglob("*") if path.is_file()),
    ]


def _argument_parser() -> argparse.ArgumentParser:
    """Build the Sites deployment command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        type=parse_worker_count,
        default=default_worker_count(),
        help="parallel worker processes for validation and normalization",
    )
    parser.add_argument(
        "--site-origin",
        type=normalize_site_origin,
        default="",
        help="public HTTP(S) origin for sitemap URLs; omit to skip sitemap generation",
    )
    add_logging_arguments(parser)
    return parser


def main() -> int:
    """Build the Sites deployment bundle."""

    arguments = _argument_parser().parse_args()
    with configure_runtime_logging(
        "build_sites_bundle",
        level=arguments.log_level,
        log_directory=arguments.log_directory,
    ) as logger:
        logger.info("Sites deployment build started", event="command.started")
        generated_paths = build_sites_bundle(
            workers=arguments.workers, site_origin=arguments.site_origin
        )
        logger.info(
            "Sites deployment build completed",
            event="command.completed",
            artifact_count=len(generated_paths),
            workers=arguments.workers,
        )
        print(f"generated {len(generated_paths)} Sites deployment artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
