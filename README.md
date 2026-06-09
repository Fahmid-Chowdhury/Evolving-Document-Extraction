
# Technical Report: OCR-Based Legal Document Extraction Pipeline

## 1. Project Overview

This project is an OCR-based legal document parsing pipeline. Its main goal is to take PDF legal documents, extract their text page by page, divide the OCR text into chunk-level inputs, use an LLM to classify and extract legal structure, validate the LLM output, and finally stitch multi-page legal points into a final structured JSON document.

The current project folder is:

```text
try2/
├── Documents/
├── phase1_output/
├── phase2_output/
├── phase3_output/
├── pdf_to_image.py
├── ocr.py
├── page_by_page_ocr.py
├── chunking.py
├── stateful_extraction_2.py
├── validator.py
├── stitch_points.py
└── __pycache__/
```

The pipeline is divided into three main phases:

```text
Phase 1: PDF / OCR processing
    ├── pdf_to_image.py
    ├── ocr.py
    └── page_by_page_ocr.py

Phase 1B: Chunk preparation
    └── chunking.py

Phase 2: LLM-based structure extraction
    ├── stateful_extraction_2.py
    └── validator.py

Phase 3: Final stitching
    └── stitch_points.py
```

The usual execution flow is:

```bash
python page_by_page_ocr.py
python chunking.py
python stateful_extraction_2.py
python stitch_points.py
```

`pdf_to_image.py` and `ocr.py` are useful supporting scripts, but the main working pipeline appears to use `page_by_page_ocr.py` → `chunking.py` → `stateful_extraction_2.py` → `stitch_points.py`.

---

## 2. Important Project Folders

### 2.1 `Documents/`

This folder contains the original PDF files. The OCR scripts read PDFs from this folder or from subfolders inside it.

Example current input path in `page_by_page_ocr.py`:

```python
INPUT_PATH = Path(r"Documents\20160408_Finance_Act_2013.pdf")
```

This can be changed to either:

```python
INPUT_PATH = Path(r"Documents\some_file.pdf")
```

or:

```python
INPUT_PATH = Path(r"Documents\Some Folder")
```

When a folder is given, the script searches for all PDF files inside it.

---

### 2.2 `phase1_output/`

This folder stores outputs from the OCR and chunking stage.

Important subfolders:

```text
phase1_output/
├── page_by_page_ocr/
├── page_by_page_ocr/without_headers_footers/
├── full_document_ocr/
├── chunks/
├── chunks/without_headers_footers/
└── reports/
```

The most important output for the current pipeline is:

```text
phase1_output/page_by_page_ocr/without_headers_footers/<document_name>/page_XXXX.json
```

Then `chunking.py` converts those page JSON files into:

```text
phase1_output/chunks/without_headers_footers/<document_name>/chunks.json
```

---

### 2.3 `phase2_output/`

This folder stores the LLM extraction results.

Important subfolders:

```text
phase2_output/
├── page_outputs_2/
└── reports/
```

For each document, `stateful_extraction_2.py` outputs one JSON file per page/chunk:

```text
phase2_output/page_outputs_2/<document_name>/<chunk_id>_page_XXXX.json
```

It also creates a run report:

```text
phase2_output/reports/<document_name>_report.json
```

---

### 2.4 `phase3_output/`

This folder stores the final stitched document.

Important subfolders:

```text
phase3_output/
├── final_documents/
└── reports/
```

Final output:

```text
phase3_output/final_documents/<document_name>/final_stitched_document.json
```

Phase 3 report:

```text
phase3_output/reports/<document_name>/phase3_stitch_report.json
```

Master report:

```text
phase3_output/reports/phase3_master_report.json
```

---

## 3. File-by-File Explanation

# 3.1 `pdf_to_image.py`

## Purpose

`pdf_to_image.py` converts a PDF into page images and performs basic image-level sanity checks.

This script is useful when we want to inspect the visual quality of a PDF before OCR. It can detect blank pages, low text density, possible rotated pages, landscape pages, and low-resolution pages.

It is not the main OCR pipeline, but it is useful for debugging OCR problems.

---

## Main Input

Configured at the top:

```python
PDF_PATH = r"001-Law-1994 Germany DTAA.pdf"
```

This should be changed to the PDF file that needs to be converted.

---

## Main Output

Images are saved inside:

```text
phase1_output/page_images/<PDF_NAME>/
```

For example:

```text
phase1_output/page_images/001-Law-1994 Germany DTAA/
├── 001-Law-1994 Germany DTAA_page_0001.png
├── 001-Law-1994 Germany DTAA_page_0002.png
└── ...
```

Reports are saved inside:

```text
phase1_output/reports/
```

Specifically:

```text
phase1_output/reports/phase1_image_sanity_report.json
phase1_output/reports/phase1_image_sanity_report.csv
```

---

## What It Does Internally

The file performs four main tasks:

### Step 1: Convert PDF pages into images

Function:

```python
save_pdf_pages_as_images()
```

It uses `pdf2image.convert_from_path()` to convert each PDF page into a PNG image.

### Step 2: Calculate blank page score

Function:

```python
calculate_blank_score()
```

