from pathlib import Path
import json
import time
import re
from typing import Any, Dict, List, Optional

import ollama


# ============================================================
# CONFIG
# ============================================================

INPUT_CHUNKS_PATH = Path("phase1_output/chunks/without_headers_footers/SRO-270-Finance Ministry-01 July 2010(6697-6699)/chunks.json")

OUTPUT_ROOT = Path("phase2_output")
PAGE_OUTPUT_ROOT = OUTPUT_ROOT / "page_outputs"
REPORT_ROOT = OUTPUT_ROOT / "reports"

# Change this to your local Ollama model name
OLLAMA_MODEL = "gemma4:latest"
# OLLAMA_MODEL = "gemma4:26b"
# OLLAMA_MODEL = "qwen3.5:9b"
# OLLAMA_MODEL = "llama3.1:8b"

TEMPERATURE = 0.1
TOP_P = 0.2
NUM_CTX = 32768

MAX_RETRIES = 2
STOP_ON_FAILURE = False


# =========================
# GLOBAL CHUNK RANGE CONFIG
# =========================

CHUNK_START = 0      # inclusive
CHUNK_END = 6     # exclusive; None means return until the end


# ============================================================
# SETUP
# ============================================================

PAGE_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
REPORT_ROOT.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def safe_name(name: str) -> str:
    """
    Converts a file/folder name into a safe folder/report name.
    """
    name = Path(name).stem if Path(name).suffix else str(name)
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    return name.strip("_")


def is_chunks_file(path: Path) -> bool:
    """
    True when the path is directly a chunks.json file.
    """
    path = Path(path)
    return path.is_file() and path.name == "chunks.json"


def is_document_chunks_folder(path: Path) -> bool:
    """
    True when the folder directly contains chunks.json.
    """
    path = Path(path)
    return path.is_dir() and (path / "chunks.json").exists()


def get_document_jobs(input_path: Path) -> List[Dict[str, Path]]:
    """
    Handles:
    1. Single chunks.json file
    2. Single document folder containing chunks.json
    3. Parent folder containing multiple document folders
    """

    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    jobs = []

    # Case 1: direct chunks.json file
    if is_chunks_file(input_path):
        doc_folder = input_path.parent

        jobs.append({
            "document_name": doc_folder.name,
            "chunks_path": input_path,
            "doc_folder": doc_folder
        })

        return jobs

    # Case 2: one document folder containing chunks.json
    if is_document_chunks_folder(input_path):
        jobs.append({
            "document_name": input_path.name,
            "chunks_path": input_path / "chunks.json",
            "doc_folder": input_path
        })

        return jobs

    # Case 3: parent/group folder containing multiple document folders
    if input_path.is_dir():
        doc_folders = [
            folder for folder in sorted(input_path.iterdir())
            if is_document_chunks_folder(folder)
        ]

        if not doc_folders:
            raise FileNotFoundError(
                f"No document folders containing chunks.json found inside: {input_path}"
            )

        for folder in doc_folders:
            jobs.append({
                "document_name": folder.name,
                "chunks_path": folder / "chunks.json",
                "doc_folder": folder
            })

        return jobs

    raise ValueError(f"Invalid input path: {input_path}")


def get_group_name(input_path: Path) -> Optional[str]:
    """
    Returns group name only when input is a parent folder containing
    multiple document folders.

    Single chunks.json file -> None
    Single document folder -> None
    Parent/group folder -> folder name
    """

    input_path = Path(input_path)

    if is_chunks_file(input_path):
        return None

    if is_document_chunks_folder(input_path):
        return None

    if input_path.is_dir():
        return input_path.name

    return None


def get_page_output_dir(document_name: str, group_name: Optional[str]) -> Path:
    """
    Applies output logic:

    Single document input:
        phase2_output/page_outputs/<document_name>/

    Folder/group input:
        phase2_output/page_outputs/<group_name>/<document_name>/
    """

    if group_name:
        return PAGE_OUTPUT_ROOT / group_name / document_name

    return PAGE_OUTPUT_ROOT / document_name


