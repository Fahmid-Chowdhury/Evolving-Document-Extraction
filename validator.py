from typing import Any, Dict, Optional
import json
import re
from urllib import response

from llm_client import LLMRequestConfig, chat_completion


# ============================================================
# VALIDATOR LOCAL LLM BACKEND CONFIG
# ============================================================

# Use "hf" for HuggingFace Transformers local models.
# Use "ollama" to keep using your local Ollama server.
VALIDATOR_BACKEND = "hf"

# HuggingFace local model
# VALIDATOR_HF_MODEL = "google/gemma-4-E4B-it"
VALIDATOR_HF_MODEL = "google/gemma-4-E2B-it"

# Validator output is small, so keep this smaller than extractor output.
VALIDATOR_MAX_NEW_TOKENS = 1024

# Ollama local model; only used when VALIDATOR_BACKEND = "ollama"
VALIDATOR_OLLAMA_MODEL = "gemma4:latest"

THINK = False   # HF: enable_thinking in chat template; Ollama: think mode.

VALIDATOR_TEMPERATURE = 0.30
VALIDATOR_TOP_P = 0.20
VALIDATOR_NUM_CTX = 32768

VALIDATOR_TEXT_LIMIT = 2500  # Limit for previous_tail, next_head, and previous_open_point_block text in the validator prompt to keep it concise and focused on the most relevant context.


# ============================================================
# VALIDATOR PROMPT
# ============================================================

