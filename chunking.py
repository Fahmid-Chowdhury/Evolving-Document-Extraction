from pathlib import Path
import json
import re


# =========================
# CONFIG
# =========================
PAGE_OCR_INPUT = Path(r"phase1_output\page_by_page_ocr\without_headers_footers\20160408_Finance_Act_2013")

# PAGE_OCR_ROOT = Path("phase1_output/page_by_page_ocr")
# OUTPUT_ROOT = Path("phase1_output/chunks")
OUTPUT_ROOT = Path("phase1_output/chunks/without_headers_footers")


TAIL_SIZE = 300
HEAD_SIZE = 300

CONTENT_TYPE = "raw_markdown"  # "raw_markdown" or "raw_html"


# =========================
# HELPERS
# =========================

def safe_name(name: str) -> str:
    name = Path(name).stem if Path(name).suffix else str(name)
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    return name.strip("_")


def is_document_folder(folder: Path) -> bool:
    """
    A document folder directly contains page_*.json files.
    """
    return folder.is_dir() and any(folder.glob("page_*.json"))


# =========================
# INPUT DISCOVERY
# =========================

def get_document_folders(input_path: Path) -> list[Path]:
    """
    Handles both:
    1. A single document OCR folder containing page_*.json files
    2. A parent folder containing multiple document OCR folders
    """

    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    if is_document_folder(input_path):
        return [input_path]

    if input_path.is_dir():
        doc_folders = [
            folder for folder in sorted(input_path.iterdir())
            if is_document_folder(folder)
        ]

        if not doc_folders:
            raise FileNotFoundError(
                f"No document folders containing page_*.json found inside: {input_path}"
            )

        return doc_folders

    raise ValueError(f"Input path must be a folder: {input_path}")


def get_output_dir_for_document(doc_folder: Path) -> Path:
    doc_folder = Path(doc_folder)
    
    if is_document_folder(PAGE_OCR_INPUT):
        return OUTPUT_ROOT / doc_folder.stem

    return OUTPUT_ROOT / f"{PAGE_OCR_INPUT.stem}_chunked" / doc_folder.stem


# =========================
# LOAD ONE DOCUMENT
# =========================

def load_pages_from_doc_folder(doc_folder: Path) -> list[dict]:
    page_files = sorted(doc_folder.glob("page_*.json"))

    pages = []

    for f in page_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        pages.append(data)

    pages.sort(key=lambda x: x.get("page_number", 0))

    return pages


# =========================
# BUILD CHUNKS FOR ONE DOCUMENT
# =========================

def build_chunks_for_document(pages: list[dict], document_name: str) -> list[dict]:
    chunks = []
    total = len(pages)

    for i, page in enumerate(pages):
        text = page["content"][CONTENT_TYPE]

        previous_tail = ""
        next_head = ""

        if i > 0:
            prev_text = pages[i - 1]["content"][CONTENT_TYPE]
            previous_tail = prev_text[-TAIL_SIZE:]

        if i < total - 1:
            next_text = pages[i + 1]["content"][CONTENT_TYPE]
            next_head = next_text[:HEAD_SIZE]

        chunk = {
            "document_name": document_name,
            "chunk_id": f"{safe_name(document_name)}_chunk_{i + 1:04d}",
            "page_number": page["page_number"],
            "text": text,
            "previous_tail": previous_tail,
            "next_head": next_head,
        }

        chunks.append(chunk)

    return chunks


# =========================
# SAVE ONE DOCUMENT CHUNKS
# =========================

def save_document_chunks(doc_folder: Path, chunks: list[dict]) -> Path:
    output_dir = get_output_dir_for_document(doc_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "chunks.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(chunks)} chunks → {output_path}")

    return output_path


# =========================
# MAIN
# =========================

def run():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    doc_folders = get_document_folders(PAGE_OCR_INPUT)

    print(f"Total document folders found: {len(doc_folders)}")

    total_docs = 0
    total_chunks = 0

    for doc_folder in doc_folders:
        document_name = doc_folder.name

        pages = load_pages_from_doc_folder(doc_folder)

        if not pages:
            print(f"Skipped empty folder: {document_name}")
            continue

        chunks = build_chunks_for_document(pages, document_name)
        save_document_chunks(doc_folder, chunks)

        total_docs += 1
        total_chunks += len(chunks)

    print("\n" + "=" * 80)
    print("CHUNKING COMPLETE")
    print("=" * 80)
    print(f"Input: {PAGE_OCR_INPUT}")
    print(f"Total documents processed: {total_docs}")
    print(f"Total chunks created: {total_chunks}")


if __name__ == "__main__":
    run()