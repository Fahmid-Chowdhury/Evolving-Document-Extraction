"""
Shared local LLM client for the OCR document extraction pipeline.

Supported backends:
1. Ollama local server
2. HuggingFace Transformers local model

This keeps the extractor/validator code clean. To switch model backend,
change MODEL_BACKEND / VALIDATOR_BACKEND in the caller file.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Literal, Optional
import os
import re
from pathlib import Path
import torch


BackendName = Literal["ollama", "hf", "huggingface"]

TEMPERATURE = 0.2    # Default temperature for HuggingFace generation. Ollama uses the config value directly.
TOP_P = 0.1    # Default top_p for HuggingFace generation. Ollama uses the config value directly.

@dataclass(frozen=True)
class LLMRequestConfig:
    backend: BackendName
    model: str
    temperature: float = 0.2
    top_p: float = 0.1
    num_ctx: Optional[int] = None
    max_new_tokens: int = 4096
    think: bool = False
    response_format: str = "json"  # Ollama uses this directly. HF follows prompt instruction.


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    config: LLMRequestConfig,
) -> str:
    """
    Sends a system+user prompt to the selected local backend and returns text.

    The rest of the pipeline can keep parsing/validating JSON exactly as before.
    """

    backend = str(config.backend).strip().lower()

    if backend == "ollama":
        return _chat_ollama(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            config=config,
        )

    if backend in {"hf", "huggingface"}:
        return _chat_huggingface(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            config=config,
        )

    raise ValueError(
        f"Unsupported LLM backend: {config.backend}. Use 'ollama' or 'hf'."
    )


# ============================================================
# OLLAMA BACKEND
# ============================================================

def _chat_ollama(
    *,
    system_prompt: str,
    user_prompt: str,
    config: LLMRequestConfig,
) -> str:
    try:
        import ollama
    except ImportError as exc:
        raise ImportError(
            "Ollama backend selected, but Python package 'ollama' is not installed. "
            "Run: pip install ollama"
        ) from exc

    options: Dict[str, Any] = {
        "temperature": config.temperature,
        "top_p": config.top_p,
    }

    if config.num_ctx is not None:
        options["num_ctx"] = config.num_ctx

    # ollama.chat expects format to be either None, '' , 'json' or a JSON-serializable dict.
    format_param: Any
    if config.response_format in (None, "", "json"):
        format_param = config.response_format
    else:
        # try to parse a JSON string into a dict, otherwise fall back to None
        try:
            import json

            parsed = json.loads(config.response_format)
            format_param = parsed if isinstance(parsed, dict) else None
        except Exception:
            format_param = None

    response = ollama.chat(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format=format_param,
        think=config.think,
        options=options,
    )

    return response["message"]["content"]


# ============================================================
# HUGGINGFACE BACKEND
# ============================================================

@dataclass
class _HFBundle:
    processor: Any
    model: Any


@lru_cache(maxsize=2)
def _load_hf_bundle(model_name: str) -> _HFBundle:
    """
    Load once and reuse across extractor/validator calls.

    device_map='auto' requires accelerate and will place the model on GPU when available.
    """
    
    OFFLOAD_DIR = Path("hf_offload")
    OFFLOAD_DIR.mkdir(exist_ok=True)

    try:
        from transformers import AutoProcessor, AutoModelForCausalLM
    except ImportError as exc:
        raise ImportError(
            "HuggingFace backend selected, but dependencies are missing. Run:\n"
            "pip install -U transformers torch accelerate"
        ) from exc

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")

    processor = AutoProcessor.from_pretrained(
        model_name,
        token=token,
    )

    # Newer Transformers accepts dtype='auto'. Some older versions expect torch_dtype.
    try:
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            dtype=torch.float16,
            device_map="auto",
            max_memory={
                0: "15GiB",
                "cpu": "4GiB",
            },
            token=token,
            offload_folder=str(OFFLOAD_DIR),
            offload_buffers=True,
            low_cpu_mem_usage=True,
        )
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map="auto",
            token=token,
            offload_folder=str(OFFLOAD_DIR),
            offload_buffers=True,
            low_cpu_mem_usage=True,
        )
    
    model.eval()
    
    print("Device map:", getattr(model, "hf_device_map", "No device map"))

    return _HFBundle(processor=processor, model=model)


def _model_input_device(model: Any):
    """
    Finds a real execution device for input tensors.
    Avoids returning 'meta' when Accelerate offloads layers.
    """

    device_map = getattr(model, "hf_device_map", None)

    if isinstance(device_map, dict):
        for device in device_map.values():
            if isinstance(device, int):
                return f"cuda:{device}"
            if isinstance(device, str) and device not in {"cpu", "disk", "meta"}:
                return device

        if "cpu" in device_map.values():
            return "cpu"

    try:
        device = model.device
        if str(device) != "meta":
            return device
    except Exception:
        pass

    try:
        device = next(model.parameters()).device
        if str(device) != "meta":
            return device
    except Exception:
        pass

    return "cpu"


def _chat_huggingface(
    *,
    system_prompt: str,
    user_prompt: str,
    config: LLMRequestConfig,
) -> str:
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "HuggingFace backend selected, but torch is not installed. "
            "Run: pip install torch"
        ) from exc

    bundle = _load_hf_bundle(config.model)
    processor = bundle.processor
    model = bundle.model

    # HF does not enforce JSON with a native format='json' switch like Ollama.
    # So we strengthen the instruction without changing your schema.
    final_user_prompt = user_prompt

    if config.response_format == "json":
        final_user_prompt = (
            user_prompt
            + "\n\nReturn exactly one valid JSON object only. "
              "No markdown. No explanation. No text before or after JSON."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": final_user_prompt},
    ]

    # Some Gemma processors support enable_thinking. Some older versions may not.
    try:
        text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            # enable_thinking=config.think,
            enable_thinking=False,
        )
    except TypeError:
        text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    inputs = processor(text=text, return_tensors="pt")

    input_len = inputs["input_ids"].shape[-1]
    device = _model_input_device(model)

    inputs = {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }

    generation_kwargs: Dict[str, Any] = {
        "max_new_tokens": config.max_new_tokens,
        "do_sample": config.temperature > 0,
    }

    tokenizer = getattr(processor, "tokenizer", processor)
    eos_token_id = getattr(tokenizer, "eos_token_id", None)

    if eos_token_id is not None:
        generation_kwargs["pad_token_id"] = eos_token_id

    if config.temperature > 0:
        generation_kwargs["temperature"] = TEMPERATURE if TEMPERATURE != 0 else config.temperature
        generation_kwargs["top_p"] = TOP_P if TOP_P != 0 else config.top_p

    with torch.inference_mode():
        outputs = model.generate(**inputs, **generation_kwargs)

    generated = outputs[0][input_len:]

    # First decode with special tokens because some processors parse response better this way.
    raw_response = processor.decode(generated, skip_special_tokens=False)

    parsed = _try_parse_llm_response(processor, raw_response)

    if parsed:
        return parsed

    # Fallback: decode cleanly and remove possible thinking blocks.
    clean_response = processor.decode(generated, skip_special_tokens=True)
    return _strip_thinking_blocks(clean_response).strip()


def _try_parse_llm_response(processor: Any, raw_response: str) -> str:
    parse_response = getattr(processor, "parse_response", None)

    if not callable(parse_response):
        return ""

    try:
        parsed = parse_response(raw_response)
    except Exception:
        return ""

    if isinstance(parsed, str):
        return _strip_thinking_blocks(parsed).strip()

    if isinstance(parsed, dict):
        # Different Transformers versions may use different keys.
        for key in ["answer", "content", "response", "text"]:
            value = parsed.get(key)

            if isinstance(value, str) and value.strip():
                return _strip_thinking_blocks(value).strip()

        # Last fallback: if the parsed dict itself is the JSON response.
        import json
        return json.dumps(parsed, ensure_ascii=False)

    return ""


def _strip_thinking_blocks(text: str) -> str:
    """
    Removes common reasoning/thinking wrappers if the model emits them.
    This is defensive; your existing JSON extractor still handles extra text.
    """

    text = str(text or "")
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"<thinking>.*?</thinking>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return text