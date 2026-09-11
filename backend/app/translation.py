import logging
from pathlib import Path

import httpx

from .database import DATA_DIR

logger = logging.getLogger("yus_ai.translation")

MODEL_DIR = DATA_DIR / "translation-models"
DOWNLOAD_DIR = DATA_DIR / "translation-downloads"
PACKAGES = {
    ("zh", "en"): {
        "name": "中文 → English",
        "size_mb": 71,
        "url": "https://data.argosopentech.com/argospm/v1/translate-zh_en-1_9.argosmodel",
    },
    ("en", "zh"): {
        "name": "English → 中文",
        "size_mb": 67,
        "url": "https://data.argosopentech.com/argospm/v1/translate-en_zh-1_9.argosmodel",
    },
}


def _argos():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["ARGOS_PACKAGES_DIR"] = str(MODEL_DIR)
    from argostranslate import package, translate
    return package, translate


def installed_pairs() -> set[tuple[str, str]]:
    package, _ = _argos()
    return {(item.from_code, item.to_code) for item in package.get_installed_packages()}


def package_status() -> list[dict]:
    installed = installed_pairs()
    return [
        {"from_code": source, "to_code": target, **details, "installed": (source, target) in installed}
        for (source, target), details in PACKAGES.items()
    ]


async def install_package(source: str, target: str) -> None:
    details = PACKAGES.get((source, target))
    if not details:
        raise ValueError("暂不支持该语言包")
    if (source, target) in installed_pairs():
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    archive = DOWNLOAD_DIR / f"translate-{source}_{target}.argosmodel"
    logger.info("translation_model_download_started pair=%s-%s", source, target)
    async with httpx.AsyncClient(timeout=300, follow_redirects=True, trust_env=False) as client:
        async with client.stream("GET", details["url"]) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    output.write(chunk)
    package, _ = _argos()
    package.install_from_path(archive)
    archive.unlink(missing_ok=True)
    logger.info("translation_model_installed pair=%s-%s", source, target)


def translate_text(text: str, source: str, target: str) -> str:
    if (source, target) not in installed_pairs():
        raise LookupError("请先下载对应的离线语言包")
    _, translate = _argos()
    return translate.translate(text, source, target)
