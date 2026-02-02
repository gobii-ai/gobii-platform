import logging
import threading

from django.conf import settings

from .promptree import hmt

logger = logging.getLogger(__name__)

LLM_LINGUA_MIN_RATIO = 0.01
LLM_LINGUA_FORCE_TOKENS = [
    "\n"
]

_LLM_LINGUA_LOCK = threading.Lock()
_LLM_LINGUA_COMPRESSOR = None


def _get_llm_lingua_compressor():
    global _LLM_LINGUA_COMPRESSOR
    if _LLM_LINGUA_COMPRESSOR is not None:
        return _LLM_LINGUA_COMPRESSOR
    if not settings.LLMLINGUA_ENABLED:
        return None
    with _LLM_LINGUA_LOCK:
        if _LLM_LINGUA_COMPRESSOR is not None:
            return _LLM_LINGUA_COMPRESSOR
        try:
            from llmlingua import PromptCompressor
        except ImportError:
            logger.debug("LLM-Lingua not installed; falling back to HMT shrinker.")
            return None
        try:
            compressor_kwargs = {
                "model_name": settings.LLMLINGUA_MODEL_NAME,
                "device_map": settings.LLMLINGUA_DEVICE_MAP,
                "use_llmlingua2": settings.LLMLINGUA_USE_LLM_LINGUA2,
            }
            if settings.LLMLINGUA_MODEL_CONFIG:
                compressor_kwargs["model_config"] = settings.LLMLINGUA_MODEL_CONFIG
            _LLM_LINGUA_COMPRESSOR = PromptCompressor(**compressor_kwargs)
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.exception("Failed to initialize LLM-Lingua compressor; falling back to HMT shrinker.")
            return None
    return _LLM_LINGUA_COMPRESSOR


def llm_lingua_shrinker(text: str, k: float) -> str:
    """Shrink text using LLM-Lingua, falling back to HMT when unavailable."""
    if not text:
        return text
    k = max(LLM_LINGUA_MIN_RATIO, min(k, 1.0))
    if k >= 0.99:
        return text

    compressor = _get_llm_lingua_compressor()
    if compressor is None:
        return hmt(text, k)

    try:
        result = compressor.compress_prompt(
            text,
            rate=k,
            force_tokens=LLM_LINGUA_FORCE_TOKENS,
        )
        compressed = result.get("compressed_prompt") if isinstance(result, dict) else result
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.exception("LLM-Lingua compression failed; falling back to HMT shrinker.")
        return hmt(text, k)

    if not compressed:
        return hmt(text, k)
    if len(compressed) >= len(text) and k < 0.99:
        return hmt(text, k)
    return compressed
