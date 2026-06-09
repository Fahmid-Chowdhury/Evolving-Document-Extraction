from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image
import cv2
import numpy as np
import pandas as pd
import json


# =========================
# CONFIG
# =========================

PDF_PATH = r"001-Law-1994 Germany DTAA.pdf"
PDF_FILE = Path(PDF_PATH)
PDF_NAME = PDF_FILE.stem

OUTPUT_DIR = Path("phase1_output")
IMAGE_DIR = OUTPUT_DIR / "page_images" / PDF_NAME
REPORT_DIR = OUTPUT_DIR / "reports"

DPI = 300
IMAGE_FORMAT = "png"

# Optional if Poppler is not in PATH:
POPPLER_PATH = None
# Example:
# POPPLER_PATH = r"C:\poppler\Library\bin"


# =========================
# SETUP
# =========================

IMAGE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# HELPERS
# =========================

def save_pdf_pages_as_images(pdf_path: str | Path, output_dir: Path, dpi: int = 300):
    pdf_file = Path(pdf_path)

    if not pdf_file.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_file}")

    print(f"Converting PDF: {pdf_file.name}")

    pages = convert_from_path(
        str(pdf_file),
        dpi=dpi,
    )

    saved_paths = []

    for idx, page in enumerate(pages, start=1):
        image_name = f"{pdf_file.stem}_page_{idx:04d}.{IMAGE_FORMAT}"
        image_path = output_dir / image_name

        page.save(image_path, IMAGE_FORMAT.upper())
        saved_paths.append(image_path)

        print(f"Saved page {idx}: {image_path}")

    return saved_paths


def calculate_blank_score(image_path: Path):
    """
    Higher blank_score means page is more blank.
    1.0 = almost fully white.
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)

    if img is None:
        return None

    white_pixels = np.sum(img > 245)
    total_pixels = img.size

    return white_pixels / total_pixels


def calculate_dark_pixel_ratio(image_path: Path):
    """
    Rough estimate of text/content density.
    Higher means more dark text/ink.
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)

    if img is None:
        return None

    dark_pixels = np.sum(img < 180)
    total_pixels = img.size

    return dark_pixels / total_pixels


def detect_rotation_risk(image_path: Path):
    """
    Simple heuristic:
    If width > height, page may be landscape or rotated.
    Not always wrong, but should be flagged.
    """
    with Image.open(image_path) as img:
        width, height = img.size

    if width > height:
        return True

    return False


def inspect_image(image_path: Path, page_number: int):
    with Image.open(image_path) as img:
        width, height = img.size
        mode = img.mode

    blank_score = calculate_blank_score(image_path)
    dark_ratio = calculate_dark_pixel_ratio(image_path)
    rotation_risk = detect_rotation_risk(image_path)

    warnings = []

    if blank_score is not None and blank_score > 0.985:
        warnings.append("possible_blank_page")

    if dark_ratio is not None and dark_ratio < 0.005:
        warnings.append("very_low_text_density")

    if rotation_risk:
        warnings.append("possible_rotation_or_landscape_page")

    if width < 1000 or height < 1000:
        warnings.append("low_resolution_page")

    return {
        "page_number": page_number,
        "image_path": str(image_path),
        "width": width,
        "height": height,
        "mode": mode,
        "blank_score": round(blank_score, 6) if blank_score is not None else None,
        "dark_pixel_ratio": round(dark_ratio, 6) if dark_ratio is not None else None,
        "rotation_risk": rotation_risk,
        "warnings": warnings,
        "sanity_status": "warning" if warnings else "ok"
    }


def run_image_sanity_check(image_paths):
    records = []

    for idx, image_path in enumerate(image_paths, start=1):
        record = inspect_image(image_path, idx)
        records.append(record)

    return records


def save_reports(records):
    json_path = REPORT_DIR / "phase1_image_sanity_report.json"
    csv_path = REPORT_DIR / "phase1_image_sanity_report.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(records)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"\nSaved JSON report: {json_path}")
    print(f"Saved CSV report: {csv_path}")

    return json_path, csv_path


def print_summary(records):
    total_pages = len(records)
    warning_pages = [r for r in records if r["sanity_status"] == "warning"]

    print("\n" + "=" * 80)
    print("PHASE 1A + 1B SANITY SUMMARY")
    print("=" * 80)
    print(f"Total pages converted: {total_pages}")
    print(f"Pages with warnings: {len(warning_pages)}")

    if warning_pages:
        print("\nWarning pages:")
        for r in warning_pages:
            print(
                f"Page {r['page_number']}: "
                f"{', '.join(r['warnings'])}"
            )
    else:
        print("No major image-level issues detected.")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    image_paths = save_pdf_pages_as_images(
        pdf_path=PDF_PATH,
        output_dir=IMAGE_DIR,
        dpi=DPI
    )

    sanity_records = run_image_sanity_check(image_paths)

    save_reports(sanity_records)

    print_summary(sanity_records)