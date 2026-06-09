from pathlib import Path
import subprocess
import json
import time
import shutil
import re


# =========================
# CONFIG
# =========================

INPUT_PATH = Path(r"Documents\20160408_Finance_Act_2013.pdf")    # Can be a single PDF file or a folder containing multiple PDFs

# Report will be saved inside:
# phase1_output/reports/<code_file_name>/
SCRIPT_NAME = Path(__file__).stem
REPORT_DIR = Path("phase1_output/reports") / SCRIPT_NAME

METHOD = "hf"
MAX_OUTPUT_TOKENS = 12384
TIMEOUT_SECONDS = 60 * 20
INCLUDE_HEADERS_FOOTERS = False # Set to True to include headers and footers in OCR output

if not INCLUDE_HEADERS_FOOTERS:
    OUTPUT_DIR = Path("phase1_output/page_by_page_ocr/without_headers_footers")
else:
    OUTPUT_DIR = Path("phase1_output/page_by_page_ocr")



# =========================
# SETUP
# =========================

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# HELPERS
# =========================

def safe_name(name: str) -> str:
    name = Path(name).stem if Path(name).suffix else str(name)
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    return name.strip("_")


# =========================
# INPUT DISCOVERY
# =========================

def get_pdf_files(input_path: Path):
    """
    Handles both:
    1. Single PDF file path
    2. Folder path containing multiple PDFs
    """

    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"Input file is not a PDF: {input_path}")
        return [input_path]

    if input_path.is_dir():
        pdf_files = sorted(input_path.rglob("*.pdf"))

        if not pdf_files:
            raise FileNotFoundError(f"No PDF files found inside folder: {input_path}")

        return pdf_files

    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def get_pdf_page_count(pdf_path: Path) -> int:
    """
    Gets page count without hardcoding.
    Requires pypdf.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("Please install pypdf first: pip install pypdf")

    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def compute_sanity(text: str):
    clean = text.strip()
    warnings = []

    if not clean:
        warnings.append("empty_page")

    if len(clean) < 80:
        warnings.append("very_short_text")

    if clean.count("�") > 5:
        warnings.append("encoding_noise")

    bangla_chars = sum(1 for c in clean if "\u0980" <= c <= "\u09FF")
    english_chars = sum(1 for c in clean if c.isascii() and c.isalpha())

    if bangla_chars == 0 and english_chars == 0:
        warnings.append("no_language_detected")

    return {
        "char_count": len(clean),
        "line_count": len(clean.splitlines()),
        "bangla_chars": bangla_chars,
        "english_chars": english_chars,
        "warnings": warnings,
        "status": "warning" if warnings else "ok"
    }


def simple_page_sanity(page_results):
    failed_pages = [item for item in page_results if not item["success"]]
    warning_pages = [
        item for item in page_results
        if item["success"] and item.get("warnings")
    ]

    if failed_pages:
        return {
            "status": "failed",
            "message": "Some pages failed during OCR."
        }

    if warning_pages:
        return {
            "status": "completed_with_warnings",
            "message": "OCR completed, but some pages have warnings."
        }

    return {
        "status": "success",
        "message": "OCR completed successfully."
    }


# =========================
# CHANDRA PAGE OCR RUNNER
# =========================

def run_chandra_single_page(pdf_path: Path, page_number: int, pdf_output_dir: Path):
    temp_dir = pdf_output_dir / f"temp_page_{page_number:04d}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "chandra",
        str(pdf_path),
        str(temp_dir),
        "--method", METHOD,
        "--page-range", str(page_number),
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS)
    ]

    print("Running command:", " ".join(command))
    
    if INCLUDE_HEADERS_FOOTERS:
        command.append("--include-headers-footers")
    else:
        command.append("--no-headers-footers")

    start = time.time()

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS
    )

    elapsed = round(time.time() - start, 2)

    if result.returncode != 0:
        return {
            "success": False,
            "page_number": page_number,
            "elapsed_seconds": elapsed,
            "warnings": ["ocr_failed"]
        }

    md_file = next(temp_dir.rglob("*.md"), None)
    html_file = next(temp_dir.rglob("*.html"), None)
    meta_file = next(temp_dir.rglob("*metadata.json"), None)

    md_text = md_file.read_text(encoding="utf-8", errors="replace") if md_file else ""
    html_text = html_file.read_text(encoding="utf-8", errors="replace") if html_file else ""

    metadata = {}
    if meta_file:
        metadata = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))

    sanity = compute_sanity(md_text)

    page_json = {
        "document_id": safe_name(pdf_path.name),
        "source_file": str(pdf_path),
        "page_number": page_number,
        "ocr": {
            "engine": "chandra_ocr_2",
            "method": METHOD
        },
        "content": {
            "raw_markdown": md_text.strip(),
            "raw_html": html_text.strip()
        },
        "metadata": metadata,
        "sanity": sanity
    }

    out_path = pdf_output_dir / f"page_{page_number:04d}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(page_json, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "page_number": page_number,
        "elapsed_seconds": elapsed,
        "output_path": str(out_path),
        "warnings": sanity["warnings"]
    }


def run_page_level_ocr(pdf_path: Path):
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if INPUT_PATH.is_file():
        document_output_dir = OUTPUT_DIR / pdf_path.stem

    else:
        document_output_dir = OUTPUT_DIR / INPUT_PATH.stem / safe_name(pdf_path.stem)

    document_output_dir.mkdir(parents=True, exist_ok=True)

    total_pages = get_pdf_page_count(pdf_path)

    print("\nRunning page-level Chandra OCR...")
    print("PDF:", pdf_path)
    print("Document output folder:", document_output_dir)
    print(f"Total pages: {total_pages}")

    page_results = []

    start = time.time()

    for page_number in range(0, total_pages):
        print(f"[{page_number + 1}/{total_pages}] OCR: {pdf_path.name}")

        result = run_chandra_single_page(pdf_path, page_number, document_output_dir)
        page_results.append(result)

        page_status = "success" if result["success"] else "failed"

        print(
            f"Page {page_number:04d} status: {page_status} "
            f"| elapsed: {result['elapsed_seconds']}s "
            f"| warnings: {result.get('warnings', [])}"
        )

    elapsed = round(time.time() - start, 2)

    success_count = sum(1 for item in page_results if item["success"])
    failed_count = total_pages - success_count

    failed_page_numbers = [
        item["page_number"]
        for item in page_results
        if not item["success"]
    ]

    warning_page_numbers = [
        item["page_number"]
        for item in page_results
        if item["success"] and item.get("warnings")
    ]

    sanity = simple_page_sanity(page_results)

    print(f"Completed: {pdf_path.name}")
    print(f"Success pages: {success_count}")
    print(f"Failed pages: {failed_count}")

    if failed_page_numbers:
        print(f"Failed page numbers: {failed_page_numbers}")
    else:
        print("Failed page numbers: None")

    return {
        "pdf": str(pdf_path),
        "status": sanity["status"],
        "message": sanity["message"],
        "elapsed_seconds": elapsed,
        "total_pages": total_pages,
        "successful_pages": success_count,
        "failed_pages": failed_count,
        "failed_page_numbers": failed_page_numbers,
        "warning_page_numbers": warning_page_numbers,
        "page_results": page_results,
        "document_output_dir": str(document_output_dir)
    }


# =========================
# REPORT
# =========================

def run_simple_sanity_report(run_results):
    failed_runs = [item for item in run_results if item["status"] == "failed"]
    warning_runs = [item for item in run_results if item["status"] == "completed_with_warnings"]

    final_status = "success"

    if failed_runs:
        final_status = "failed"
    elif warning_runs:
        final_status = "completed_with_warnings"

    total_pages = sum(item["total_pages"] for item in run_results)
    successful_pages = sum(item["successful_pages"] for item in run_results)
    failed_pages = sum(item["failed_pages"] for item in run_results)

    failed_pages_by_pdf = {}

    for item in run_results:
        failed_page_numbers = item.get("failed_page_numbers", [])

        if failed_page_numbers:
            failed_pages_by_pdf[item["pdf"]] = failed_page_numbers

    report = {
        "input_path": str(INPUT_PATH),
        "include_headers_footers": INCLUDE_HEADERS_FOOTERS,
        "run_status": final_status,
        "total_pdfs": len(run_results),
        "successful_pdfs": sum(1 for item in run_results if item["status"] != "failed"),
        "failed_pdfs": len(failed_runs),
        "total_pages": total_pages,
        "successful_pages": successful_pages,
        "failed_pages": failed_pages,
        "failed_pages_by_pdf": failed_pages_by_pdf,
        "pdf_runs": run_results
    }

    report_name = ""
    
    if not INCLUDE_HEADERS_FOOTERS:
        report_name = f"{safe_name(INPUT_PATH.name)}_report_without_headers_footers.json"
    else:
        report_name = f"{safe_name(INPUT_PATH.name)}_report.json"
        
    report_path = REPORT_DIR / report_name

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 80)
    print("PAGE-LEVEL CHANDRA OCR RUN SUMMARY")
    print("=" * 80)
    print(f"Input: {INPUT_PATH}")
    print(f"Run status: {final_status}")
    print(f"Total PDFs: {report['total_pdfs']}")
    print(f"Successful PDFs: {report['successful_pdfs']}")
    print(f"Failed PDFs: {report['failed_pdfs']}")
    print(f"Total pages: {total_pages}")
    print(f"Successful pages: {successful_pages}")
    print(f"Failed pages: {failed_pages}")

    if failed_pages_by_pdf:
        print("\nFailed pages by PDF:")
        for pdf, pages in failed_pages_by_pdf.items():
            print(f"- {pdf}: {pages}")
    else:
        print("\nFailed pages by PDF: None")

    print(f"\nReport saved: {report_path}")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    pdf_files = get_pdf_files(INPUT_PATH)

    print(f"Total PDFs found: {len(pdf_files)}")

    run_results = []

    for pdf_path in pdf_files:
        result = run_page_level_ocr(pdf_path)
        run_results.append(result)

        if result["status"] == "failed":
            print(f"Failed: {pdf_path}")

    run_simple_sanity_report(run_results)