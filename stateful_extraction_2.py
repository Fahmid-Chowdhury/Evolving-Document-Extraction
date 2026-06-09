from urllib import response

from validator import validate_extraction_with_agent

from pathlib import Path
import json
import time
import re
from typing import Any, Dict, List, Optional

import ollama


# ============================================================
# CONFIG
# ============================================================

INPUT_CHUNKS_PATH = Path(r"phase1_output\chunks\without_headers_footers\20160408_Finance_Act_2013\chunks.json")

OUTPUT_ROOT = Path("phase2_output")
PAGE_OUTPUT_ROOT = OUTPUT_ROOT / "page_outputs_2"
REPORT_ROOT = OUTPUT_ROOT / "reports"

# Change this to your local Ollama model name
OLLAMA_MODEL = "gemma4:latest"
# OLLAMA_MODEL = "gemma4:26b"
# OLLAMA_MODEL = "qwen3.5:9b"
# OLLAMA_MODEL = "llama3.1:8b"
# OLLAMA_MODEL = "gpt-oss:20b"

THINK = True   # Set to True to enable Ollama's think mode for better reasoning (may increase latency)

TEMPERATURE = 0.2
TOP_P = 0.1
NUM_CTX = 32768

MAX_RETRIES = 2
STOP_ON_FAILURE = False

USE_VALIDATOR_AGENT = True
MAX_VALIDATOR_REPAIR_ROUNDS = 3

SEND_PREVIOUS_OPEN_POINT_BLOCK = True
# When enabled, only send the last point block from the previous chunk if continues_to_next=true. Currently, it sends the previous open point block even when this flag is false. (see line 239-240) 
# Keep this small enough for local 9B models.
# Set to None if you want to send the full previous point text.
PREVIOUS_OPEN_POINT_TEXT_LIMIT = None  # in characters

# =========================
# GLOBAL CHUNK RANGE CONFIG
# =========================

CHUNK_START = 48      # inclusive; set to 0 to start from the beginning
CHUNK_END = None     # exclusive; None means return until the end


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


