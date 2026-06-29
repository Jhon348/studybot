import re
import logging
import pdfplumber

logger = logging.getLogger(__name__)

CHAPTER_PATTERNS = [
    re.compile(r"^(cap[ií]tulo\s+\d+[\.\:\-\s]*.{0,60})$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(chapter\s+\d+[\.\:\-\s]*.{0,60})$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(\d+[\.\)]\s+[A-ZÁÉÍÓÚÜÑ][^a-z]{2,}.{0,60})$", re.MULTILINE),
    re.compile(r"^(UNIDAD\s+\d+[\.\:\-\s]*.{0,60})$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(TEMA\s+\d+[\.\:\-\s]*.{0,60})$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(PARTE\s+[IVXLCDM\d]+[\.\:\-\s]*.{0,60})$", re.IGNORECASE | re.MULTILINE),
]

MAX_CHARS = 12000
MIN_CHARS = 300


class PDFProcessor:

    def extract_chapters(self, pdf_path):
        try:
            raw_pages = self._extract_pages(pdf_path)
        except Exception as e:
            logger.error(f"Error abriendo PDF: {e}")
            return []
        if not raw_pages:
            return []
        full_text = "\n".join(p["text"] for p in raw_pages)
        chapters = self._split_by_chapters(full_text, raw_pages)
        if len(chapters) <= 1:
            chapters = self._split_by_pages(raw_pages)
        return self._trim_chapters(chapters)

    def _extract_pages(self, pdf_path):
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                text = self._clean_text(text)
                if text.strip():
                    pages.append({"page": i + 1, "text": text})
        return pages

    def _clean_text(self, text):
        lines = [l.strip() for l in text.split("\n") if not re.match(r"^\d{1,4}$", l.strip())]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return re.sub(r" {2,}", " ", text).strip()

    def _split_by_chapters(self, full_text, pages):
        splits = []
        for pattern in CHAPTER_PATTERNS:
            for m in pattern.finditer(full_text):
                splits.append((m.start(), m.group(1).strip()))
        if not splits:
            return []
        splits.sort(key=lambda x: x[0])
        deduped = [splits[0]]
        for pos, title in splits[1:]:
            if pos - deduped[-1][0] > 500:
                deduped.append((pos, title))
        chapters = []
        for i, (pos, title) in enumerate(deduped):
            end = deduped[i + 1][0] if i + 1 < len(deduped) else len(full_text)
            text = full_text[pos:end].strip()
            if len(text) >= MIN_CHARS:
                chapters.append({"title": title[:80], "text": text, "pages": []})
        return chapters

    def _split_by_pages(self, pages, pages_per_chunk=15):
        chapters = []
        for i in range(0, len(pages), pages_per_chunk):
            chunk = pages[i:i + pages_per_chunk]
            text = "\n\n".join(p["text"] for p in chunk)
            nums = [p["page"] for p in chunk]
            chapters.append({"title": f"Sección {len(chapters)+1} (págs. {nums[0]}–{nums[-1]})", "text": text, "pages": nums})
        return chapters

    def _trim_chapters(self, chapters):
        trimmed = []
        for ch in chapters:
            text = ch["text"]
            if len(text) > MAX_CHARS:
                half = MAX_CHARS // 2
                text = text[:half] + "\n\n[...]\n\n" + text[-half:]
            trimmed.append({**ch, "text": text})
        return trimmed