It reads the image in grayscale and calculates how many pixels are almost white.

A high blank score means the page may be mostly empty.

### Step 3: Calculate dark pixel ratio

Function:

```python
calculate_dark_pixel_ratio()
```

It estimates how much dark text or ink exists on a page.

A very low dark pixel ratio means the page may contain very little readable content.

### Step 4: Detect rotation or landscape risk

Function:

```python
detect_rotation_risk()
```

If the page width is greater than the height, the script flags it as possible landscape or rotated.

---

## What Can Be Safely Changed

A teammate can safely change:

```python
PDF_PATH
DPI
IMAGE_FORMAT
POPPLER_PATH
```

Usually, `DPI = 300` is a good OCR-quality setting. Higher DPI may improve OCR but increases processing time and file size.

---

## When to Use This File

Use this file when:

* OCR output is poor.
* Pages appear blank after OCR.
* Some pages may be rotated.
* We need visual page images for manual inspection.
* We want a quick quality report before OCR.

---

# 3.2 `ocr.py`

## Purpose

`ocr.py` runs Chandra OCR on full PDF documents. It processes either a single PDF file or all PDF files inside a folder.

This file generates full-document OCR output, such as Markdown, HTML, metadata JSON, and extracted images.

However, the current main pipeline seems to depend more on `page_by_page_ocr.py`, because page-by-page OCR gives cleaner page-level JSON files for downstream chunking.

---

## Main Input

Configured at the top:

```python
INPUT_PATH = Path("Documents/Finance Acts/")
```

This can be:

```python
INPUT_PATH = Path("Documents/example.pdf")
```

or:

```python
INPUT_PATH = Path("Documents/Finance Acts/")
```

If it is a folder, the script recursively finds all `.pdf` files inside it.

---

## Main Output

If headers and footers are included:

```text
phase1_output/full_document_ocr/
```

If headers and footers are excluded:

```text
phase1_output/full_document_ocr/without_headers_footers/
```

Reports are saved inside:

```text
phase1_output/reports/ocr/
```

Example report:

```text
phase1_output/reports/ocr/Finance_Acts_report.json
```

---

## Important Config Variables

```python
METHOD = "hf"
PAGE_RANGE = None
MAX_OUTPUT_TOKENS = 12384
INCLUDE_IMAGES = True
INCLUDE_HEADERS_FOOTERS = True
TIMEOUT_SECONDS = 60 * 60 * 3
```

Meaning:

* `METHOD = "hf"` uses local HuggingFace mode.
* `METHOD = "vllm"` can be used if the Chandra vLLM server is running.
* `PAGE_RANGE = None` means process the full document.
* `INCLUDE_IMAGES = True` keeps extracted images.
* `INCLUDE_HEADERS_FOOTERS = True` includes headers and footers in OCR output.
* `TIMEOUT_SECONDS` controls how long the script waits before killing the OCR process.

---

## What It Does Internally

### Step 1: Discover PDF files

Function:

```python
get_pdf_files()
```

It checks whether `INPUT_PATH` is a single file or a folder. If it is a folder, it finds all PDFs recursively.

### Step 2: Run Chandra OCR

Function:

```python
run_chandra_cli()
```

It builds a terminal command like:

```bash
chandra <pdf_path> <output_dir> --method hf --max-output-tokens 12384 --include-images --include-headers-footers
```

Then it runs that command using `subprocess.run()`.

### Step 3: Discover Chandra outputs

Function:

```python
find_chandra_outputs()
```

It searches for:

```text
*.md
*.html
*metadata.json
*.png
*.jpg
*.jpeg
*.webp
```

### Step 4: Create sanity report

Function:

```python
run_simple_sanity_report()
```

It checks whether markdown files and metadata files were created. It also warns if the markdown text is empty or too short.

---

## What Can Be Safely Changed

A teammate can safely change:

```python
INPUT_PATH
METHOD
PAGE_RANGE
MAX_OUTPUT_TOKENS
INCLUDE_IMAGES
INCLUDE_HEADERS_FOOTERS
TIMEOUT_SECONDS
```

For the current pipeline, if the aim is page-level structure extraction, use `page_by_page_ocr.py` instead of `ocr.py`.

---

## When to Use This File

Use this file when:

* You want full-document OCR output.
* You want Markdown and HTML for the whole document.
* You want a quick full-document OCR report.
* You do not need page-by-page JSON for LLM extraction.

---

# 3.3 `page_by_page_ocr.py`

## Purpose

`page_by_page_ocr.py` is the main OCR script for the current pipeline.

It runs Chandra OCR one page at a time and saves each page as a separate JSON file. This page-level structure is important because the next step, `chunking.py`, expects `page_*.json` files.

---

## Main Input

Configured at the top:

```python
INPUT_PATH = Path(r"Documents\20160408_Finance_Act_2013.pdf")
```

This can be a single PDF file or a folder containing multiple PDFs.

---

## Main Output

If headers and footers are excluded:

```text
phase1_output/page_by_page_ocr/without_headers_footers/<document_name>/
```

If headers and footers are included:

```text
phase1_output/page_by_page_ocr/<document_name>/
```