def get_report_path(group_name: Optional[str], document_name: Optional[str] = None) -> Path:
    """
    Applies report logic:

    Single document input:
        phase2_output/reports/<document_name>_report.json

    Folder/group input:
        phase2_output/reports/<group_name>_report.json
    """

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    if group_name:
        return REPORT_ROOT / f"{safe_name(group_name)}_report.json"

    if document_name:
        return REPORT_ROOT / f"{safe_name(document_name)}_report.json"

    return REPORT_ROOT / "phase2_extraction_report.json"


# ============================================================
# PHASE 2 TARGET SCHEMA
# ============================================================

def empty_carry_state() -> Dict[str, Any]:
    return {
        "status": "closed",
        "active_clause_number": "",
        "active_stitch_group_id": "",
        "active_block_id": "",
        "active_clause_summary": "",
        "continuation_hint_for_next_chunk": "",
        "last_visible_text": "",
        "recent_clause_history": []
    }


def default_content_block() -> Dict[str, Any]:
    return {
        "block_id": "",
        "type": "uncertain",
        "clause_number": "",
        "text": "",
        "continues_from_previous": False,
        "continues_to_next": False,
        "stitch_group_id": "",
        "stitching_note": ""
    }


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are a legal document extraction assistant.

Your task is to extract simple stitchable content blocks from OCR text.

You must return ONLY valid JSON.
Do not use markdown.
Do not explain anything.

IMPORTANT RULES:
1. Extract content only from CURRENT_PAGE_TEXT.
2. Use PREVIOUS_TAIL, NEXT_HEAD, and PREVIOUS_STATE only for deciding continuation.
3. Never copy PREVIOUS_TAIL or NEXT_HEAD into the block text.
4. Preserve the exact reading order of the current page text.
5. If tables, quoted text, explanations, substitutions, or subclauses belong to a clause, keep them inside that clause’s text until a new clause begins.
6. Do not create separate table or quote blocks.
7. Use only these block types:
   - clause
   - metadata
   - ignore
   - uncertain
8. Use "metadata" for chapter headings, document headers, notification title, dates, authority lines, signature lines, and preamble-like context.
9. Use "ignore" only for clear page numbers, repeated footers, or OCR garbage.
10. Use "uncertain" when you are not sure.
11. If a page starts in the middle of an already open clause, meaning it continues from PREVIOUS_TAIL, create a clause block and set continues_from_previous=true.
12. If the clause appears to continue into NEXT_HEAD that is the next page, set continues_to_next=true.
13. The output_carry_forward_state must summarize the active legal context for the next chunk.
14. Keep the JSON simple and do not add extra fields.
15. If there is no clause on the page, content_blocks can contain only metadata/ignore/uncertain blocks.

Allowed output schema:

{
  "chunk_id": "",
  "page_number": 0,
  "document_id": "",
  "content_blocks": [
    {
      "block_id": "",
      "type": "clause | metadata | ignore | uncertain",
      "clause_number": "",
      "text": "",
      "continues_from_previous": false | true,
      "continues_to_next": false | true,
      "stitch_group_id": "",
      "stitching_note": ""
    }
  ],
  "output_carry_forward_state": {
    "status": "open | closed | uncertain",
    "active_clause_number": "",
    "active_stitch_group_id": "",
    "active_block_id": "",
    "active_clause_summary": "",
    "continuation_hint_for_next_chunk": "",
    "last_visible_text": "",
    "recent_clause_history": [
      {
        "page_number": 0,
        "clause_number": "",
        "stitch_group_id": "",
        "summary": ""
      }
    ]
  }
}

Definitions:
- clause_number should be the main clause number only, such as "3", "৪", "Article 5", or "Section 192C".
- stitch_group_id should be stable across pages for the same clause, such as "clause_3" or "clause_৪".
- For metadata blocks, clause_number and stitch_group_id should be empty strings.
- For ignore blocks, clause_number and stitch_group_id should be empty strings.
- If the clause number is unclear but the previous state says an active clause is open, use the previous active clause number.
"""


def build_user_prompt(chunk: Dict[str, Any], previous_state: Dict[str, Any]) -> str:
    payload = {
        "chunk_id": chunk.get("chunk_id", ""),
        "page_number": chunk.get("page_number", 0),
        "document_id": chunk.get("document_name", ""),
        "previous_state": previous_state,
        "previous_tail": chunk.get("previous_tail", ""),
        "current_page_text": chunk.get("text", ""),
        "next_head": chunk.get("next_head", "")
    }

    return (
        "Return only the target JSON output.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


# ============================================================
# OLLAMA CALL
# ============================================================

def call_ollama(prompt: str) -> str:
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        format="json",
        think=False,
        options={
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "num_ctx": NUM_CTX
        }
    )

    return response["message"]["content"]


# ============================================================
# JSON PARSING AND REPAIR
# ============================================================

def extract_json_object(text: str) -> Dict[str, Any]:
    """
    First try direct JSON parse.
    If the model adds extra text, extract the largest JSON object.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if not match:
        raise ValueError("No JSON object found in model output.")

    return json.loads(match.group(0))


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in ["true", "yes", "1"]

    return False


