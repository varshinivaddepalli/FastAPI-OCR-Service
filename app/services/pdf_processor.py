import os
import tempfile
import logging
from pathlib import Path
import fitz  # PyMuPDF
import pdfplumber
from groq import Groq
from rich import print as rprint
from app.config import settings
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PaddleOCR PPStructure singleton ---
_pp_structure = None
_pp_available = False

# --- PaddleOCR text engine singleton ---
_pp_text_ocr = None

try:
    from paddleocr import PPStructure as _PPStructure  # compatible across versions
    _pp_available = True
except Exception as e:
    logger.warning(f"Paddle import error: {e}")
    _pp_available = False

DEFAULT_PROMPT = '''
- Extract and process the key information from the given document and present it as a single, syntactically valid JSON object.
- The output must be STRICTLY valid JSON. Do NOT include explanations, formatting, or text outside the JSON.
- Identify logical sections as appropriate for this document. If a section is not present, set its value as null or as an empty array/object, whichever fits best.
- For main content, structure headings and subheadings as nested objects or arrays.
- When tables or images are present, describe them briefly in addition to labeling their page number and position if extractable.
- If any data appears ambiguous, make a best-effort guess but avoid hallucinating information that isn't present.
- After extracting, analyze all expense-related items using details such as item name, description, vendor name, and HSN code.
- Cross-check item categorization against real-world knowledge (e.g., common usage, product context, industry standards) instead of relying only on keyword matches.
- * Example: wooden stirrers, cups, plates, tissues → Pantry expenses.
- cloud subscription, SaaS tool, software license → Tech invoices.
- hotel stay, guest house → Accommodation.
- laptops, monitors → Electronics.
- Dinners, birthday cakes → Entertainment
- Assign each expense to one of the predefined categories: "Stationary", "Travel", "Accommodation", "Electronics", "Entertainment", "Pantry expenses", "Utilities", "Tech invoices".
- If an expense does not fit any category, classify it under "Miscellaneous".
- Finally, provide the aggregated total amount for each category
'''


def _get_pp_structure():
    global _pp_structure
    if not _pp_available:
        return None
    if _pp_structure is None:
        try:
            _pp_structure = _PPStructure(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                show_log=False
            )
        except Exception as e:
            logger.error(f"Failed to init PPStructure: {e}")
            _pp_structure = None
    return _pp_structure


def _get_text_ocr():
    global _pp_text_ocr
    if _pp_text_ocr is None:
        try:
            from paddleocr import PaddleOCR
            _pp_text_ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
        except Exception as e:
            logger.error(f"Failed to init PaddleOCR text engine: {e}")
            _pp_text_ocr = None
    return _pp_text_ocr


def clean_text(text: str) -> str:
    if not text:
        return ""
    lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(" ".join(stripped.split()))
    return "\n".join(lines)


def is_digital_pdf(file_path: str) -> bool:
    try:
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                return False
            pages_to_check = min(3, total_pages)
            text_pages = 0
            for i in range(pages_to_check):
                text = pdf.pages[i].extract_text()
                if text and len(text.strip()) > 10:
                    text_pages += 1
            return text_pages >= pages_to_check / 2
    except Exception:
        return True


def digital_pdf_content(file_path: str) -> str:
    merged = {}
    file_path = Path(file_path)
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                merged[i] = {"text": "", "tables": []}
                t = clean_text(page.extract_text())
                if t:
                    merged[i]["text"] = t
                for table in (page.extract_tables() or []):
                    if table and any(any(c for c in row) for row in table):
                        table_str = "\n".join(" | ".join(c or "" for c in row) for row in table if any(row))
                        merged[i]["tables"].append(table_str.strip())
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")

    try:
        doc = fitz.open(file_path)
        for i, page in enumerate(doc, start=1):
            t = clean_text(page.get_text())
            if t:
                merged.setdefault(i, {"text": "", "tables": []})
                merged[i]["text"] = (merged[i]["text"] + "\n" + t).strip()
        doc.close()
    except Exception as e:
        logger.warning(f"PyMuPDF failed: {e}")

    out = []
    for i in sorted(merged.keys()):
        out.append(f"\n=== Page {i} ===\n")
        if merged[i]["text"]:
            out.append(f"[Text]\n{merged[i]['text']}\n")
        for ti, tbl in enumerate(merged[i]["tables"], start=1):
            out.append(f"\n[Table {ti}]\n{tbl}\n")
    return "".join(out).strip()