Each page is saved as:

```text
page_0000.json
page_0001.json
page_0002.json
...
```

Important note: the current code loops from `0` to `total_pages - 1`, so page numbers are zero-based in the output files.

---

## Page JSON Structure

Each page output has this structure:

```json
{
  "document_id": "20160408_Finance_Act_2013",
  "source_file": "Documents\\20160408_Finance_Act_2013.pdf",
  "page_number": 0,
  "ocr": {
    "engine": "chandra_ocr_2",
    "method": "hf"
  },
  "content": {
    "raw_markdown": "...",
    "raw_html": "..."
  },
  "metadata": {},
  "sanity": {
    "char_count": 1234,
    "line_count": 50,
    "bangla_chars": 800,
    "english_chars": 100,
    "warnings": [],
    "status": "ok"
  }
}
```

The most important field for the next phase is:

```json
"content": {
  "raw_markdown": "..."
}
```

`chunking.py` reads this field.

---

## Important Config Variables

```python
METHOD = "hf"
MAX_OUTPUT_TOKENS = 12384
TIMEOUT_SECONDS = 60 * 20
INCLUDE_HEADERS_FOOTERS = False
```

The current configuration excludes headers and footers:

```python
INCLUDE_HEADERS_FOOTERS = False
```

That is usually good for legal point extraction because repeated page headers and footers can confuse the LLM.

---

## What It Does Internally

### Step 1: Find PDF files

Function:

```python
get_pdf_files()
```

It supports both single-PDF and folder input.

### Step 2: Count PDF pages

Function:

```python
get_pdf_page_count()
```

It uses `pypdf.PdfReader` to count pages.

### Step 3: Run OCR page by page

Function:

```python
run_chandra_single_page()
```

For each page, it creates a temporary folder:

```text
temp_page_XXXX/
```

Then it runs Chandra OCR with:

```bash
chandra <pdf_path> <temp_dir> --method hf --page-range <page_number> --max-output-tokens 12384 --no-headers-footers
```

After Chandra finishes, the script reads the temporary Markdown, HTML, and metadata files.

### Step 4: Save page JSON

Each page is saved as:

```text
page_XXXX.json
```

inside the document output folder.

### Step 5: Compute OCR sanity

Function:

```python
compute_sanity()
```

It checks:

* Whether the OCR text is empty.
* Whether the OCR text is very short.
* Whether there is encoding noise.
* Whether Bangla or English characters are detected.

### Step 6: Save final OCR run report

Function:

```python
run_simple_sanity_report()
```

It summarizes:

* Total PDFs.
* Successful PDFs.
* Failed PDFs.
* Total pages.
* Successful pages.
* Failed pages.
* Warning pages.
* Failed page numbers by PDF.

Report location:

```text
phase1_output/reports/page_by_page_ocr/<input_name>_report_without_headers_footers.json
```

---

## What Can Be Safely Changed

A teammate can safely change:

```python
INPUT_PATH
METHOD
MAX_OUTPUT_TOKENS
TIMEOUT_SECONDS
INCLUDE_HEADERS_FOOTERS
```

Be careful with page numbering. The script currently uses zero-based page numbers. If Chandra expects one-based page ranges in your environment, this should be tested carefully.

---

## When to Use This File

Use this file as the main OCR entry point before running `chunking.py`.

---

# 3.4 `chunking.py`

## Purpose

`chunking.py` converts page-level OCR JSON files into chunk-level JSON input for the LLM extractor.

Each chunk corresponds to one OCR page, but it also includes a small amount of surrounding context:

* `previous_tail`: the last part of the previous page.
* `next_head`: the first part of the next page.

This helps the LLM detect whether a legal point continues across page boundaries.

---

## Main Input

Configured at the top:

```python
PAGE_OCR_INPUT = Path(r"phase1_output\page_by_page_ocr\without_headers_footers\20160408_Finance_Act_2013")
```

This can be either:

1. A single document folder containing `page_*.json`, or
2. A parent folder containing multiple document folders.

---

## Main Output

Configured as:

```python
OUTPUT_ROOT = Path("phase1_output/chunks/without_headers_footers")
```

For a single document, it outputs:

```text
phase1_output/chunks/without_headers_footers/<document_name>/chunks.json
```

---

## Chunk JSON Structure

Each chunk looks like this:

```json
{
  "document_name": "20160408_Finance_Act_2013",
  "chunk_id": "20160408_Finance_Act_2013_chunk_0001",
  "page_number": 0,
  "text": "current page OCR markdown",
  "previous_tail": "last 800 characters from previous page",
  "next_head": "first 800 characters from next page"
}
```

The most important field is:

```json
"text": "current page OCR markdown"
```

This is the only text that the Phase 2 extractor should extract from.

The surrounding fields are only for continuity judgment:

```json
"previous_tail": "..."
"next_head": "..."
```

---

## Important Config Variables

```python
TAIL_SIZE = 800
HEAD_SIZE = 800
```

Meaning:

* `TAIL_SIZE = 800`: take the last 800 characters of the previous page.
* `HEAD_SIZE = 800`: take the first 800 characters of the next page.