def get_previous_open_point_block(output: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extracts the last point block from the current output only if it continues to next chunk.

    This compact block is passed to the next LLM call as input context.
    It is not generated by the LLM as a separate field.
    """

    if not SEND_PREVIOUS_OPEN_POINT_BLOCK:
        return None

    blocks = output.get("content_blocks", [])

    if not isinstance(blocks, list):
        return None

    for block in reversed(blocks):
        if not isinstance(block, dict):
            continue

        if block.get("type") != "point":
            continue

        if not normalize_bool(block.get("continues_to_next", False)):
            continue

        text = str(block.get("text") or "").strip()

        if PREVIOUS_OPEN_POINT_TEXT_LIMIT is not None:
            text = text[-PREVIOUS_OPEN_POINT_TEXT_LIMIT:]

        return {
            "block_id": str(block.get("block_id") or ""),
            "point_number": str(block.get("point_number") or ""),
            "text": text,
            "stitching_note": str(block.get("stitching_note") or "")
        }

    return None


# ============================================================
# PHASE 2 TARGET SCHEMA
# ============================================================

def empty_carry_state() -> Dict[str, Any]:
    return {
        "status": "closed",
        "active_point_number": "",
        "active_stitch_group_id": "",
        "active_point_summary": "",
        "numbering_system": "",
        "expected_next_main_point": "",
        "continuation_hint": "",
        "last_visible_text": ""
    }


def default_content_block() -> Dict[str, Any]:
    return {
        "block_id": "",
        "type": "uncertain",
        "point_number": "",
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
You are a legal document structure extraction assistant.

Your task is to extract simple stitchable content blocks from OCR text.

You must return ONLY valid JSON.
Do not use markdown.
Do not explain anything.
Do not add fields outside the given output schema.

==================================================
Core concept
==================================================

- Extract MAIN POINTS, not clauses.
- A main point is the largest numbered unit in the current document flow.
- Subpoints, quoted text, tables, explanations, schedules, definitions, and substituted text must stay inside the current main point.

==================================================
Allowed block types
==================================================

1. point
2. metadata
3. ignore
4. uncertain

Use "point" for main legal numbered content.
Use "metadata" for chapter number and titles, document titles, notification headers, dates, preambles, authority lines, and signature lines.
Use "ignore" only for clear page numbers, repeated footers, or OCR garbage.
Use "uncertain" when you are not sure.

==================================================
Input context
==================================================

- PREVIOUS_STATE summarizes the legal context carried from earlier chunks.
- PREVIOUS_TAIL is only the ending text of the previous chunk.
- NEXT_HEAD is only the beginning text of the next chunk.
- PREVIOUS_OPEN_POINT_BLOCK is the last point block from the previous chunk that had continues_to_next=true.
- PREVIOUS_OPEN_POINT_BLOCK is provided only to help decide continuation, extraction pattern and structure.
- Never copy PREVIOUS_OPEN_POINT_BLOCK text into the current output block text.

==================================================
Very important extraction rules
==================================================

1. Extract text only from CURRENT_PAGE_TEXT.
2. Use PREVIOUS_TAIL, NEXT_HEAD, PREVIOUS_STATE, and PREVIOUS_OPEN_POINT_BLOCK only for understanding continuation.
3. Never copy PREVIOUS_TAIL, NEXT_HEAD, or PREVIOUS_OPEN_POINT_BLOCK into the output block text.
4. Preserve the reading order of the current page.
5. Do not create separate blocks for tables or quoted text if they belong to a point.
6. If a table belongs inside point 3, include the table text inside point 3.
7. If quoted text belongs inside point 3, include the quoted text inside point 3.
8. If the current page starts in the middle of an open point, create a point block with continues_from_previous=true.
9. If the point seems to continue into the next page, set continues_to_next=true.
10. If the page contains a new chapter, document title, date, authority line, schedule title or signature block, output it as metadata.
11. If PREVIOUS_OPEN_POINT_BLOCK is provided and CURRENT_PAGE_TEXT continues that same point, use the same point_number and stitch_group_id.

==================================================
Numbering rules
==================================================

1. Track the main numbering system in output_carry_forward_state.numbering_system.
2. Main points usually use Bangla digits like ১। ২। ৩। or English digits like 1. 2. 3.
3. Subpoints usually use markers like (ক), (খ), (গ), (a), (b), (c), (i), (ii), (iii), (অ), (আ).
4. If the current main numbering system is Bangla digits, then (ক), (খ), (গ), (ঘ), (a), (b), (i), (ii) are not new main points.
5. If the previous state says active point ۵ is open and the current page begins with (ঘ), attach it to point ۵.
6. Start a new main point only when the marker matches the main numbering system or a new chapter/major section clearly resets the numbering.
7. If the expected next main point is ۶, then (ঘ) cannot be main point ۶. It is probably a subpoint of the active point.
8. Update numbering_system only when a new chapter, new major section, or clearly new main numbering sequence starts.
9. If unsure whether text is a new main point or a continuation, prefer continuation when PREVIOUS_STATE.status is open.

==================================================
Output schema
==================================================

{
  "chunk_id": "",
  "page_number": 0,
  "document_id": "",
  "content_blocks": [
    {
      "block_id": "",
      "type": "point | metadata | ignore | uncertain",
      "point_number": "",
      "text": "",
      "continues_from_previous": false,
      "continues_to_next": false,
      "stitch_group_id": "",
      "stitching_note": ""
    }
  ],
  "output_carry_forward_state": {
    "status": "open | closed | uncertain",
    "active_point_number": "",
    "active_stitch_group_id": "",
    "active_point_summary": "",
    "numbering_system": " "" | bangla_digits | english_digits | bangla_letters | english_letters | roman | mixed | unknown ",
    "expected_next_main_point": "",
    "continuation_hint": "",
    "last_visible_text": ""
  }
}

==================================================
Allowed point numbering_system values
==================================================

- ""
- bangla_digits
- english_digits
- bangla_letters
- english_letters
- roman
- mixed
- unknown

==================================================
ID generation rules
==================================================

- block_id format:
  "p{page_number_4digit}_b{block_serial_3digit}"
  Example:
  "p0003_b001"
  "p0003_b002"
- block_serial must follow reading order within the current page/chunk.
- stitch_group_id format:
  "point_{point_number}"
  Example:
  "point_৬"
  "point_5"
- Every point block must have its own unique stitch_group_id.
- If a point continues across pages, always reuse the same stitch_group_id and format.
- Never use random values for block_id or stitch_group_id.

==================================================
Field rules
==================================================

- point_number must be the main point number only, such as "৬", "5".
- stitch_group_id must stay stable across pages for the same point, such as "point_৬" or "point_5".
- For metadata and ignore blocks, use empty string for point_number and stitch_group_id.
- For uncertain blocks, use empty string for point_number and stitch_group_id unless the text clearly belongs to the active point.
- output_carry_forward_state must summarize what should be remembered for the next chunk in detail.
- last_visible_text should contain only the final visible part of the current active point from CURRENT_PAGE_TEXT.
"""

def build_user_prompt(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any],
    previous_open_point_block: Optional[Dict[str, str]] = None,
    validator_instruction: str = ""
) -> str:
    payload = {
        "chunk_id": chunk.get("chunk_id", ""),
        "page_number": chunk.get("page_number", 0),
        "document_id": chunk.get("document_name", ""),
        "previous_state": previous_state,
        "previous_tail": chunk.get("previous_tail", ""),
        "current_page_text": chunk.get("text", ""),
        "next_head": chunk.get("next_head", "")
    }

    if SEND_PREVIOUS_OPEN_POINT_BLOCK and previous_open_point_block:
        payload["previous_open_point_block"] = previous_open_point_block

    if validator_instruction:
        payload["validator_instruction_for_this_retry"] = validator_instruction

    return (
        "Extract Phase 2 point blocks from this input JSON. "
        "Return only valid JSON using the required output schema.\n\n"
        "If validator_instruction_for_this_retry is present, treat it as a correction guide for this same chunk. "
        "Follow it carefully, fix the specific mistake, and return the full corrected JSON output. "
        "Do not add any extra output fields.\n\n"
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
        think=THINK,
        options={
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "num_ctx": NUM_CTX
        }
    )
    
    # print("Extract response received.")
    # print("Input tokens:", response["prompt_eval_count"])
    # print("Output tokens:", response["eval_count"])
    # print("Total tokens:", response["prompt_eval_count"] + response["eval_count"])
    
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

    if block_type not in ["point", "metadata", "ignore", "uncertain"]:
        block_type = "uncertain"

    clean["type"] = block_type
    clean["point_number"] = str(block.get("point_number") or "").strip()
    clean["text"] = str(block.get("text") or "").strip()

    clean["continues_from_previous"] = normalize_bool(
        block.get("continues_from_previous", False)
    )
    clean["continues_to_next"] = normalize_bool(
        block.get("continues_to_next", False)
    )

    clean["stitch_group_id"] = str(block.get("stitch_group_id") or "").strip()
    clean["stitching_note"] = str(block.get("stitching_note") or "").strip()

    if clean["type"] != "point":
        clean["point_number"] = ""
        clean["stitch_group_id"] = ""
        clean["continues_from_previous"] = False
        clean["continues_to_next"] = False

    if clean["type"] == "point" and not clean["stitch_group_id"] and clean["point_number"]:
        clean["stitch_group_id"] = f"point_{clean['point_number']}"

    return clean