def _structure_ocr(file_path: str) -> tuple[str, int]:
    pipeline = _get_pp_structure()
    if not pipeline:
        return "", 0
    try:
        results = pipeline(str(file_path))
    except Exception as e:
        logger.error(f"PPStructure call failed: {e}")
        return "", 0

    text_chars = 0
    out = []
    if isinstance(results, list):
        for page_idx, res in enumerate(results, start=1):
            out.append(f"\n=== Page {page_idx} ===\n")
            page_lines: List[str] = []
            if isinstance(res, dict):
                ocr_res = res.get("overall_ocr_res")
                if isinstance(ocr_res, dict):
                    for t in ocr_res.get("rec_texts", []) or []:
                        if t:
                            page_lines.append(str(t))
                tbls = res.get("table_res_list")
                if isinstance(tbls, list):
                    for ti, tbl in enumerate(tbls, start=1):
                        if isinstance(tbl, dict) and tbl.get("pred_html"):
                            out.append(f"\n[Table {ti}]\n{tbl['pred_html']}\n")
            if page_lines:
                text = "\n".join(clean_text(t) for t in page_lines)
                text_chars += len(text)
                out.append(f"[Text]\n{text}\n")
            else:
                out.append("[Text]\n[No text detected by PPStructure]\n")
    return "".join(out).strip(), text_chars


def _raster_text_ocr(file_path: str, zoom: float = 3.0) -> str:
    ocr = _get_text_ocr()
    if not ocr:
        return ""
    try:
        from PIL import Image, ImageOps
    except Exception:
        Image = None
        ImageOps = None
    try:
        doc = fitz.open(file_path)
        out = []
        for pidx in range(len(doc)):
            page = doc.load_page(pidx)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            with tempfile.NamedTemporaryFile(delete=True, suffix=".png") as tmp:
                img_bytes = pix.tobytes("png")
                if Image is not None:
                    try:
                        # Simple preprocessing to help OCR
                        import io
                        img = Image.open(io.BytesIO(img_bytes)).convert("L")
                        img = ImageOps.autocontrast(img)
                        img.save(tmp.name, format="PNG")
                    except Exception:
                        tmp.write(img_bytes)
                        tmp.flush()
                else:
                    tmp.write(img_bytes)
                    tmp.flush()
                result = ocr.ocr(tmp.name, cls=True)
            lines: List[str] = []
            for block in result or []:
                for line in block or []:
                    try:
                        txt = line[1][0]
                        if txt:
                            lines.append(txt)
                    except Exception:
                        continue
            out.append(f"\n=== Page {pidx + 1} ===\n")
            if lines:
                out.append("[Text]\n" + "\n".join(clean_text(t) for t in lines) + "\n")
            else:
                out.append("[Text]\n[No text detected by raster OCR]\n")
        doc.close()
        return "".join(out).strip()
    except Exception as e:
        logger.error(f"Raster OCR failed: {e}")
        return ""


def scanned_pdf_content(file_path: str) -> str:
    logger.info("Processing scanned PDF with PPStructure + text OCR")
    file_path = Path(file_path)

    struct_text, chars = _structure_ocr(str(file_path))
    raster_text = _raster_text_ocr(str(file_path), zoom=3.0)

    # Always prefer raster text as it contains the actual OCR content
    if raster_text and len(raster_text.strip()) > 20:
        logger.info(f"Using raster OCR text: {len(raster_text)} characters")
        return raster_text
    
    # Fallback to structure OCR if raster failed
    if struct_text and len(struct_text.strip()) > 20:
        logger.info(f"Using structure OCR text: {len(struct_text)} characters")
        return struct_text
    
    # If both failed, return empty
    logger.warning("Both OCR methods failed to extract meaningful text")
    return ""


def process_with_llm(text: str) -> str:
    logger.info("Processing with Groq LLM")
    if not text or len(text.strip()) < 10:
        raise ValueError("Insufficient text extracted for LLM processing")
    try:
        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": DEFAULT_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0,
            max_tokens=4000
        )
        return response.choices[0].message.content  # type: ignore
    except Exception as e:
        logger.error(f"LLM processing failed: {e}")
        raise RuntimeError(f"LLM processing failed: {e}")


def extract_json_from_pdf(temp_pdf_path: str) -> str:
    logger.info(f"Starting PDF extraction: {temp_pdf_path}")
    if is_digital_pdf(temp_pdf_path):
        rprint("Digital PDF detected")
        text = digital_pdf_content(temp_pdf_path)
    else:
        rprint("Scanned PDF detected")
        text = scanned_pdf_content(temp_pdf_path)
    if not text or len(text.strip()) < 20:
        raise ValueError("No meaningful text extracted from the PDF")
    final_json = process_with_llm(text)
    try:
        import json
        json.loads(final_json)
    except Exception as e:
        raise ValueError(f"LLM returned invalid JSON: {e}")
    return final_json
