import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from .database import DATA_DIR

logger = logging.getLogger("yus_ai.translation")
MODEL_DIR = DATA_DIR / "translation-models"
DOWNLOAD_DIR = DATA_DIR / "translation-downloads"
SPEED_TEST_BYTES = 256 * 1024
MAX_RETRIES = 3
BACKUP_BASE_URL = "https://argos-net.com/v1"
PACKAGES = {
    ("zh", "en"): {"name": "中文 → English", "size_mb": 71, "url": "https://data.argosopentech.com/argospm/v1/translate-zh_en-1_9.argosmodel"},
    ("en", "zh"): {"name": "English → 中文", "size_mb": 67, "url": "https://data.argosopentech.com/argospm/v1/translate-en_zh-1_9.argosmodel"},
}


def _argos():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["ARGOS_PACKAGES_DIR"] = str(MODEL_DIR)
    from argostranslate import package, translate
    return package, translate


def installed_pairs() -> set[tuple[str, str]]:
    package, _ = _argos()
    return {(item.from_code, item.to_code) for item in package.get_installed_packages()}


def package_status() -> list[dict]:
    installed = installed_pairs()
    return [{"from_code": source, "to_code": target, **details, "installed": (source, target) in installed} for (source, target), details in PACKAGES.items()]


def _candidate_urls(details: dict, mirror_url: str = "") -> list[str]:
    file_name = details["url"].rsplit("/", 1)[-1]
    mirror = (mirror_url or os.getenv("YUS_AI_TRANSLATION_MIRROR", "")).strip().rstrip("/")
    urls = ([f"{mirror}/{file_name}"] if mirror else []) + [details["url"], f"{BACKUP_BASE_URL}/{file_name}"]
    return list(dict.fromkeys(urls))


def _source_name(url: str) -> str:
    return urlparse(url).netloc or url


async def _probe_source(client: httpx.AsyncClient, url: str) -> tuple[str, float]:
    started = time.perf_counter()
    received = 0
    async with client.stream("GET", url, headers={"Range": f"bytes=0-{SPEED_TEST_BYTES - 1}"}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received >= SPEED_TEST_BYTES:
                break
    if not received:
        raise httpx.TransportError("测速未收到数据")
    return url, received / max(time.perf_counter() - started, 0.001)


def _total_size(response: httpx.Response, existing: int) -> int:
    content_range = response.headers.get("content-range", "")
    if "/" in content_range and content_range.rsplit("/", 1)[-1].isdigit():
        return int(content_range.rsplit("/", 1)[-1])
    length = int(response.headers.get("content-length", 0))
    return existing + length if response.status_code == 206 else length


async def _download_source(client: httpx.AsyncClient, url: str, partial: Path, source_marker: Path, progress: Callable[[dict], None] | None) -> tuple[int, int]:
    saved_source = source_marker.read_text(encoding="utf-8") if source_marker.exists() else ""
    if partial.exists() and saved_source != url:
        partial.unlink(missing_ok=True)
    source_marker.write_text(url, encoding="utf-8")
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    async with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 416:
            partial.unlink(missing_ok=True)
            raise httpx.HTTPStatusError("服务器拒绝续传，将从头重试", request=response.request, response=response)
        response.raise_for_status()
        resumed = existing > 0 and response.status_code == 206
        downloaded = existing if resumed else 0
        total = _total_size(response, downloaded)
        last_percent = -1
        with partial.open("ab" if resumed else "wb") as output:
            async for chunk in response.aiter_bytes():
                output.write(chunk)
                downloaded += len(chunk)
                percent = min(92, int(downloaded * 92 / total)) if total else 0
                if progress and percent != last_percent:
                    progress({"stage": "downloading", "percent": percent, "downloaded": downloaded, "total": total, "source": _source_name(url), "resumed": resumed})
                    last_percent = percent
    return downloaded, total


async def install_package(source: str, target: str, progress: Callable[[dict], None] | None = None, mirror_url: str = "") -> None:
    details = PACKAGES.get((source, target))
    if not details:
        raise ValueError("暂不支持该语言包")
    if (source, target) in installed_pairs():
        if progress:
            progress({"stage": "complete", "percent": 100, "downloaded": 0, "total": 0})
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    archive = DOWNLOAD_DIR / f"translate-{source}_{target}.argosmodel"
    partial = archive.with_suffix(archive.suffix + ".part")
    source_marker = partial.with_suffix(partial.suffix + ".source")
    urls = _candidate_urls(details, mirror_url)
    if progress:
        progress({"stage": "testing", "percent": 0, "tested": 0, "total_sources": len(urls)})
    timeout = httpx.Timeout(300, connect=15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        probes = await asyncio.gather(*(_probe_source(client, url) for url in urls), return_exceptions=True)
        speeds = {url: speed for result in probes if not isinstance(result, Exception) for url, speed in [result]}
        ordered_urls = sorted(urls, key=lambda item: speeds.get(item, -1), reverse=True)
        logger.info("translation_sources_ranked pair=%s-%s sources=%s", source, target, [_source_name(url) for url in ordered_urls])
        if progress:
            progress({"stage": "testing", "percent": 0, "tested": len(urls), "total_sources": len(urls), "source": _source_name(ordered_urls[0])})
        last_error: Exception | None = None
        downloaded = total = 0
        completed = False
        for url in ordered_urls:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if attempt > 1 and progress:
                        progress({"stage": "retrying", "percent": 0, "attempt": attempt, "max_attempts": MAX_RETRIES, "source": _source_name(url)})
                    downloaded, total = await _download_source(client, url, partial, source_marker, progress)
                    partial.replace(archive)
                    source_marker.unlink(missing_ok=True)
                    completed = True
                    break
                except (httpx.HTTPError, OSError) as error:
                    last_error = error
                    logger.warning("translation_download_failed source=%s attempt=%d error=%s", _source_name(url), attempt, type(error).__name__)
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
            if completed:
                break
        if not completed:
            raise last_error or httpx.TransportError("所有下载源均不可用")
    if progress:
        progress({"stage": "installing", "percent": 96, "downloaded": downloaded, "total": total})
    package, _ = _argos()
    await asyncio.to_thread(package.install_from_path, archive)
    archive.unlink(missing_ok=True)
    if progress:
        progress({"stage": "complete", "percent": 100, "downloaded": downloaded, "total": total})
    logger.info("translation_model_installed pair=%s-%s", source, target)


def translate_text(text: str, source: str, target: str) -> str:
    if (source, target) not in installed_pairs():
        raise LookupError("请先下载对应的离线语言包")
    _, translate = _argos()
    return translate.translate(text, source, target)