These values are important. If they are too small, the LLM may miss continuation clues. If they are too large, the LLM may accidentally copy context text into the extracted output.

---

## What It Does Internally

### Step 1: Detect document folders

Function:

```python
get_document_folders()
```

It finds folders that directly contain files matching:

```text
page_*.json
```

### Step 2: Load all page JSON files

Function:

```python
load_pages_from_doc_folder()
```

It loads each page JSON file and sorts them by `page_number`.

### Step 3: Build chunks

Function:

```python
build_chunks_for_document()
```

For each page, it creates a chunk containing:

* document name
* chunk id
* page number
* current page text
* previous page tail
* next page head

### Step 4: Save chunks

Function:

```python
save_document_chunks()
```

It saves all chunks into:

```text
chunks.json
```

---

## What Can Be Safely Changed

A teammate can safely change:

```python
PAGE_OCR_INPUT
OUTPUT_ROOT
TAIL_SIZE
HEAD_SIZE
```

Usually, `TAIL_SIZE = 800` and `HEAD_SIZE = 800` are reasonable. Increase them only if continuation detection is weak. Decrease them if the extractor copies previous or next page context into the current output.

---

## When to Use This File

Run this after `page_by_page_ocr.py` and before `stateful_extraction_2.py`.

---

# 3.5 `stateful_extraction_2.py`

## Purpose

`stateful_extraction_2.py` is the core LLM extraction engine.

It reads `chunks.json`, sends each chunk to an Ollama model, extracts structured content blocks, optionally sends the output to a validator agent, repairs invalid outputs, and saves one structured JSON file per page/chunk.

This is the most important and most sensitive file in the project.

---

## Main Input

Configured at the top:

```python
INPUT_CHUNKS_PATH = Path(r"phase1_output\chunks\without_headers_footers\20160408_Finance_Act_2013\chunks.json")
```

This can be:

1. A direct `chunks.json` file,
2. A document folder containing `chunks.json`, or
3. A parent folder containing multiple document folders.

---

## Main Output

Page outputs are saved inside:

```text
phase2_output/page_outputs_2/<document_name>/
```

Each page/chunk produces a JSON file:

```text
<chunk_id>_page_XXXX.json
```

Reports are saved inside:

```text
phase2_output/reports/
```

Example:

```text
phase2_output/reports/20160408_Finance_Act_2013_report.json
```

---

## Critical Config Variables

```python
OLLAMA_MODEL = "gemma4:latest"
THINK = True
TEMPERATURE = 0.2
TOP_P = 0.1
NUM_CTX = 32768
MAX_RETRIES = 2
STOP_ON_FAILURE = False
USE_VALIDATOR_AGENT = True
MAX_VALIDATOR_REPAIR_ROUNDS = 3
SEND_PREVIOUS_OPEN_POINT_BLOCK = True
PREVIOUS_OPEN_POINT_TEXT_LIMIT = None
CHUNK_START = 48
CHUNK_END = None
```

The most important issue:

```python
CHUNK_START = 48
```

This means the extractor currently starts from chunk 48 and skips all previous chunks. For full-document extraction, change it to:

```python
CHUNK_START = 0
```

`CHUNK_END = None` means continue until the end.

---

## What the Extractor Produces

The extractor outputs a structure like:

```json
{
  "chunk_id": "20160408_Finance_Act_2013_chunk_0001",
  "page_number": 0,
  "document_id": "20160408_Finance_Act_2013",
  "content_blocks": [
    {
      "block_id": "p0000_b001",
      "type": "point",
      "point_number": "১",
      "text": "...",
      "continues_from_previous": false,
      "continues_to_next": true,
      "stitch_group_id": "point_১",
      "stitching_note": "..."
    }
  ],
  "output_carry_forward_state": {
    "status": "open",
    "active_point_number": "১",
    "active_stitch_group_id": "point_১",
    "active_point_summary": "...",
    "numbering_system": "bangla_digits",
    "expected_next_main_point": "২",
    "continuation_hint": "...",
    "last_visible_text": "..."
  }
}
```

---

## Allowed Block Types

The current extractor supports only these block types:

```text
point
metadata
ignore
uncertain
```

Meaning:

* `point`: main legal numbered content.
* `metadata`: title, chapter heading, date, notification header, authority line, preamble, signature line, etc.
* `ignore`: page number, repeated footer, OCR garbage.
* `uncertain`: used when the model is unsure.

Important limitation: the current code does not yet support a separate `paragraph` block type. If paragraph extraction is required, then `stateful_extraction_2.py`, `validator.py`, and `stitch_points.py` must all be updated together.

---

## How Continuation Works

The extractor uses three kinds of context:

```json
"previous_state": {}
"previous_tail": "..."
"next_head": "..."
```

It may also receive:

```json
"previous_open_point_block": {}
```

The key rule is:

```text
The model must extract text only from current_page_text.
```

`previous_tail`, `next_head`, and `previous_open_point_block` are only context. They help the model decide whether a point continues, but they must not be copied into the output text.

---

## Carry-Forward State

The carry-forward state is how the extractor remembers legal structure across pages.