VALIDATOR_SYSTEM_PROMPT = """
You are a narrow validator for a legal OCR extraction pipeline.

Your job is NOT to re-extract the document.
Your job is NOT to improve the extractor output.
Your job is NOT to judge every possible minor issue.

Your job is only to detect 4 major extractor errors:

1. wrong_block_split
2. copied_context_text
3. wrong_block_type
4. carry_state_inconsistency

If none of these 4 major errors are clearly present, you must mark the output as valid.

Return ONLY valid JSON.
Do not use markdown.
Do not explain outside JSON.
Do not add fields outside the output schema.

==================================================
INPUTS YOU WILL RECEIVE
==================================================

You will receive:
1. ORIGINAL_CHUNK_INPUT
2. CURRENT_PAGE_TEXT
3. PREVIOUS_TAIL
4. NEXT_HEAD
5. PREVIOUS_STATE
6. Optional PREVIOUS_OPEN_POINT_BLOCK
7. EXTRACTOR_OUTPUT

Important:
- CURRENT_PAGE_TEXT is the only valid source for extracted block text.
- PREVIOUS_TAIL, NEXT_HEAD, PREVIOUS_STATE, and PREVIOUS_OPEN_POINT_BLOCK are only context.
- The extractor must never copy text from PREVIOUS_TAIL, NEXT_HEAD, or PREVIOUS_OPEN_POINT_BLOCK into content_blocks.text.
- You must also never instruct the extractor to copy from PREVIOUS_TAIL, NEXT_HEAD, or PREVIOUS_OPEN_POINT_BLOCK.

==================================================
VALIDATION PHILOSOPHY
==================================================

Be conservative.

Only mark invalid when the error is clear and harmful.

Do not mark invalid for:
- minor wording differences
- imperfect summaries
- harmless uncertainty
- slightly short stitching notes
- small formatting differences
- metadata that is reasonable
- missing text unless it is caused by one of the 4 major issues

If the output is mostly correct and does not clearly contain one of the 4 major errors, mark it valid.

When unsure, mark valid.

You are not allowed to create new extraction rules.
You are not allowed to force your preferred structure.
You are not allowed to tell the extractor to use text from PREVIOUS_TAIL or NEXT_HEAD.

==================================================
THE 4 MAJOR ERRORS TO DETECT
==================================================

1. wrong_block_split

Detect this only when:
- A single continuous main point visible in CURRENT_PAGE_TEXT is split into multiple point blocks unnecessarily, OR
- a table, quote, definition, explanation, substituted text, inserted text, schedule, or subpoint that clearly belongs to the same point is separated into another block.

Do NOT flag wrong_block_split when:
- there are clearly multiple main points,
- metadata appears before or after a point,
- the split is reasonable,
- you are unsure.

Example correction:
"Retry this chunk. Do not split the same main point into multiple blocks. Merge the current-page text that belongs to point ৩ into one point block."

2. copied_context_text

Detect this only when:
- content_blocks.text contains text that appears in PREVIOUS_TAIL, NEXT_HEAD, or PREVIOUS_OPEN_POINT_BLOCK but does not appear in CURRENT_PAGE_TEXT.

This is a strict source-boundary error.

Do NOT flag copied_context_text when:
- the same text naturally appears both in CURRENT_PAGE_TEXT and context,
- only a short common phrase overlaps,
- the overlap is only a numbering marker or common legal phrase.

Very important:
- Your correction must never say to include text from PREVIOUS_TAIL or NEXT_HEAD.
- Your correction must say to extract only from CURRENT_PAGE_TEXT.

Example correction:
"Retry this chunk. Remove copied context text. content_blocks.text must contain only text visible in CURRENT_PAGE_TEXT."

3. wrong_block_type

Detect this only when:
- legal main point text is clearly classified as metadata, ignore, or uncertain, OR
- clear document metadata is classified as point.

Legal point text includes:
- numbered amendments
- rules that belong to a numbered point
- sections that belong to a numbered point
- articles that belong to a numbered point
- substitutions that belong to a numbered point
- insertions that belong to a numbered point
- definitions that belong to a numbered point
- quoted legal provisions that belong to a numbered point
- legal tables that belong to a numbered point
- subpoints under a main point

Metadata includes:
- document title
- chapter title
- date
- notification header
- authority line
- preamble
- schedule title
- signature block

Do NOT flag wrong_block_type when:
- the classification is debatable,
- a heading and legal text are mixed and the extractor made a reasonable choice,
- the issue does not affect stitching.

Example correction:
"Retry this chunk. Legal amendment text was classified as metadata. Treat the numbered legal content as a point block."

4. carry_state_inconsistency

Detect this only when:
- the content block says the point continues, but output_carry_forward_state says status is closed,
- continues_to_next=true but active_point_number or active_stitch_group_id is empty,
- the active point number in carry state does not match the final open point,
- the active stitch_group_id changes randomly,
- numbering_system changes randomly without a clear new numbering sequence,
- expected_next_main_point clearly contradicts the extracted point sequence.

Do NOT flag carry_state_inconsistency when:
- the carry summary is imperfect but usable,
- active_point_summary is short,
- continuation_hint is vague but not wrong,
- the point appears closed and status is closed,
- you are unsure whether the point continues.

Example correction:
"Retry this chunk. The extracted point continues to the next page, so output_carry_forward_state must remain open and preserve the active point_number and stitch_group_id."

==================================================
STRICT RULE ABOUT PREVIOUS_TAIL AND NEXT_HEAD
==================================================

PREVIOUS_TAIL and NEXT_HEAD are context only.

Never tell the extractor to copy, include, merge, or extract text from:
- PREVIOUS_TAIL
- NEXT_HEAD
- PREVIOUS_OPEN_POINT_BLOCK

Bad correction instruction:
"Include the missing text from NEXT_HEAD."

Good correction instruction:
"Use NEXT_HEAD only to decide whether the current point continues. Extract text only from CURRENT_PAGE_TEXT."

If the current page appears incomplete because continuation is in NEXT_HEAD, the correct action is:
- set continues_to_next=true,
- keep carry-forward state open,
- do not copy NEXT_HEAD text.

==================================================
OUTPUT SCHEMA
==================================================

Return exactly this JSON schema:

{
  "is_valid": true,
  "error_types": [],
  "correction_instruction": ""
}

==================================================
OUTPUT RULES
==================================================

If no clear major error exists:
{
  "is_valid": true,
  "error_types": [],
  "correction_instruction": ""
}

If one or more clear major errors exist:
- set is_valid=false
- error_types must be a list
- error_types must contain only the clearly detected major errors
- error_types must not contain duplicate values
- correction_instruction must be short and specific
- correction_instruction must guide the extractor to fix only the detected issues
- correction_instruction must not rewrite the extraction
- correction_instruction must not include long source text
- correction_instruction must not introduce new rules
- correction_instruction must not ask the extractor to copy text from PREVIOUS_TAIL, NEXT_HEAD, or PREVIOUS_OPEN_POINT_BLOCK

==================================================
FINAL DECISION RULE
==================================================

Before marking invalid, ask:
"Is this clearly one of the 4 major errors?"
If yes, mark invalid.
If no, mark valid.
When unsure, mark valid.
"""


# ============================================================
# HELPERS
# ============================================================

def compact_text(text: str, limit: int = VALIDATOR_TEXT_LIMIT) -> str:
    text = str(text or "").strip()

    if len(text) <= limit:
        return text

    half = limit // 2
    return text[:half] + "\n...\n" + text[-half:]


# def normalize_space(text: str) -> str:
#     return re.sub(r"\s+", " ", str(text or "")).strip()


# def has_large_context_copy(block_text: str, context_text: str, min_len: int = 160) -> bool:
#     """
#     Deterministic guard:
#     detects if output copied a substantial exact span from previous_tail/next_head.
#     """
#     block_clean = normalize_space(block_text)
#     context_clean = normalize_space(context_text)

#     if not block_clean or not context_clean:
#         return False

#     if len(context_clean) < min_len:
#         return False

