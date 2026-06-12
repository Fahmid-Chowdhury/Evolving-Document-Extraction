#!/bin/bash
set -e

# Detect if running inside Docker (no .env file present in container)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    # Running locally — load .env and cd to project root
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
    cd "$PROJECT_ROOT"
fi

# For local runs, set VLLM_BASE_URL to localhost if not already set by Compose
if [ -z "${VLLM_BASE_URL}" ]; then
    export VLLM_BASE_URL="http://localhost:${VLLM_PORT:-8000}/v1"
fi

echo "=========================================="
echo " Evolving-Document-Extraction Pipeline"
echo " Phase 2 + Phase 3 only"
echo "=========================================="
echo " INPUT_CHUNKS_PATH : ${INPUT_CHUNKS_PATH}"
echo " PHASE2_INPUT_PATH : ${PHASE2_INPUT_PATH}"
echo " CHUNK_START       : ${CHUNK_START}"
echo " CHUNK_END         : ${CHUNK_END:-<full document>}"
echo " VLLM_BASE_URL     : ${VLLM_BASE_URL}"
echo "=========================================="

echo ""
echo "Waiting for vLLM at ${VLLM_BASE_URL}..."
until curl -sf "${VLLM_BASE_URL}/models" > /dev/null 2>&1; do
    echo "  Not ready yet, retrying in 5s..."
    sleep 5
done
echo "vLLM is ready."

echo ""
echo "[Phase 2] stateful_extraction_2.py ..."
python stateful_extraction_2.py

echo ""
echo "[Phase 3] stitch_points.py ..."
python stitch_points.py

echo ""
echo "=========================================="
echo " Done."
echo " Results: phase3_output/final_documents/"
echo "=========================================="