def normalize_block(block: Dict[str, Any], page_number: int, index: int) -> Dict[str, Any]:
    clean = default_content_block()

    clean["block_id"] = str(block.get("block_id") or f"p{page_number:04d}_b{index:03d}")

    block_type = str(block.get("type", "uncertain")).strip().lower()

    if block_type not in ["clause", "metadata", "ignore", "uncertain"]:
        block_type = "uncertain"

    clean["type"] = block_type
    clean["clause_number"] = str(block.get("clause_number") or "").strip()
    clean["text"] = str(block.get("text") or "").strip()

    clean["continues_from_previous"] = normalize_bool(
        block.get("continues_from_previous", False)
    )
    clean["continues_to_next"] = normalize_bool(
        block.get("continues_to_next", False)
    )

    clean["stitch_group_id"] = str(block.get("stitch_group_id") or "").strip()
    clean["stitching_note"] = str(block.get("stitching_note") or "").strip()

    if clean["type"] != "clause":
        clean["clause_number"] = ""
        clean["stitch_group_id"] = ""
        clean["continues_from_previous"] = False
        clean["continues_to_next"] = False

    if clean["type"] == "clause" and not clean["stitch_group_id"] and clean["clause_number"]:
        clean["stitch_group_id"] = f"clause_{clean['clause_number']}"

    return clean


def normalize_recent_history(history: Any) -> List[Dict[str, Any]]:
    if not isinstance(history, list):
        return []

    cleaned = []

    for item in history[-5:]:
        if not isinstance(item, dict):
            continue

        cleaned.append({
            "page_number": item.get("page_number", 0),
            "clause_number": str(item.get("clause_number") or ""),
            "stitch_group_id": str(item.get("stitch_group_id") or ""),
            "summary": str(item.get("summary") or "")
        })

    return cleaned


def normalize_state(state: Any) -> Dict[str, Any]:
    clean = empty_carry_state()

    if not isinstance(state, dict):
        return clean

    status = str(state.get("status", "uncertain")).strip().lower()

    if status not in ["open", "closed", "uncertain"]:
        status = "uncertain"

    clean["status"] = status
    clean["active_clause_number"] = str(state.get("active_clause_number") or "")
    clean["active_stitch_group_id"] = str(state.get("active_stitch_group_id") or "")
    clean["active_block_id"] = str(state.get("active_block_id") or "")
    clean["active_clause_summary"] = str(state.get("active_clause_summary") or "")
    clean["continuation_hint_for_next_chunk"] = str(
        state.get("continuation_hint_for_next_chunk") or ""
    )
    clean["last_visible_text"] = str(state.get("last_visible_text") or "")[-700:]
    clean["recent_clause_history"] = normalize_recent_history(
        state.get("recent_clause_history", [])
    )

    return clean


def validate_and_normalize_output(
    raw: Dict[str, Any],
    chunk: Dict[str, Any]
) -> Dict[str, Any]:
    page_number = int(chunk.get("page_number", 0))
    chunk_id = str(chunk.get("chunk_id", ""))
    document_name = str(chunk.get("document_name", ""))

    blocks = raw.get("content_blocks", [])

    if not isinstance(blocks, list):
        blocks = []

    normalized_blocks = []

    for idx, block in enumerate(blocks, start=1):
        if isinstance(block, dict):
            normalized_blocks.append(normalize_block(block, page_number, idx))

    if not normalized_blocks:
        normalized_blocks.append({
            **default_content_block(),
            "block_id": f"p{page_number:04d}_b001",
            "type": "uncertain",
            "text": str(chunk.get("text", "")).strip(),
            "stitching_note": "Fallback uncertain block because model returned no valid content blocks."
        })

    state = normalize_state(raw.get("output_carry_forward_state", {}))

    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "document_id": document_name,
        "content_blocks": normalized_blocks,
        "output_carry_forward_state": state
    }