Example:

```json
{
  "status": "open",
  "active_point_number": "৫",
  "active_stitch_group_id": "point_৫",
  "active_point_summary": "The current point discusses...",
  "numbering_system": "bangla_digits",
  "expected_next_main_point": "৬",
  "continuation_hint": "The next page may continue subpoint (ঘ).",
  "last_visible_text": "..."
}
```

If a point continues to the next page:

```json
"continues_to_next": true
```

Then the carry state should usually be:

```json
"status": "open"
```

If the point is complete:

```json
"continues_to_next": false
```

Then the carry state should usually be:

```json
"status": "closed"
```

---

## Main Functions

### `get_document_jobs()`

Detects whether the input is:

* a direct `chunks.json`,
* a document folder containing `chunks.json`, or
* a parent folder containing multiple document folders.

### `get_previous_open_point_block()`

Finds the last `point` block from the current output where:

```json
"continues_to_next": true
```

It sends this compact block to the next LLM call so the model can continue the same point.

### `build_user_prompt()`

Builds the JSON payload for the LLM.

It includes:

* chunk id
* page number
* document id
* previous state
* previous tail
* current page text
* next head
* previous open point block
* validator instruction, if this is a repair attempt

### `call_ollama()`

Calls the local Ollama model using:

```python
ollama.chat()
```

The response format is forced to JSON:

```python
format="json"
```

### `extract_json_object()`

Parses the model output. If the model returns extra text, it tries to extract the largest JSON object.

### `validate_and_normalize_output()`

Normalizes the model output so it follows the expected schema.

It fixes:

* missing block ids
* invalid block types
* string booleans
* missing stitch group ids
* invalid carry state values

### `fallback_output()`

If the extractor completely fails, this creates a fallback output.

If the previous state was open, the fallback assumes the current text continues the previous point. Otherwise, it marks the page as uncertain.

### `process_chunk()`

This is the main per-chunk extraction function.

Flow:

```text
1. Run extractor once.
2. Send result to validator.
3. If validator accepts, save result.
4. If validator rejects, retry extraction with validator correction.
5. Repeat up to MAX_VALIDATOR_REPAIR_ROUNDS.
```

### `run_extraction_for_document()`

Processes all chunks for one document.

It keeps track of:

* previous carry state
* previous open point block
* page-level reports
* validation logs
* failed pages
* processing time per page

### `run_extraction()`

Main entry point. It processes one or more documents and writes the final Phase 2 report.

---

## What Can Be Safely Changed

A teammate can safely change:

```python
INPUT_CHUNKS_PATH
OLLAMA_MODEL
THINK
TEMPERATURE
TOP_P
NUM_CTX
MAX_RETRIES
USE_VALIDATOR_AGENT
MAX_VALIDATOR_REPAIR_ROUNDS
PREVIOUS_OPEN_POINT_TEXT_LIMIT
CHUNK_START
CHUNK_END
```

Recommended default for full extraction:

```python
CHUNK_START = 0
CHUNK_END = None
USE_VALIDATOR_AGENT = True
```

---

## What Should Be Changed Carefully

Be very careful changing:

```python
SYSTEM_PROMPT
output schema
normalize_block()
normalize_state()
fallback_output()
process_chunk()
```

These parts control the contract between extraction, validation, and stitching. If the schema changes in Phase 2, Phase 3 must also be updated.

---

# 3.6 `validator.py`

## Purpose

`validator.py` is a second LLM agent that checks the extractor output.

It does not re-extract the page. It only checks whether the extractor made one of four serious mistakes.

This validator improves reliability because the extractor sometimes makes small but harmful mistakes, especially with carry-forward state or page continuation.

---

## Main Input

The validator receives:

```json
{
  "chunk_id": "...",
  "page_number": 0,
  "previous_state": {},
  "previous_tail": "...",
  "current_page_text": "...",
  "next_head": "...",
  "previous_open_point_block": {},
  "extractor_output": {}
}
```

It is called from `stateful_extraction_2.py`.

---

## Main Output

The validator returns:

```json
{
  "is_valid": true,
  "error_types": [],
  "correction_instruction": ""
}
```

or:

```json
{
  "is_valid": false,
  "error_types": ["wrong_block_type"],
  "correction_instruction": "Retry this chunk. Legal amendment text was classified as metadata. Treat the numbered legal content as a point block."
}
```

The correction instruction is then sent back to the extractor for a repair attempt.

---

## Validator Model Config

```python
VALIDATOR_MODEL = "gemma4:latest"
THINK = False
VALIDATOR_TEMPERATURE = 0.3
VALIDATOR_TOP_P = 0.2
VALIDATOR_NUM_CTX = 32768
VALIDATOR_TEXT_LIMIT = 2500
```

The validator uses a shorter and more focused context. It compacts long `previous_tail` and `next_head` values using:

```python
compact_text()
```

---

## The Four Major Errors It Checks

### 1. `wrong_block_split`

This means the extractor split one continuous legal point into multiple point blocks unnecessarily.

Example:

```text
Point ৩ starts on the current page and includes a table, but the extractor separates the table into another point block.
```