def normalize_state(state: Any) -> Dict[str, Any]:
    clean = empty_carry_state()

    if not isinstance(state, dict):
        return clean

    status = str(state.get("status", "uncertain")).strip().lower()

    if status not in ["open", "closed", "uncertain"]:
        status = "uncertain"

    numbering_system = str(state.get("numbering_system") or "").strip().lower()

    allowed_numbering_systems = [
        "",
        "bangla_digits",
        "english_digits",
        "bangla_letters",
        "english_letters",
        "roman",
        "mixed",
        "unknown"
    ]

    if numbering_system not in allowed_numbering_systems:
        numbering_system = "unknown"

    clean["status"] = status
    clean["active_point_number"] = str(state.get("active_point_number") or "").strip()
    clean["active_stitch_group_id"] = str(state.get("active_stitch_group_id") or "").strip()
    clean["active_point_summary"] = str(state.get("active_point_summary") or "").strip()
    clean["numbering_system"] = numbering_system
    clean["expected_next_main_point"] = str(state.get("expected_next_main_point") or "").strip()
    clean["continuation_hint"] = str(state.get("continuation_hint") or "").strip()
    clean["last_visible_text"] = str(state.get("last_visible_text") or "").strip()[-700:]

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

    if previous_state.get("status") == "open" and previous_state.get("active_point_number"):
        block_type = "point"
        point_number = previous_state.get("active_point_number", "")
        stitch_group_id = previous_state.get(
            "active_stitch_group_id",
            f"point_{point_number}"
        )
        continues_from_previous = True
    else:
        block_type = "uncertain"
        point_number = ""
        stitch_group_id = ""
        continues_from_previous = False

    block = {
        "block_id": f"p{page_number:04d}_b001",
        "type": block_type,
        "point_number": point_number,
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

def run_extractor_once(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any],
    previous_open_point_block: Optional[Dict[str, str]] = None,
    validator_instruction: str = ""
) -> Dict[str, Any]:
    prompt = build_user_prompt(
        chunk=chunk,
        previous_state=previous_state,
        previous_open_point_block=previous_open_point_block,
        validator_instruction=validator_instruction
    )

    last_error = ""

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw_text = call_ollama(prompt)
            raw_json = extract_json_object(raw_text)
            output = validate_and_normalize_output(raw_json, chunk)
            return output

        except Exception as e:
            last_error = str(e)
            print(f"  Extractor retry {attempt}/{MAX_RETRIES + 1} failed: {last_error}")

            if attempt <= MAX_RETRIES:
                time.sleep(1.5)

    if STOP_ON_FAILURE:
        raise RuntimeError(f"Failed processing chunk {chunk.get('chunk_id')}: {last_error}")

    return fallback_output(chunk, previous_state, last_error)