# ============================================================
# FALLBACK OUTPUT
# ============================================================

def fallback_output(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any],
    error: str
) -> Dict[str, Any]:
    page_number = int(chunk.get("page_number", 0))
    chunk_id = str(chunk.get("chunk_id", ""))
    document_name = str(chunk.get("document_name", ""))

    text = str(chunk.get("text", "")).strip()

    # If previous state is open, assume uncertain continuation rather than losing the page.
    if previous_state.get("status") == "open" and previous_state.get("active_clause_number"):
        block_type = "clause"
        clause_number = previous_state.get("active_clause_number", "")
        stitch_group_id = previous_state.get(
            "active_stitch_group_id",
            f"clause_{clause_number}"
        )
        continues_from_previous = True
    else:
        block_type = "uncertain"
        clause_number = ""
        stitch_group_id = ""
        continues_from_previous = False

    block = {
        "block_id": f"p{page_number:04d}_b001",
        "type": block_type,
        "clause_number": clause_number,
        "text": text,
        "continues_from_previous": continues_from_previous,
        "continues_to_next": False,
        "stitch_group_id": stitch_group_id,
        "stitching_note": f"Fallback generated because Phase 2 model failed: {error}"
    }

    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "document_id": document_name,
        "content_blocks": [block],
        "output_carry_forward_state": previous_state
    }


# ============================================================
# FILE IO
# ============================================================

def load_chunks(chunks_path: Path) -> List[Dict[str, Any]]:
    chunks_path = Path(chunks_path)

    if not chunks_path.exists():
        raise FileNotFoundError(f"Chunks file not found: {chunks_path}")

    data = json.loads(chunks_path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError(f"Chunks file must contain a list of chunk objects: {chunks_path}")

    return data[CHUNK_START:CHUNK_END]


def save_page_output(output: Dict[str, Any], page_output_dir: Path) -> Path:
    page_output_dir = Path(page_output_dir)
    page_output_dir.mkdir(parents=True, exist_ok=True)

    page_number = int(output.get("page_number", 0))
    chunk_id = str(output.get("chunk_id", f"chunk_{page_number:04d}"))

    out_path = page_output_dir / f"{chunk_id}_page_{page_number:04d}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return out_path


def save_report(report: Dict[str, Any], report_path: Path) -> Path:
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report_path


# ============================================================
# MAIN EXTRACTION LOOP
# ============================================================

def process_chunk(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any]
) -> Dict[str, Any]:
    prompt = build_user_prompt(chunk, previous_state)

    last_error = ""

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw_text = call_ollama(prompt)
            raw_json = extract_json_object(raw_text)
            output = validate_and_normalize_output(raw_json, chunk)
            return output

        except Exception as e:
            last_error = str(e)
            print(f"  Retry {attempt}/{MAX_RETRIES + 1} failed: {last_error}")

            if attempt <= MAX_RETRIES:
                time.sleep(1.5)

    if STOP_ON_FAILURE:
        raise RuntimeError(f"Failed processing chunk {chunk.get('chunk_id')}: {last_error}")

    return fallback_output(chunk, previous_state, last_error)