That should be invalid.

---

### 2. `copied_context_text`

This means the extractor copied text from:

```text
previous_tail
next_head
previous_open_point_block
```

even though that text does not appear in the current page.

This is a serious source-boundary error.

Correct behavior:

```text
Use previous_tail and next_head only to understand continuation.
Extract block text only from current_page_text.
```

---

### 3. `wrong_block_type`

This means legal point text was classified as metadata, ignore, or uncertain, or clear metadata was classified as a point.

Example:

```text
A numbered amendment is marked as metadata.
```

That should be invalid.

---

### 4. `carry_state_inconsistency`

This means the extracted blocks and carry-forward state contradict each other.

Examples:

```text
continues_to_next = true, but output_carry_forward_state.status = closed
```

or:

```text
continues_to_next = true, but active_point_number is empty
```

or:

```text
active_stitch_group_id changes randomly
```

---

## Conservative Validation Philosophy

The validator is intentionally conservative.

It should not reject outputs for minor issues, formatting differences, short summaries, or debatable classification. It only rejects outputs when one of the four major errors is clearly present.

This is important because an overly strict validator would create unnecessary retry loops and slow down the pipeline.

---

## Main Functions

### `compact_text()`

Limits long context fields to keep validator prompts smaller.

### `normalize_validator_response()`

Cleans the validator response and ensures the output schema is valid.

It also filters error types so only the four allowed errors are accepted.

### `parse_json_object()`

Parses validator JSON output.

### `validate_extraction_with_agent()`

Main function called by `stateful_extraction_2.py`.

It sends the extractor output and chunk context to the validator model, parses the response, and returns a normalized validation result.

If the validator itself fails, it returns valid by default:

```json
{
  "is_valid": true,
  "error_types": [],
  "correction_instruction": ""
}
```

This prevents the validator from blocking the whole extraction pipeline.

---

## What Can Be Safely Changed

A teammate can safely change:

```python
VALIDATOR_MODEL
THINK
VALIDATOR_TEMPERATURE
VALIDATOR_TOP_P
VALIDATOR_NUM_CTX
VALIDATOR_TEXT_LIMIT
```

They can also adjust the validator prompt, but should not make it too aggressive.

---

## What Should Be Changed Carefully

If new block types are added, such as:

```text
paragraph
```

then the validator must be updated to understand that new type. Otherwise, it may incorrectly mark outputs valid or invalid.

---

# 3.7 `stitch_points.py`

## Purpose

`stitch_points.py` is Phase 3 of the pipeline.

It reads all Phase 2 page outputs, stitches multi-page point blocks using `stitch_group_id`, and creates one final structured JSON document.

This is the script that produces the final clean output.

---

## Main Input

Configured at the top:

```python
PHASE2_INPUT_PATH = Path(r"phase2_output\page_outputs_2\20160408_Finance_Act_2013")
```

This can be:

1. A single document folder containing Phase 2 page JSON files,
2. A parent folder containing multiple document folders, or
3. A nested group folder containing document folders.

---

## Main Output

Final stitched document:

```text
phase3_output/final_documents/<document_name>/final_stitched_document.json
```

Per-document report:

```text
phase3_output/reports/<document_name>/phase3_stitch_report.json
```

Master report:

```text
phase3_output/reports/phase3_master_report.json
```

---

## Final JSON Structure

The final output looks like:

```json
{
  "contents": [
    {
      "text": "Document title or heading",
      "type": "metadata",
      "clause_number": ""
    },
    {
      "text": "Full stitched legal point text...",
      "type": "clause",
      "clause_number": "১"
    }
  ]
}
```

Important note: Phase 2 uses block type `point`, but Phase 3 converts final legal points into type:

```json
"type": "clause"
```

So in the final output:

* Phase 2 `metadata` becomes final `metadata`.
* Phase 2 `point` becomes final `clause`.
* Phase 2 `ignore` is skipped.
* Phase 2 `uncertain` is skipped unless `INCLUDE_UNCERTAIN_IN_FINAL = True`.

---

## Important Config Variables

```python
INCLUDE_UNCERTAIN_IN_FINAL = False
FINAL_JSON_NAME = "final_stitched_document.json"
REPORT_JSON_NAME = "phase3_stitch_report.json"
```

If:

```python
INCLUDE_UNCERTAIN_IN_FINAL = False
```

then uncertain blocks are excluded from the final document.

If changed to:

```python
INCLUDE_UNCERTAIN_IN_FINAL = True
```

then uncertain blocks are included as metadata.

---

## How Stitching Works

Phase 3 uses:

```json
"stitch_group_id": "point_৫"
```

to identify the same legal point across multiple pages.

It keeps two dictionaries:

```python
open_groups
latest_group_index
```

### `open_groups`

Tracks points that are currently open because they have:

```json
"continues_to_next": true
```

### `latest_group_index`

Tracks the latest known location of a stitch group, even if it was already closed.

This allows recovery if the extractor says:

```json
"continues_from_previous": true
```

but the group was not technically open.

---

## Stitching Cases

### Case 1: Normal continuation

If a block has:

```json
"continues_from_previous": true
```