def process_chunk(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any],
    previous_open_point_block: Optional[Dict[str, str]] = None
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:

    validation_log = []

    output = run_extractor_once(
        chunk=chunk,
        previous_state=previous_state,
        previous_open_point_block=previous_open_point_block,
        validator_instruction=""
    )

    if not USE_VALIDATOR_AGENT:
        return output, validation_log

    validator_result = validate_extraction_with_agent(
        chunk=chunk,
        previous_state=previous_state,
        previous_open_point_block=previous_open_point_block,
        extractor_output=output
    )

    validation_log.append({
        "round": 0,
        "is_valid": validator_result["is_valid"],
        "error_types": validator_result["error_types"] if not validator_result["is_valid"] else [],
        "correction_instruction": validator_result["correction_instruction"]
    })

    if validator_result["is_valid"]:
        return output, validation_log

    validator_instruction = validator_result["correction_instruction"]

    for repair_round in range(1, MAX_VALIDATOR_REPAIR_ROUNDS + 1):
        print(f"  Validator rejected output. Repair round {repair_round}: {validator_instruction}")

        repaired_output = run_extractor_once(
            chunk=chunk,
            previous_state=previous_state,
            previous_open_point_block=previous_open_point_block,
            validator_instruction=validator_instruction
        )

        repaired_validation = validate_extraction_with_agent(
            chunk=chunk,
            previous_state=previous_state,
            previous_open_point_block=previous_open_point_block,
            extractor_output=repaired_output
        )

        validation_log.append({
            "round": repair_round,
            "is_valid": repaired_validation["is_valid"],
            "error_types": repaired_validation["error_types"] if not repaired_validation["is_valid"] else [],
            "correction_instruction": repaired_validation["correction_instruction"]
        })

        output = repaired_output

        if repaired_validation["is_valid"]:
            break

        validator_instruction = repaired_validation["correction_instruction"]

    return output, validation_log


def run_extraction_for_document(
    document_name: str,
    chunks_path: Path,
    page_output_dir: Path
) -> Dict[str, Any]:
    chunks = load_chunks(chunks_path)

    previous_state = empty_carry_state()
    previous_open_point_block = None
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
        validation_log = []

        try:
            output, validation_log = process_chunk(
                chunk=chunk,
                previous_state=previous_state,
                previous_open_point_block=previous_open_point_block
            )

            out_path = save_page_output(output, page_output_dir)

            previous_state = output["output_carry_forward_state"]
            previous_open_point_block = get_previous_open_point_block(output)

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
            "active_point_number": previous_state.get("active_point_number", ""),
            "numbering_system": previous_state.get("numbering_system", ""),
            "expected_next_main_point": previous_state.get("expected_next_main_point", ""),
            "sent_previous_open_point_block": previous_open_point_block is not None,
            "previous_open_point_block_id": previous_open_point_block.get("block_id", "") if previous_open_point_block else "",
            "validator_used": USE_VALIDATOR_AGENT,
            "validator_final_valid": validation_log[-1]["is_valid"] if validation_log else None,
            "validator_final_error_types": validation_log[-1]["error_types"] if validation_log else [],
            "validation_log": validation_log,
            "error": error
        })

        validator_status = ""

        if validation_log:
            validator_status = (
                f" | Validator: {validation_log[-1]['is_valid']} "
                f"({validation_log[-1]['error_types']})"
            )

        print(
            f"  Status: {status} | Blocks: {block_count} | "
            f"State: {carry_state_status} | "
            f"Active point: {previous_state.get('active_point_number', '')} | "
            f"Time: {elapsed}s"
            f"{validator_status}"
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