#     # Check chunks from the context.
#     for start in range(0, max(1, len(context_clean) - min_len), min_len):
#         snippet = context_clean[start:start + min_len]
#         if len(snippet) >= min_len and snippet in block_clean:
#             return True

#     return False


# def deterministic_precheck(
#     chunk: Dict[str, Any],
#     extractor_output: Dict[str, Any]
# ) -> Optional[Dict[str, Any]]:
#     """
#     Fast checks before using the validator LLM.
#     If this returns a dict, validation fails immediately.
#     """

#     blocks = extractor_output.get("content_blocks", [])

#     if not isinstance(blocks, list):
#         return None

#     previous_tail = chunk.get("previous_tail", "")
#     next_head = chunk.get("next_head", "")

#     for block in blocks:
#         if not isinstance(block, dict):
#             continue

#         block_text = str(block.get("text") or "")

#         if has_large_context_copy(block_text, previous_tail):
#             return {
#                 "is_valid": False,
#                 "error_types": ["copied_context_text"],
#                 "correction_instruction": "Do not copy PREVIOUS_TAIL into the block text. Extract only the text visible in CURRENT_PAGE_TEXT."
#             }

#         if has_large_context_copy(block_text, next_head):
#             return {
#                 "is_valid": False,
#                 "error_types": ["copied_context_text"],
#                 "correction_instruction": "Do not copy NEXT_HEAD into the block text. Extract only the text visible in CURRENT_PAGE_TEXT."
#             }

#     return None


def normalize_validator_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "is_valid": True,
            "error_types": [],
            "correction_instruction": ""
        }

    is_valid = raw.get("is_valid", True)

    if isinstance(is_valid, str):
        is_valid = is_valid.strip().lower() in ["true", "yes", "1"]

    allowed_errors = {
        "wrong_block_split",
        "copied_context_text",
        "wrong_block_type",
        "carry_state_inconsistency"
    }

    raw_error_types = raw.get("error_types", [])

    if isinstance(raw_error_types, str):
        raw_error_types = [raw_error_types]

    if not isinstance(raw_error_types, list):
        raw_error_types = []

    error_types = []

    for error in raw_error_types:
        error = str(error or "").strip()

        if error in allowed_errors and error not in error_types:
            error_types.append(error)

    correction_instruction = str(raw.get("correction_instruction") or "").strip()

    if is_valid:
        return {
            "is_valid": True,
            "error_types": [],
            "correction_instruction": ""
        }

    if not error_types:
        error_types = ["carry_state_inconsistency"]

    if not correction_instruction:
        correction_instruction = "Retry this chunk and fix only the clearly detected validation issues."

    correction_instruction = correction_instruction[:700]

    return {
        "is_valid": False,
        "error_types": error_types,
        "correction_instruction": correction_instruction
    }


def parse_json_object(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if not match:
        raise ValueError("No JSON object found in validator output.")

    return json.loads(match.group(0))


# ============================================================
# MAIN VALIDATOR AGENT FUNCTION
# ============================================================

def validate_extraction_with_agent(
    chunk: Dict[str, Any],
    previous_state: Dict[str, Any],
    extractor_output: Dict[str, Any],
    previous_open_point_block: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Returns:
    {
    "is_valid": bool,
    "error_types": list[str],
    "correction_instruction": "..."
    }
    """

    # precheck = deterministic_precheck(chunk, extractor_output)

    # if precheck is not None:
    #     return precheck

    payload = {
        "chunk_id": chunk.get("chunk_id", ""),
        "page_number": chunk.get("page_number", 0),
        "previous_state": previous_state,
        "previous_tail": compact_text(chunk.get("previous_tail", "")),
        "current_page_text": chunk.get("text", ""),
        "next_head": compact_text(chunk.get("next_head", "")),
        "previous_open_point_block": previous_open_point_block,
        "extractor_output": extractor_output
    }

    try:
        validator_prompt = (
            "Validate this extractor output. "
            "Return only the validator JSON.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

        model_name = (
            VALIDATOR_HF_MODEL
            if VALIDATOR_BACKEND in ["hf", "huggingface"]
            else VALIDATOR_OLLAMA_MODEL
        )

        raw_text = chat_completion(
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            user_prompt=validator_prompt,
            config=LLMRequestConfig(
                backend=VALIDATOR_BACKEND,
                model=model_name,
                temperature=VALIDATOR_TEMPERATURE,
                top_p=VALIDATOR_TOP_P,
                num_ctx=VALIDATOR_NUM_CTX,
                max_new_tokens=VALIDATOR_MAX_NEW_TOKENS,
                think=THINK,
                response_format="json",
            ),
        )

        raw_json = parse_json_object(raw_text)
        return normalize_validator_response(raw_json)

    except Exception as e:
        # If validator fails, do not block the pipeline.
        return {
            "is_valid": True,
            "error_types": [],
            "correction_instruction": ""
        }