and the same group is open, Phase 3 appends the current block text to the existing clause.

---

### Case 2: Recovery from closed group

If the block says:

```json
"continues_from_previous": true
```

but the group is not open, Phase 3 checks `latest_group_index`.

If the group exists there, it appends anyway and records a warning.

---

### Case 3: Implicit continuation

If the same group is still open but the model forgot to set:

```json
"continues_from_previous": true
```

Phase 3 still appends the block and records a warning.

---

### Case 4: New point

If there is no open group and no matching latest group, Phase 3 creates a new final clause.

---

## Duplicate Boundary Handling

Function:

```python
remove_boundary_overlap()
```

This tries to remove repeated boundary text if the end of the previous block overlaps with the beginning of the next block.

This helps prevent duplicate text when the extractor accidentally repeats a small part of the previous page.

---

## Phase 3 Report

The stitch report includes:

```json
{
  "document_name": "...",
  "total_pages": 50,
  "total_blocks": 120,
  "final_content_items": 80,
  "metadata_blocks": 20,
  "stitched_point_blocks": 15,
  "ignored_blocks": 5,
  "uncertain_blocks": 3,
  "open_groups_remaining": [],
  "warnings": [],
  "skipped_blocks": [],
  "uncertain_block_details": []
}
```

Important fields:

* `stitched_point_blocks`: how many continuation blocks were appended.
* `open_groups_remaining`: should usually be empty at the end.
* `warnings`: shows stitching issues that were recovered.
* `uncertain_block_details`: useful for manual review.

---

## What Can Be Safely Changed

A teammate can safely change:

```python
PHASE2_INPUT_PATH
INCLUDE_UNCERTAIN_IN_FINAL
FINAL_JSON_NAME
REPORT_JSON_NAME
```

---

## What Should Be Changed Carefully

Be careful changing:

```python
make_group_id()
append_text()
remove_boundary_overlap()
stitch_document()
```

These functions control final document correctness.

If a new block type like `paragraph` is added, this file must be updated to handle paragraph stitching separately from point stitching.

---

# 4. Full Pipeline Input and Output Flow

The whole system can be understood as this chain:

```text
Raw PDF
  ↓
page_by_page_ocr.py
  ↓
phase1_output/page_by_page_ocr/without_headers_footers/<document>/page_XXXX.json
  ↓
chunking.py
  ↓
phase1_output/chunks/without_headers_footers/<document>/chunks.json
  ↓
stateful_extraction_2.py + validator.py
  ↓
phase2_output/page_outputs_2/<document>/<chunk_id>_page_XXXX.json
  ↓
stitch_points.py
  ↓
phase3_output/final_documents/<document>/final_stitched_document.json
```

---

# 5. Recommended Running Procedure

## Step 1: Put PDF in `Documents/`

Example:

```text
Documents/20160408_Finance_Act_2013.pdf
```

---

## Step 2: Run page-level OCR

Update in `page_by_page_ocr.py`:

```python
INPUT_PATH = Path(r"Documents\20160408_Finance_Act_2013.pdf")
INCLUDE_HEADERS_FOOTERS = False
```

Run:

```bash
python page_by_page_ocr.py
```

Expected output:

```text
phase1_output/page_by_page_ocr/without_headers_footers/20160408_Finance_Act_2013/page_0000.json
phase1_output/reports/page_by_page_ocr/20160408_Finance_Act_2013_report_without_headers_footers.json
```

---

## Step 3: Run chunking

Update in `chunking.py`:

```python
PAGE_OCR_INPUT = Path(r"phase1_output\page_by_page_ocr\without_headers_footers\20160408_Finance_Act_2013")
```

Run:

```bash
python chunking.py
```

Expected output:

```text
phase1_output/chunks/without_headers_footers/20160408_Finance_Act_2013/chunks.json
```

---

## Step 4: Run Phase 2 LLM extraction

Update in `stateful_extraction_2.py`:

```python
INPUT_CHUNKS_PATH = Path(r"phase1_output\chunks\without_headers_footers\20160408_Finance_Act_2013\chunks.json")
CHUNK_START = 0
CHUNK_END = None
USE_VALIDATOR_AGENT = True
```

Run:

```bash
python stateful_extraction_2.py
```

Expected output:

```text
phase2_output/page_outputs_2/20160408_Finance_Act_2013/
phase2_output/reports/20160408_Finance_Act_2013_report.json
```

---

## Step 5: Run Phase 3 stitching

Update in `stitch_points.py`:

```python
PHASE2_INPUT_PATH = Path(r"phase2_output\page_outputs_2\20160408_Finance_Act_2013")
```

Run:

```bash
python stitch_points.py
```

Expected output:

```text
phase3_output/final_documents/20160408_Finance_Act_2013/final_stitched_document.json
phase3_output/reports/20160408_Finance_Act_2013/phase3_stitch_report.json
phase3_output/reports/phase3_master_report.json
```

---

# 6. Dependencies and Environment Requirements

The project depends on these Python packages and external tools:

## Python packages

```bash
pip install pdf2image pillow opencv-python numpy pandas pypdf ollama
```

## External tools

