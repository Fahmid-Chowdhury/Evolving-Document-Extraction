from pathlib import Path
import subprocess
import json
import time
import re


# =========================
# CONFIG
# =========================

INPUT_PATH = Path("Documents/Finance Acts/")   # Can be a single PDF file or a folder containing multiple PDFs

# Report will be saved inside:
# phase1_output/reports/<code_file_name>/
SCRIPT_NAME = Path(__file__).stem
REPORT_DIR = Path("phase1_output/reports") / SCRIPT_NAME

METHOD = "hf"         # "hf" for local HuggingFace, "vllm" if chandra_vllm server is running
PAGE_RANGE = None     # Example: "1-5,7,9-12"
MAX_OUTPUT_TOKENS = 12384
INCLUDE_IMAGES = True
INCLUDE_HEADERS_FOOTERS = True  # Set to True to include headers and footers in OCR output

if not INCLUDE_HEADERS_FOOTERS:
    OUTPUT_DIR = Path("phase1_output/full_document_ocr/without_headers_footers")
else:
    OUTPUT_DIR = Path("phase1_output/full_document_ocr")


TIMEOUT_SECONDS = 60 * 60 * 3


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


# =========================
# CHANDRA OCR RUNNER
# =========================

def run_chandra_cli(pdf_path: Path):
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if INPUT_PATH.is_file():
        pdf_output_dir = OUTPUT_DIR

    else:
        pdf_output_dir = OUTPUT_DIR / INPUT_PATH.name

    pdf_output_dir.mkdir(parents=True, exist_ok=True)
    
    document_output_dir = pdf_output_dir / pdf_path.stem

    command = [
        "chandra",
        str(pdf_path),
        str(pdf_output_dir),
        "--method",
        METHOD,
        "--max-output-tokens",
        str(MAX_OUTPUT_TOKENS)
    ]

    if PAGE_RANGE:
        command.extend(["--page-range", PAGE_RANGE])

    if INCLUDE_IMAGES:
        command.append("--include-images")
    else:
        command.append("--no-images")

    if INCLUDE_HEADERS_FOOTERS:
        command.append("--include-headers-footers")
    else:
        command.append("--no-headers-footers")

    print("\nRunning Chandra OCR...")
    print("PDF:", pdf_path)
    print("Output parent folder:", pdf_output_dir)
    print("Expected document output folder:", document_output_dir)
    print("Command:", " ".join(command))

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
            "pdf": str(pdf_path),
            "status": "failed",
            "elapsed_seconds": elapsed,
            "output_dir": str(pdf_output_dir),
            "document_output_dir": str(document_output_dir)
        }

    print(f"Completed: {pdf_path.name} in {elapsed} seconds.")

    return {
        "pdf": str(pdf_path),
        "status": "success",
        "elapsed_seconds": elapsed,
        "output_dir": str(pdf_output_dir),
        "document_output_dir": str(document_output_dir)
    }


# =========================
# OUTPUT DISCOVERY
# =========================

def find_chandra_outputs(document_output_dirs):
    md_files = []
    html_files = []
    metadata_files = []
    image_files = []
    missing_dirs = []

    for document_output_dir in document_output_dirs:
        document_output_dir = Path(document_output_dir)

        if not document_output_dir.exists():
            missing_dirs.append(str(document_output_dir))
            continue

        md_files.extend(list(document_output_dir.glob("*.md")))
        html_files.extend(list(document_output_dir.glob("*.html")))
        metadata_files.extend(list(document_output_dir.glob("*metadata.json")))

        image_files.extend(
            list(document_output_dir.rglob("*.png")) +
            list(document_output_dir.rglob("*.jpg")) +
            list(document_output_dir.rglob("*.jpeg")) +
            list(document_output_dir.rglob("*.webp"))
        )

    return {
        "markdown_files": md_files,
        "html_files": html_files,
        "metadata_files": metadata_files,
        "extracted_images": image_files,
        "missing_dirs": missing_dirs
    }


def read_text_safely(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def simple_text_sanity(text: str):
    clean = text.strip()

    if not clean:
        return {
            "status": "warning",
            "message": "Output file is empty."
        }

    if len(clean) < 200:
        return {
            "status": "warning",
            "message": "Output text is very short."
        }

    return {
        "status": "ok",
        "message": "Output text was generated."
    }


def run_simple_sanity_report(run_results):
    current_document_output_dirs = sorted(
        set(item["document_output_dir"] for item in run_results)
    )

    outputs = find_chandra_outputs(current_document_output_dirs)

    markdown_files = outputs["markdown_files"]
    html_files = outputs["html_files"]
    metadata_files = outputs["metadata_files"]
    image_files = outputs["extracted_images"]
    missing_dirs = outputs["missing_dirs"]

    failed_runs = [item for item in run_results if item["status"] != "success"]

    warnings = []

    for missing_dir in missing_dirs:
        warnings.append(f"Document output folder not found: {missing_dir}")

    if not markdown_files:
        warnings.append("No markdown output found for the given input.")

    if not metadata_files:
        warnings.append("No metadata JSON found for the given input.")

    markdown_sanity = []

    for md_path in markdown_files:
        text = read_text_safely(md_path)
        sanity = simple_text_sanity(text)

        markdown_sanity.append({
            "file": str(md_path),
            "status": sanity["status"],
            "message": sanity["message"]
        })

        if sanity["status"] != "ok":
            warnings.append(f"Markdown warning: {md_path}")

    final_status = "success"

    if failed_runs:
        final_status = "failed"
    elif warnings:
        final_status = "completed_with_warnings"

    report = {
        "input_path": str(INPUT_PATH),
        "run_status": final_status,
        "total_pdfs": len(run_results),
        "successful_pdfs": sum(1 for item in run_results if item["status"] == "success"),
        "failed_pdfs": len(failed_runs),
        "document_output_dirs_checked": current_document_output_dirs,
        "output_files_found_for_given_input": {
            "markdown": len(markdown_files),
            "html": len(html_files),
            "metadata_json": len(metadata_files),
            "images": len(image_files)
        },
        "pdf_runs": run_results,
        "sanity_check": {
            "status": "ok" if not warnings else "warning",
            "warnings": warnings,
            "markdown_files": markdown_sanity
        }
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
    print("CHANDRA OCR RUN SUMMARY")
    print("=" * 80)
    print(f"Input: {INPUT_PATH}")
    print(f"Run status: {final_status}")
    print(f"Total PDFs: {report['total_pdfs']}")
    print(f"Successful PDFs: {report['successful_pdfs']}")
    print(f"Failed PDFs: {report['failed_pdfs']}")
    print(f"Markdown files found: {len(markdown_files)}")
    print(f"HTML files found: {len(html_files)}")
    print(f"Metadata files found: {len(metadata_files)}")
    print(f"Images found: {len(image_files)}")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")

    print(f"\nReport saved: {report_path}")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    pdf_files = get_pdf_files(INPUT_PATH)

    print(f"Total PDFs found: {len(pdf_files)}")

    run_results = []

    for pdf_path in pdf_files:
        result = run_chandra_cli(pdf_path)
        run_results.append(result)

        if result["status"] == "failed":
            print(f"Failed: {pdf_path}")

    run_simple_sanity_report(run_results)