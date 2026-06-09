from pathlib import Path
import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# CONFIG
# ============================================================

PHASE2_INPUT_PATH = Path(r"phase2_output\page_outputs_2\20160408_Finance_Act_2013")

OUTPUT_ROOT = Path("phase3_output")
FINAL_OUTPUT_ROOT = OUTPUT_ROOT / "final_documents"
REPORT_ROOT = OUTPUT_ROOT / "reports"

INCLUDE_UNCERTAIN_IN_FINAL = False

FINAL_JSON_NAME = "final_stitched_document.json"
REPORT_JSON_NAME = "phase3_stitch_report.json"

FINAL_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
REPORT_ROOT.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def safe_name(name: str) -> str:
    name = Path(name).stem if Path(name).suffix else str(name)
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    return name.strip("_")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_group_id(block: Dict[str, Any]) -> str:
    stitch_group_id = str(block.get("stitch_group_id") or "").strip()
    point_number = str(block.get("point_number") or "").strip()

    if stitch_group_id:
        return stitch_group_id

    if point_number:
        return f"point_{point_number}"

    return ""


def remove_boundary_overlap(previous: str, current: str, min_overlap: int = 30, max_overlap: int = 500) -> str:
    """
    Prevents accidental duplicate text when the model wrongly copied a little previous_tail
    into the current block.

    It checks whether the end of previous text overlaps with the beginning of current text.
    """
    previous = previous or ""
    current = current or ""

    if not previous or not current:
        return current

    prev_clean = re.sub(r"\s+", " ", previous.strip())
    curr_clean = re.sub(r"\s+", " ", current.strip())

    max_len = min(len(prev_clean), len(curr_clean), max_overlap)

    best = 0

    for size in range(min_overlap, max_len + 1):
        if prev_clean[-size:] == curr_clean[:size]:
            best = size

    if best == 0:
        return current

    # Approximate removal on original current string.
    return current[best:].lstrip()


def append_text(existing: str, new_text: str) -> str:
    existing = normalize_text(existing)
    new_text = normalize_text(new_text)

    if not existing:
        return new_text

    if not new_text:
        return existing

    new_text = remove_boundary_overlap(existing, new_text)

    if not new_text:
        return existing

    return existing.rstrip() + "\n" + new_text.lstrip()


# ============================================================
# INPUT DISCOVERY
# ============================================================

def is_phase2_page_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".json":
        return False

    try:
        data = read_json(path)
    except Exception:
        return False

    return isinstance(data, dict) and "content_blocks" in data and "page_number" in data


def folder_contains_phase2_pages(path: Path) -> bool:
    if not path.is_dir():
        return False

    return any(is_phase2_page_file(p) for p in path.glob("*.json"))


def get_document_jobs(input_path: Path) -> List[Dict[str, Path]]:
    """
    Handles:
    1. Single document folder containing Phase 2 page JSON files
    2. Parent folder containing multiple document folders
    3. Nested group folder containing document folders
    """
    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    jobs = []

    if folder_contains_phase2_pages(input_path):
        jobs.append({
            "document_name": input_path.name,
            "page_output_dir": input_path
        })
        return jobs

    # Search one level and two levels deep.
    candidate_dirs = []

    for child in sorted(input_path.iterdir()):
        if child.is_dir():
            candidate_dirs.append(child)

            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir():
                    candidate_dirs.append(grandchild)

    for folder in candidate_dirs:
        if folder_contains_phase2_pages(folder):
            jobs.append({
                "document_name": folder.name,
                "page_output_dir": folder
            })

    if not jobs:
        raise FileNotFoundError(
            f"No Phase 2 page output folders found inside: {input_path}"
        )

    return jobs


def load_phase2_pages(page_output_dir: Path) -> List[Dict[str, Any]]:
    files = sorted(page_output_dir.glob("*.json"))

    pages = []

    for file_path in files:
        try:
            data = read_json(file_path)
        except Exception as e:
            pages.append({
                "_load_error": str(e),
                "_source_path": str(file_path)
            })
            continue

        if not isinstance(data, dict):
            continue

        data["_source_path"] = str(file_path)
        pages.append(data)

    pages = [
        p for p in pages
        if "page_number" in p and "content_blocks" in p
    ]

    pages.sort(
        key=lambda x: (
            int(x.get("page_number", 0)),
            str(x.get("chunk_id", ""))
        )
    )

    return pages


