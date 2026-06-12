# Evolving-Document-Extraction — How to Run

Phase 1 (OCR + chunking) must already be complete before running this.

---

## Prerequisites

- Docker + Docker Compose
- NVIDIA Container Toolkit
- Model weights under `/home/emran/Desktop/dgx_2/sscl-works/base-models/`

---

## The only file you edit: `.env`

```env
# vLLM
VLLM_PORT=8000
VLLM_MODEL_PATH=/models/Qwen/Qwen3.5-9B
VLLM_SERVED_MODEL_NAME=qwen3.5-9b

# Document to process
INPUT_CHUNKS_PATH=phase1_output/chunks/without_headers_footers/20160408_Finance_Act_2013/chunks.json
PHASE2_INPUT_PATH=phase2_output/page_outputs_2/20160408_Finance_Act_2013

# Chunk range — leave CHUNK_END blank for full document
CHUNK_START=0
CHUNK_END=
```

---

## Run via Docker

**1. Start vLLM server**
```bash
docker compose -f docker-compose.vllm.yml up -d
docker compose -f docker-compose.vllm.yml logs -f
# Wait for: "Application startup complete."
```

**2. Run the pipeline**
```bash
# First run or after code changes
docker compose -f docker-compose.pipeline.yml up --build

# Subsequent runs
docker compose -f docker-compose.pipeline.yml up
```

---

## Run locally (no Docker for pipeline)

vLLM must still be running via Docker. Then:

```bash
./scripts/run_pipeline.sh
```

---

## Stop everything

```bash
docker compose -f docker-compose.pipeline.yml down
docker compose -f docker-compose.vllm.yml down
```

---

## Switch model

Edit `.env` only:
```env
VLLM_MODEL_PATH=/models/google/gemma-4-31B-it
VLLM_SERVED_MODEL_NAME=gemma-4-31b-it
```

Then restart vLLM:
```bash
docker compose -f docker-compose.vllm.yml down
docker compose -f docker-compose.vllm.yml up -d
```

---

## Results

```
phase3_output/final_documents/<doc>/final_stitched_document.json  ← final output
phase2_output/reports/<doc>_report.json                           ← Phase 2 report
phase3_output/reports/<doc>/phase3_stitch_report.json             ← Phase 3 report
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Pipeline can't reach vLLM | `docker network inspect llm-net` — both containers must appear |
| Container name conflict | `docker compose -f docker-compose.vllm.yml down --remove-orphans` |
| Code changed, need rebuild | `docker compose -f docker-compose.pipeline.yml up --build` |
| OOM on GPU | Reduce `--gpu-memory-utilization` in `docker-compose.vllm.yml` |

---

## Available Models

| Model | `VLLM_MODEL_PATH` | Est. t/s (GB10) |
|-------|-------------------|-----------------|
| Qwen3.5-9B | `/models/Qwen/Qwen3.5-9B` | ~25-30 |
| Gemma-4-E4B-it | `/models/google/gemma-4-E4B-it` | ~20-25 |
| GPT-OSS-20B | `/models/openai/gpt-oss-20b` | ~6-8 |
| Gemma-4-31B-it | `/models/google/gemma-4-31B-it` | ~3-4 |