def run_extraction_for_document(
    document_name: str,
    chunks_path: Path,
    page_output_dir: Path
) -> Dict[str, Any]:
    chunks = load_chunks(chunks_path)

    previous_state = empty_carry_state()
    page_report = []

    print("\n" + "=" * 80)
    print(f"PHASE 2 EXTRACTION STARTED: {document_name}")
    print("=" * 80)
    print(f"Chunks file: {chunks_path}")
    print(f"Page output folder: {page_output_dir}")

    for index, chunk in enumerate(chunks, start=1):
        chunk["document_name"] = document_name

        chunk_id = chunk.get("chunk_id", f"chunk_{index:04d}")
        page_number = chunk.get("page_number", index)

        print(f"[{index}/{len(chunks)}] Phase 2 extracting {chunk_id}, page {page_number}...")

        start = time.time()

        # Ensure output exists for later reporting even if the model fails.
        output = {}

        try:
            output = process_chunk(chunk, previous_state)
            out_path = save_page_output(output, page_output_dir)

            previous_state = output["output_carry_forward_state"]

            status = "success"
            error = None

        except Exception as e:
            out_path = None
            status = "failed"
            error = str(e)

            if STOP_ON_FAILURE:
                raise

        elapsed = round(time.time() - start, 2)

        block_count = len(output.get("content_blocks", [])) if status == "success" else 0

        carry_state_status = (
            output.get("output_carry_forward_state", {}).get("status", "")
            if status == "success"
            else ""
        )

        page_report.append({
            "chunk_id": chunk_id,
            "page_number": page_number,
            "status": status,
            "elapsed_seconds": elapsed,
            "output_path": str(out_path) if out_path else None,
            "block_count": block_count,
            "carry_state_status": carry_state_status,
            "active_clause_number": previous_state.get("active_clause_number", ""),
            "error": error
        })

        print(
            f"  Status: {status} | Blocks: {block_count} | "
            f"State: {carry_state_status} | Time: {elapsed}s"
        )

    successful_chunks = sum(1 for item in page_report if item["status"] == "success")
    failed_chunks = sum(1 for item in page_report if item["status"] == "failed")

    failed_pages = [
        item["page_number"]
        for item in page_report
        if item["status"] == "failed"
    ]

    document_report = {
        "document_name": document_name,
        "chunks_path": str(chunks_path),
        "page_output_dir": str(page_output_dir),
        "total_chunks": len(chunks),
        "successful_chunks": successful_chunks,
        "failed_chunks": failed_chunks,
        "failed_pages": failed_pages,
        "final_carry_forward_state": previous_state,
        "pages": page_report
    }

    print("\nDocument complete:")
    print(f"Document: {document_name}")
    print(f"Chunks processed: {len(chunks)}")
    print(f"Successful: {successful_chunks}")
    print(f"Failed: {failed_chunks}")
    print(f"Failed pages: {failed_pages if failed_pages else 'None'}")

    return document_report


def run_extraction():
    jobs = get_document_jobs(INPUT_CHUNKS_PATH)
    group_name = get_group_name(INPUT_CHUNKS_PATH)

    all_reports = []

    print(f"Total documents found: {len(jobs)}")

    for job in jobs:
        document_name = str(job["document_name"])
        chunks_path = Path(job["chunks_path"])

        page_output_dir = get_page_output_dir(document_name, group_name)

        document_report = run_extraction_for_document(
            document_name=document_name,
            chunks_path=chunks_path,
            page_output_dir=page_output_dir
        )

        all_reports.append(document_report)

    total_chunks = sum(item["total_chunks"] for item in all_reports)
    successful_chunks = sum(item["successful_chunks"] for item in all_reports)
    failed_chunks = sum(item["failed_chunks"] for item in all_reports)

    failed_pages_by_document = {
        item["document_name"]: item["failed_pages"]
        for item in all_reports
        if item["failed_pages"]
    }

    final_status = "success"

    if failed_chunks > 0:
        final_status = "failed"

    final_report = {
        "input_path": str(INPUT_CHUNKS_PATH),
        "group_name": group_name,
        "run_status": final_status,
        "total_documents": len(all_reports),
        "total_chunks": total_chunks,
        "successful_chunks": successful_chunks,
        "failed_chunks": failed_chunks,
        "failed_pages_by_document": failed_pages_by_document,
        "documents": all_reports
    }

    if group_name:
        report_path = get_report_path(group_name=group_name)
    else:
        report_path = get_report_path(
            group_name=None,
            document_name=all_reports[0]["document_name"] if all_reports else None
        )

    save_report(final_report, report_path)

    print("\n" + "=" * 80)
    print("PHASE 2 EXTRACTION COMPLETE")
    print("=" * 80)
    print(f"Input: {INPUT_CHUNKS_PATH}")
    print(f"Run status: {final_status}")
    print(f"Total documents: {len(all_reports)}")
    print(f"Total chunks: {total_chunks}")
    print(f"Successful chunks: {successful_chunks}")
    print(f"Failed chunks: {failed_chunks}")

    if failed_pages_by_document:
        print("\nFailed pages by document:")
        for document_name, pages in failed_pages_by_document.items():
            print(f"- {document_name}: {pages}")
    else:
        print("\nFailed pages by document: None")

    print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    run_extraction()