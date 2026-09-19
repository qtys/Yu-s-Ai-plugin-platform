import base64
import io
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
import pypdfium2 as pdfium
from PIL import Image

from .database import DATA_DIR

DOCUMENT_DIR = DATA_DIR / "documents"
ALLOWED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_VISUALS = 30
MAX_VISUAL_BYTES = 30 * 1024 * 1024
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def decode_document(filename: str, encoded: str) -> tuple[bytes, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("仅支持 TXT、Markdown、PDF 和 DOCX")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("文件内容无效") from exc
    if not payload or len(payload) > MAX_FILE_BYTES:
        raise ValueError("文件不能为空且不能超过 20 MB")
    return payload, suffix


def save_original(filename: str, payload: bytes, suffix: str) -> str:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (DOCUMENT_DIR / stored_name).write_bytes(payload)
    return stored_name


def remove_original(stored_name: str) -> None:
    path = DOCUMENT_DIR / Path(stored_name).name
    if path.exists():
        path.unlink()


def extract_pages(payload: bytes, suffix: str) -> list[tuple[int | None, str]]:
    if suffix in {".txt", ".md", ".markdown"}:
        text = payload.decode("utf-8-sig", errors="replace")
        return [(None, text)]
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(payload))
        return [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = ["".join(node.text or "" for node in paragraph.iter(f"{namespace}t")) for paragraph in root.iter(f"{namespace}p")]
    return [(None, "\n".join(paragraphs))]


def _visual(name: str, data: bytes, page_number: int | None) -> dict | None:
    mime = IMAGE_MIME.get(Path(name).suffix.lower())
    if not mime or not data:
        return None
    return {"name": name, "page_number": page_number,
            "data_url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "byte_count": len(data)}


def _jpeg_visual(image: Image.Image, name: str, page_number: int) -> dict:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
    return _visual(name, output.getvalue(), page_number)  # type: ignore[return-value]


def render_pdf_pages(payload: bytes) -> list[dict]:
    """Render vector-heavy PDF pages as an overview plus readable quadrant crops."""
    document = pdfium.PdfDocument(payload)
    visuals: list[dict] = []
    regions = (("top-left", 0, 0, .52, .52), ("top-right", .48, 0, 1, .52),
               ("bottom-left", 0, .48, .52, 1), ("bottom-right", .48, .48, 1, 1))
    for page_index in range(len(document)):
        page_number = page_index + 1
        page = document[page_index]
        detailed = page.render(scale=2.0, rev_byteorder=True).to_pil()
        overview = detailed.copy()
        overview.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        visuals.append(_jpeg_visual(overview, f"page-{page_number}-overview.jpg", page_number))
        width, height = detailed.size
        for region, left, top, right, bottom in regions:
            crop = detailed.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
            visuals.append(_jpeg_visual(crop, f"page-{page_number}-{region}.jpg", page_number))
    return visuals


def extract_visuals(payload: bytes, suffix: str) -> list[dict]:
    if suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            candidates = [(Path(name).name, archive.read(name), None) for name in archive.namelist() if name.startswith("word/media/")]
    elif suffix == ".pdf":
        try:
            return render_pdf_pages(payload)[:MAX_VISUALS]
        except Exception as exc:
            raise ValueError("PDF 页面渲染失败") from exc
    else:
        candidates = []
    visuals, total_bytes = [], 0
    for name, data, page_number in candidates:
        item = _visual(name, data, page_number)
        if item is None or total_bytes + item["byte_count"] > MAX_VISUAL_BYTES:
            continue
        visuals.append(item)
        total_bytes += item["byte_count"]
        if len(visuals) >= MAX_VISUALS:
            break
    return visuals


def chunk_pages(pages: list[tuple[int | None, str]], size: int = 1800, overlap: int = 180) -> list[dict]:
    chunks: list[dict] = []
    for page, raw in pages:
        text = re.sub(r"[ \t]+", " ", raw).strip()
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind("。", start, end), text.rfind(". ", start, end))
                if boundary > start + size // 2:
                    end = boundary + 1
            part = text[start:end].strip()
            if part:
                chunks.append({"page_number": page, "content": part})
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def relevant_chunks(chunks: list[dict], query: str, limit: int = 5) -> list[dict]:
    terms = set(re.findall(r"[\w\u4e00-\u9fff]", query.lower()))
    ranked = sorted(chunks, key=lambda item: len(terms & set(item["content"].lower())), reverse=True)
    return [item for item in ranked[:limit] if item["content"]]