# ============================================================
# STITCHING CORE
# ============================================================

def create_metadata_content(text: str) -> Dict[str, str]:
    return {
        "text": normalize_text(text),
        "type": "metadata",
        "clause_number": ""
    }


def create_clause_content(text: str, point_number: str) -> Dict[str, str]:
    return {
        "text": normalize_text(text),
        "type": "clause",
        "clause_number": str(point_number or "").strip()
    }


def stitch_document(document_name: str, phase2_pages: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    contents: List[Dict[str, str]] = []

    # group_id -> index in contents
    open_groups: Dict[str, int] = {}

    # group_id -> index in contents, including already closed groups
    latest_group_index: Dict[str, int] = {}

    warnings = []
    skipped_blocks = []
    uncertain_blocks = []

    total_blocks = 0
    stitched_blocks = 0
    metadata_blocks = 0
    ignored_blocks = 0
    uncertain_count = 0

    for page in phase2_pages:
        page_number = int(page.get("page_number", 0))
        chunk_id = str(page.get("chunk_id", ""))

        blocks = page.get("content_blocks", [])

        if not isinstance(blocks, list):
            warnings.append({
                "type": "invalid_blocks",
                "page_number": page_number,
                "chunk_id": chunk_id,
                "message": "content_blocks is not a list."
            })
            continue

        for block_index, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                continue

            total_blocks += 1

            block_id = str(block.get("block_id") or f"p{page_number:04d}_b{block_index:03d}")
            block_type = str(block.get("type") or "uncertain").strip().lower()
            text = normalize_text(block.get("text", ""))

            if not text:
                skipped_blocks.append({
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "reason": "empty_text"
                })
                continue

            if block_type == "ignore":
                ignored_blocks += 1
                skipped_blocks.append({
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "reason": "ignore_block"
                })
                continue

            if block_type == "metadata":
                contents.append(create_metadata_content(text))
                metadata_blocks += 1
                continue

            if block_type == "uncertain":
                uncertain_count += 1

                uncertain_record = {
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "text_preview": text[:300],
                    "stitching_note": str(block.get("stitching_note") or "")
                }
                uncertain_blocks.append(uncertain_record)

                if INCLUDE_UNCERTAIN_IN_FINAL:
                    contents.append({
                        "text": text,
                        "type": "metadata",
                        "clause_number": ""
                    })

                continue

            if block_type != "point":
                warnings.append({
                    "type": "unknown_block_type",
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "block_type": block_type
                })
                continue

            point_number = str(block.get("point_number") or "").strip()
            group_id = make_group_id(block)

            continues_from_previous = bool(block.get("continues_from_previous", False))
            continues_to_next = bool(block.get("continues_to_next", False))

            if not group_id:
                group_id = f"unknown_point_page_{page_number}_block_{block_index}"
                warnings.append({
                    "type": "missing_group_id",
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "message": "Point block had no stitch_group_id or point_number."
                })

            target_index: Optional[int] = None

            # Best case: the point is explicitly open from previous pages.
            if continues_from_previous and group_id in open_groups:
                target_index = open_groups[group_id]

            # Recovery case: model says continuation, but open state was lost.
            elif continues_from_previous and group_id in latest_group_index:
                target_index = latest_group_index[group_id]
                warnings.append({
                    "type": "recovered_closed_group_continuation",
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "group_id": group_id,
                    "message": "Block says continues_from_previous=true, but group was not open. Appended to latest same group."
                })

            # If same group is still open, append even if model forgot continues_from_previous.
            elif group_id in open_groups:
                target_index = open_groups[group_id]
                warnings.append({
                    "type": "implicit_open_group_continuation",
                    "page_number": page_number,
                    "chunk_id": chunk_id,
                    "block_id": block_id,
                    "group_id": group_id,
                    "message": "Group was open, so block was appended even though continues_from_previous was false."
                })

            # Otherwise create a new final clause content.
            else:
                contents.append(create_clause_content(text, point_number))
                target_index = len(contents) - 1
                latest_group_index[group_id] = target_index

            # Append if target was an existing clause.
            if target_index is not None and target_index < len(contents):
                if contents[target_index]["type"] == "clause":
                    if contents[target_index]["text"] != text:
                        contents[target_index]["text"] = append_text(
                            contents[target_index]["text"],
                            text
                        )
                        stitched_blocks += 1
                else:
                    warnings.append({
                        "type": "target_not_clause",
                        "page_number": page_number,
                        "chunk_id": chunk_id,
                        "block_id": block_id,
                        "group_id": group_id,
                        "message": "Target index was not a clause item."
                    })

            latest_group_index[group_id] = target_index

            if continues_to_next:
                open_groups[group_id] = target_index
            else:
                if group_id in open_groups:
                    del open_groups[group_id]

    final_json = {
        "contents": contents
    }

    report = {
        "document_name": document_name,
        "total_pages": len(phase2_pages),
        "total_blocks": total_blocks,
        "final_content_items": len(contents),
        "metadata_blocks": metadata_blocks,
        "stitched_point_blocks": stitched_blocks,
        "ignored_blocks": ignored_blocks,
        "uncertain_blocks": uncertain_count,
        "open_groups_remaining": list(open_groups.keys()),
        "warnings": warnings,
        "skipped_blocks": skipped_blocks,
        "uncertain_block_details": uncertain_blocks
    }

    return final_json, report


# ============================================================
# MAIN
# ============================================================

def run_phase3():
    jobs = get_document_jobs(PHASE2_INPUT_PATH)

    all_reports = []

    print(f"Total Phase 2 document folders found: {len(jobs)}")

    for job in jobs:
        document_name = str(job["document_name"])
        page_output_dir = Path(job["page_output_dir"])

        print("\n" + "=" * 80)
        print(f"PHASE 3 STITCHING: {document_name}")
        print("=" * 80)
        print(f"Reading Phase 2 outputs from: {page_output_dir}")

        phase2_pages = load_phase2_pages(page_output_dir)

        final_json, report = stitch_document(document_name, phase2_pages)

        final_output_dir = FINAL_OUTPUT_ROOT / safe_name(document_name)
        report_output_dir = REPORT_ROOT / safe_name(document_name)

        final_path = final_output_dir / FINAL_JSON_NAME
        report_path = report_output_dir / REPORT_JSON_NAME

        write_json(final_path, final_json)
        write_json(report_path, report)

        all_reports.append({
            "document_name": document_name,
            "phase2_page_output_dir": str(page_output_dir),
            "final_output_path": str(final_path),
            "report_path": str(report_path),
            "total_pages": report["total_pages"],
            "total_blocks": report["total_blocks"],
            "final_content_items": report["final_content_items"],
            "warnings": len(report["warnings"]),
            "uncertain_blocks": report["uncertain_blocks"],
            "open_groups_remaining": report["open_groups_remaining"]
        })

        print(f"Pages read: {report['total_pages']}")
        print(f"Blocks read: {report['total_blocks']}")
        print(f"Final content items: {report['final_content_items']}")
        print(f"Warnings: {len(report['warnings'])}")
        print(f"Uncertain blocks: {report['uncertain_blocks']}")
        print(f"Open groups remaining: {report['open_groups_remaining']}")
        print(f"Saved final JSON: {final_path}")
        print(f"Saved report: {report_path}")

    master_report = {
        "input_path": str(PHASE2_INPUT_PATH),
        "total_documents": len(all_reports),
        "documents": all_reports
    }

    master_report_path = REPORT_ROOT / "phase3_master_report.json"
    write_json(master_report_path, master_report)

    print("\n" + "=" * 80)
    print("PHASE 3 COMPLETE")
    print("=" * 80)
    print(f"Documents processed: {len(all_reports)}")
    print(f"Master report: {master_report_path}")


if __name__ == "__main__":
    run_phase3()