### Chandra OCR

The OCR scripts assume the `chandra` command is available from the terminal.

Test with:

```bash
chandra --help
```

### Ollama

The extraction and validator scripts assume Ollama is installed and running.

Test with:

```bash
ollama list
```

The configured model is:

```text
gemma4:latest
```

If this model is not available, pull it or change the model name in both:

```text
stateful_extraction_2.py
validator.py
```

---

# 7. Current Limitations

## 7.1 Paragraph support is not implemented yet

The current Phase 2 schema supports:

```text
point
metadata
ignore
uncertain
```

It does not yet support:

```text
paragraph
```

So if a document has large non-numbered informational paragraphs, the current extractor will likely classify them as metadata or uncertain.

To properly support paragraphs, these files must be updated together:

```text
stateful_extraction_2.py
validator.py
stitch_points.py
```

Required changes:

1. Add `paragraph` to the allowed block types.
2. Update the system prompt to define paragraph behavior.
3. Update `default_content_block()` and `normalize_block()`.
4. Update validator rules so paragraphs are not wrongly rejected.
5. Update Phase 3 so paragraphs are included in the final output.
6. Add paragraph stitching using `stitch_group_id`, similar to point stitching.

---

## 7.2 `CHUNK_START = 48` may skip pages

In `stateful_extraction_2.py`, the current setting is:

```python
CHUNK_START = 48
```

This is useful for debugging from a specific chunk, but it is dangerous for full-document extraction.

For full runs, use:

```python
CHUNK_START = 0
```

---

## 7.3 Page numbers are zero-based in OCR

`page_by_page_ocr.py` currently loops using:

```python
for page_number in range(0, total_pages):
```

This creates files like:

```text
page_0000.json
```

This is okay if the whole pipeline expects zero-based page numbering. But it can confuse humans because PDF page 1 becomes `page_0000.json`.

---

## 7.4 Validator is LLM-based, not deterministic

The validator is helpful, but it is still an LLM. It may miss errors or occasionally accept imperfect extraction.

There is commented deterministic logic in `validator.py` for detecting copied context text. This could be useful later if more strict checking is needed.

---

# 8. Safe Modification Guide for Teammates

## To change the input document

Change:

```python
INPUT_PATH
```

in `page_by_page_ocr.py`.

Then update:

```python
PAGE_OCR_INPUT
```

in `chunking.py`.

Then update:

```python
INPUT_CHUNKS_PATH
```

in `stateful_extraction_2.py`.

Then update:

```python
PHASE2_INPUT_PATH
```

in `stitch_points.py`.

---

## To change the OCR behavior

Modify:

```python
INCLUDE_HEADERS_FOOTERS
METHOD
MAX_OUTPUT_TOKENS
TIMEOUT_SECONDS
```

in `page_by_page_ocr.py`.

---

## To change context size around pages

Modify:

```python
TAIL_SIZE
HEAD_SIZE
```

in `chunking.py`.

---

## To change the LLM model

Modify:

```python
OLLAMA_MODEL
```

in `stateful_extraction_2.py`.

Modify:

```python
VALIDATOR_MODEL
```

in `validator.py`.

Both should usually use the same model unless there is a reason to use a smaller or faster validator model.

---

## To run only part of a document

Modify:

```python
CHUNK_START
CHUNK_END
```

in `stateful_extraction_2.py`.

Example:

```python
CHUNK_START = 10
CHUNK_END = 20
```

This processes chunks 10 to 19.

For the full document:

```python
CHUNK_START = 0
CHUNK_END = None
```

---

## To disable validator

Modify:

```python
USE_VALIDATOR_AGENT = False
```

in `stateful_extraction_2.py`.

This will make extraction faster but less reliable.

---

# 9. Key Development Warning

The pipeline is schema-dependent. The Phase 2 output schema and Phase 3 input assumptions must match.

If anyone changes this structure:

```json
{
  "content_blocks": [
    {
      "type": "point",
      "point_number": "",
      "text": "",
      "continues_from_previous": false,
      "continues_to_next": false,
      "stitch_group_id": ""
    }
  ]
}
```

then they must also update:

```text
validator.py
stitch_points.py
```

Otherwise, Phase 3 may ignore blocks, fail to stitch points, or produce incomplete final JSON.

---

# 10. Summary for New Teammate

This project converts legal PDFs into structured JSON through a multi-stage OCR and LLM extraction pipeline.

Use this order:

```bash
python page_by_page_ocr.py
python chunking.py
python stateful_extraction_2.py
python stitch_points.py
```

The most important file for OCR is:

```text
page_by_page_ocr.py
```

The most important file for LLM extraction is:

```text
stateful_extraction_2.py
```

The validator is:

```text
validator.py
```

The final stitching file is:

```text
stitch_points.py
```

The final output is:

```text
phase3_output/final_documents/<document_name>/final_stitched_document.json
```

Before running the full extraction, always check:

```python
CHUNK_START = 0
```

in `stateful_extraction_2.py`.

Also remember that paragraph extraction is not yet fully implemented. The current pipeline mainly handles legal points, metadata, ignored text, and uncertain text.
