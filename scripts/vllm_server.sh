#!/bin/bash

MODEL_PATH="/home/emran/Desktop/dgx_2/sscl-works/base-models/google/gemma-4-31B-it"

# Gemma-4-31B is a multimodal model (image+text). 
# limit_mm_per_prompt is needed even for text-only use.
vllm serve "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port 8000 \
    --served-model-name "gemma-4-31b-it" \
    --tensor-parallel-size 1 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.90 \
    --dtype bfloat16 \
    --trust-remote-code \
    --limit_mm_per_prompt "image=0"