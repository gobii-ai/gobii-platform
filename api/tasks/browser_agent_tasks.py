import os
import logging
import asyncio
import json
import hashlib
import mimetypes
import tempfile
import shutil
import random
import stat
import time
from typing import Any, Awaitable, Callable, List, Dict, Tuple, Optional
import tarfile
import zstandard as zstd
from browser_use.browser.profile import ProxySettings
from django.core.files.storage import default_storage
from django.core.files import File

from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.db import close_old_connections
from django.db.utils import OperationalError

from observability import traced, trace
from ..agent.core.budget import AgentBudgetManager
from ..models import BrowserUseAgentTask, BrowserUseAgentTaskStep, ProxyServer
from util import EphemeralXvfb, should_use_ephemeral_xvfb

tracer = trace.get_tracer('gobii.utils')

# --------------------------------------------------------------------------- #
#  Optional libs – in the worker container these are installed; in migrations
#  or other management contexts they may be missing.
# --------------------------------------------------------------------------- #

# Disable browser_use telemetry
os.environ["ANONYMIZED_TELEMETRY"] = "false"

try:
    from browser_use import BrowserSession, BrowserProfile, Agent as BUAgent, Controller  # safe: telemetry is already off
    from browser_use.llm import ChatGoogle, ChatOpenAI, ChatAnthropic  # safe: telemetry is already off
    from json_schema_to_pydantic import create_model
    from opentelemetry import baggage

    LIBS_AVAILABLE = True
    IMPORT_ERROR = None
except ImportError as e:  # e.g. when running manage.py commands
    BrowserSession = BrowserProfile = BUAgent = ChatGoogle = ChatOpenAI = ChatAnthropic = Controller = create_model = baggage = None  # type: ignore
    LIBS_AVAILABLE = False
    IMPORT_ERROR = str(e)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Robust temp‑dir helpers
# --------------------------------------------------------------------------- #
def _handle_remove_readonly(func, path, exc_info):  # noqa: ANN001
    """Make a read‑only file writable and retry removal."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to remove %s during robust rmtree", path, exc_info=True)


def _robust_rmtree(path: str) -> None:
    """Try hard to delete a directory; log if it ultimately fails."""
    for _ in range(3):
        try:
            shutil.rmtree(path, onerror=_handle_remove_readonly)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    logger.warning("Failed to remove temp profile dir after retries: %s", path)


# --------------------------------------------------------------------------- #
#  Chrome profile pruning helpers
# --------------------------------------------------------------------------- #

CHROME_PROFILE_PRUNE_DIRS = [
    "Cache",
    "Code Cache",
    "ShaderCache",
    "GPUCache",
    os.path.join("Service Worker", "CacheStorage"),
    os.path.join("Crashpad", "completed"),
    os.path.join("Crashpad", "pending"),
    "Safe Browsing",
]

CHROME_PROFILE_PRUNE_FILES = ["BrowserMetrics-spare.pma", "SingletonCookie", "SingletonLock", "SingletonSocket"]

# Reset profile if bigger than this after pruning (in bytes)
CHROME_PROFILE_MAX_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


def _prune_chrome_profile(profile_dir: str) -> None:
    """Remove cache/temporary sub-directories and files from a Chrome user data
    directory to minimise its size before persistence."""
    # --------------------------------------------------------------
    #  Measure size before pruning
    # --------------------------------------------------------------
    def _dir_size(path: str) -> int:
        size = 0
        for dirpath, _dnames, fnames in os.walk(path):
            for fn in fnames:
                try:
                    size += os.path.getsize(os.path.join(dirpath, fn))
                except FileNotFoundError:
                    pass  # File may disappear; ignore
        return size

    pre_prune_size_bytes = _dir_size(profile_dir)
    logger.info("Chrome profile size before pruning: %.1f MB", pre_prune_size_bytes / (1024 * 1024))

    pruned_dirs: list[str] = []
    pruned_files: list[str] = []

    # Remove known directories first
    for rel_path in CHROME_PROFILE_PRUNE_DIRS:
        full_path = os.path.join(profile_dir, rel_path)
        if os.path.exists(full_path):
            try:
                _robust_rmtree(full_path)
                pruned_dirs.append(rel_path)
                logger.info("Pruned chrome profile dir: %s", full_path)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to prune dir %s", full_path, exc_info=True)

    # Remove individual files and wildcard patterns
    for root, _dirs, files in os.walk(profile_dir):
        for filename in files:
            if filename in CHROME_PROFILE_PRUNE_FILES or filename.endswith((".tmp", ".old")):
                file_path = os.path.join(root, filename)
                try:
                    os.unlink(file_path)
                    pruned_files.append(filename)
                    logger.info("Pruned chrome profile file: %s", file_path)
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to prune file %s", file_path, exc_info=True)

    # Measure size after pruning
    post_prune_size_bytes = _dir_size(profile_dir)
    logger.info(
        "Chrome profile pruning completed: size before %.1f MB, after %.1f MB; %d dirs, %d files removed",
        pre_prune_size_bytes / (1024 * 1024),
        post_prune_size_bytes / (1024 * 1024),
        len(pruned_dirs),
        len(pruned_files),
    )

    # --------------------------------------------------------------
    #  Reset profile if still too large
    # --------------------------------------------------------------
    if post_prune_size_bytes > CHROME_PROFILE_MAX_SIZE_BYTES:
        size_mb = post_prune_size_bytes / (1024 * 1024)
        logger.info(
            "Chrome profile still %.1f MB after pruning (>500 MB). Resetting directory.",
            size_mb,
        )
        try:
            _robust_rmtree(profile_dir)
            os.makedirs(profile_dir, exist_ok=True)
            logger.info("Chrome profile directory reset due to size constraint")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to reset oversized chrome profile directory")
    else:
        logger.info(
            "Chrome profile size after pruning within limit: %.1f MB",
            post_prune_size_bytes / (1024 * 1024),
            )

# --------------------------------------------------------------------------- #
#  Provider config / tiers / defaults
# --------------------------------------------------------------------------- #
PROVIDER_CONFIG: Dict[str, Dict[str, str]] = {
    "anthropic": {"env_var": "ANTHROPIC_API_KEY"},
    "openai": {"env_var": "OPENAI_API_KEY"},
    "openrouter": {"env_var": "OPENROUTER_API_KEY"},
    "google": {"env_var": "GOOGLE_API_KEY"},
    "fireworks": {"env_var": "FIREWORKS_AI_API_KEY"},
}

# Tier 1: 80% OpenAI GPT-4.1, 20% Anthropic. Tier 2: Google. Tier 3: 50% Fireworks, 50% OpenRouter. Tier 4: Anthropic.
# We only advance to the next tier if all providers in the current tier fail.
DEFAULT_PROVIDER_TIERS: List[List[Tuple[str, float]]] = [
    [("openai", 0.8), ("anthropic", 0.2)],     # Tier 1: 80% OpenAI GPT-4.1, 20% Anthropic (load balanced)
    [("google", 1.0)],     # Tier 2: 100% Google (Gemini 2.5 Pro)
    [("fireworks", 0.5), ("openrouter", 0.5)],     # Tier 3: 50% Fireworks Qwen3-235B, 50% OpenRouter GLM-4.5 (combined old tiers 1&2)
    [("anthropic", 1.0)],  # Tier 4: 100% Anthropic (rarely used)
]

# Allow override via Django settings (must be a list of lists of tuples, or flat list).
PROVIDER_PRIORITY: List[List[Any]] = getattr(
    settings, "LLM_PROVIDER_PRIORITY", DEFAULT_PROVIDER_TIERS
)

DEFAULT_GOOGLE_MODEL = getattr(settings, "GOOGLE_LLM_MODEL", "gemini-2.5-pro")

# --------------------------------------------------------------------------- #
#  Proxy helpers
# --------------------------------------------------------------------------- #
@tracer.start_as_current_span("SELECT Proxy")
def select_proxy_for_task(task_obj, override_proxy=None) -> Optional[ProxyServer]:
    """Select appropriate proxy for a task based on agent preferences and health checks."""
    from ..proxy_selection import select_proxy_for_browser_task

    span = trace.get_current_span()
    if task_obj and task_obj.id and baggage:
        baggage.set_baggage("task.id", str(task_obj.id))
        span.set_attribute("task.id", str(task_obj.id))
    if task_obj.user and task_obj.user.id and baggage:
        baggage.set_baggage("user.id", str(task_obj.user.id))
        span.set_attribute("user.id", str(task_obj.user.id))

    with traced("SELECT Proxy") as proxy_span:
        # Use the new proxy selection module with debug mode enabled
        proxy_server = select_proxy_for_browser_task(
            task_obj,
            override_proxy=override_proxy,
            allow_no_proxy_in_debug=True
        )

        # Add tracing attributes if we have a proxy
        if proxy_server:
            span.set_attribute("proxy.id", str(proxy_server.id))
            span.set_attribute("proxy.host", proxy_server.host)
            span.set_attribute("proxy.port", proxy_server.port)
            span.set_attribute("proxy.proxy_type", proxy_server.proxy_type)
            span.set_attribute("proxy.name", proxy_server.name)

            if override_proxy:
                proxy_span.set_attribute("override_proxy", True)
            elif task_obj.agent and task_obj.agent.preferred_proxy:
                span.set_attribute("task.agent.id", str(task_obj.agent.id))
                span.set_attribute("agent.id", task_obj.agent.name)
                span.set_attribute("agent.has_preferred_proxy", True)
                span.set_attribute("preferred_proxy.id", str(task_obj.agent.preferred_proxy.id))
        else:
            span.set_attribute("no_proxy_available", True)

        return proxy_server

# --------------------------------------------------------------------------- #
#  Async helpers
# --------------------------------------------------------------------------- #
async def _safe_aclose(obj: Any, close_attr: str = "aclose") -> None:
    """Await obj.aclose()/stop()/kill() (or given attr) if present, swallowing/logging errors."""
    if obj is None:
        return
    close_fn: Callable[[], Awaitable[Any]] | None = getattr(obj, close_attr, None)
    if close_fn is None:
        return
    try:
        await close_fn()  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        logger.debug("async close failed for %s: %s", obj, exc, exc_info=True)


def _jsonify(obj: Any) -> Any:
    """Convert `obj` into something json.dumps can handle."""
    try:
        json.dumps(obj)  # type: ignore[arg-type]
        return obj
    except TypeError:
        pass

    if hasattr(obj, "model_dump"):
        return {k: _jsonify(v) for k, v in obj.model_dump().items()}
    if hasattr(obj, "__dict__") and obj.__dict__:
        return {k: _jsonify(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    return str(obj)


def _result_is_invalid(res: Any) -> bool:
    """Return True for None / empty results so fail‑over can trigger."""
    if res is None:
        return True
    if isinstance(res, str) and not res.strip():
        return True
    if isinstance(res, (list, tuple, dict, set)) and len(res) == 0:
        return True
    return False


# NOTE: We deliberately shard profiles two-levels deep (first four hex chars of the
# UUID without hyphens) to avoid placing millions of objects in a single
# directory/prefix while still keeping the layout human-navigable.  Example
# UUID "123e4567-e89b-12d3-a456-426614174000" →
#   browser_profiles/12/3e/123e4567-e89b-12d3-a456-426614174000.tar.zst


def _profile_storage_key(agent_uuid: str) -> str:
    """Return hierarchical object key for a browser profile archive.

    The first two directory levels are derived from the first four hexadecimal
    characters of the UUID (hyphens stripped) to distribute objects evenly.
    This scales to millions of agents without overloading a single directory on
    most object stores (e.g. S3, GCS) while remaining human friendly.
    """

    clean_uuid = agent_uuid.replace("-", "")  # strip hyphens for even sharding
    return f"browser_profiles/{clean_uuid[:2]}/{clean_uuid[2:4]}/{agent_uuid}.tar.zst"

# --------------------------------------------------------------------------- #
#  Secure tar extraction helper
# --------------------------------------------------------------------------- #

def _safe_extract_tar_member(tar_obj: tarfile.TarFile, member: tarfile.TarInfo, dest_dir: str) -> None:
    """Extract a single TarInfo member safely to *dest_dir*.

    Raises an exception if the member's final path would escape *dest_dir* to
    mitigate path-traversal attacks (e.g. entries containing "../" or absolute
    paths).  Streaming extraction (mode="r|") requires member-by-member checks
    instead of `extractall`.
    """

    # Resolve the target path and ensure it stays within dest_dir
    target_path = os.path.join(dest_dir, member.name)
    abs_dest = os.path.realpath(dest_dir)
    abs_target = os.path.realpath(target_path)

    # Allow extraction if target is exactly the dest dir (e.g., member.name is ".")
    # or if target is within the dest dir
    if not (abs_target == abs_dest or abs_target.startswith(abs_dest + os.sep)):
        raise Exception(f"Unsafe path detected in tar archive: {member.name}")

    tar_obj.extract(member, path=dest_dir)

# --------------------------------------------------------------------------- #
#  Agent runner
# --------------------------------------------------------------------------- #
async def _run_agent(
    task_input: str,
    llm_api_key: str,
    task_id: str,
    proxy_server=None,
    provider: str = "anthropic",
    controller: Any = None,
    sensitive_data: Optional[dict] = None,
    output_schema: Optional[dict] = None,
    browser_use_agent_id: Optional[str] = None,
    persistent_agent_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """Execute the Browser‑Use agent for a single provider."""
    if baggage:
        baggage.set_baggage("task.id", str(task_id))
    with traced("RUN BUAgent") as agent_span:
        agent_span.set_attribute("task.id", task_id)
        agent_span.set_attribute("provider", provider)

        if browser_use_agent_id:
            agent_span.set_attribute("browser_use_agent.id", browser_use_agent_id)
            agent_span.set_attribute("profile_persistence.enabled", True)
            logger.info("Running browser agent %s with profile persistence for task %s", browser_use_agent_id, task_id)
        else:
            agent_span.set_attribute("profile_persistence.enabled", False)
            logger.info("Running browser agent without profile persistence for task %s", task_id)

        xvfb_manager: Optional[EphemeralXvfb] = None
        browser_session = None
        browser_ctx = None
        llm: Any = None
        playwright = None
        temp_profile_dir = tempfile.mkdtemp(prefix="bu_profile_")

        logger.debug("Created temporary profile directory: %s", temp_profile_dir)

        # --------------------------------------------------------------
        #  Browser profile restore (if applicable)
        # --------------------------------------------------------------
        if browser_use_agent_id:
            with traced("Browser Profile Restore") as restore_span:
                restore_span.set_attribute("browser_use_agent.id", browser_use_agent_id)
                storage_key = _profile_storage_key(browser_use_agent_id)
                restore_span.set_attribute("storage.key", storage_key)
                restore_span.set_attribute("storage.backend", str(type(default_storage).__name__))

                start_time = time.time()
                try:
                    # Log storage backend configuration for debugging
                    try:
                        storage_backend_type = getattr(settings, 'STORAGE_BACKEND_TYPE', 'LOCAL')
                        restore_span.set_attribute("config.storage_backend_type", storage_backend_type)
                        logger.debug("Using storage backend: %s", storage_backend_type)
                    except Exception:
                        pass

                    if default_storage.exists(storage_key):
                        logger.info(
                            "Found existing browser profile for agent %s, starting restore from %s",
                            browser_use_agent_id,
                            storage_key
                        )
                        restore_span.set_attribute("profile.exists", True)

                        with default_storage.open(storage_key, "rb") as src:
                            # Get file size for logging
                            try:
                                file_size = src.size
                                restore_span.set_attribute("compressed_file.size_bytes", file_size)
                                logger.info("Compressed profile size: %d bytes", file_size)
                            except Exception:
                                logger.debug("Could not determine compressed file size")

                            decompress_start = time.time()
                            dctx = zstd.ZstdDecompressor()
                            with dctx.stream_reader(src) as reader:
                                with tarfile.open(fileobj=reader, mode="r|") as tar:
                                    # Count extracted files for logging
                                    extracted_count = 0
                                    for member in tar:
                                        _safe_extract_tar_member(tar, member, temp_profile_dir)
                                        extracted_count += 1

                            decompress_time = time.time() - decompress_start
                            restore_span.set_attribute("decompression.duration_seconds", decompress_time)
                            restore_span.set_attribute("extracted_files.count", extracted_count)

                        # Check extracted directory size
                        try:
                            total_size = sum(
                                os.path.getsize(os.path.join(dirpath, filename))
                                for dirpath, dirnames, filenames in os.walk(temp_profile_dir)
                                for filename in filenames
                            )
                            restore_span.set_attribute("extracted_profile.size_bytes", total_size)
                            logger.info(
                                "Browser profile restored successfully for agent %s: %d files, %d bytes extracted in %.2fs",
                                browser_use_agent_id,
                                extracted_count,
                                total_size,
                                decompress_time
                            )
                        except Exception:
                            logger.info(
                                "Browser profile restored successfully for agent %s: %d files extracted in %.2fs",
                                browser_use_agent_id,
                                extracted_count,
                                decompress_time
                            )

                        restore_span.set_attribute("restore.success", True)
                    else:
                        logger.info("No existing browser profile found for agent %s, starting fresh", browser_use_agent_id)
                        restore_span.set_attribute("profile.exists", False)
                        restore_span.set_attribute("restore.success", True)

                    total_time = time.time() - start_time
                    restore_span.set_attribute("restore.total_duration_seconds", total_time)

                except Exception as e:  # noqa: BLE001
                    error_time = time.time() - start_time
                    restore_span.set_attribute("restore.success", False)
                    restore_span.set_attribute("restore.error_duration_seconds", error_time)
                    restore_span.set_attribute("error.message", str(e))
                    logger.exception(
                        "Failed to restore browser profile for agent %s after %.2fs: %s",
                        browser_use_agent_id,
                        error_time,
                        str(e)
                    )
        else:
            logger.debug("Browser profile persistence disabled for task %s (no browser_use_agent_id)", task_id)

        try:
            if should_use_ephemeral_xvfb() and not os.environ.get("DISPLAY"):
                logger.info("Launching Ephemeral Xvfb for task %s", task_id)
                xvfb_manager = EphemeralXvfb()
                xvfb_manager.start()

            proxy_settings = None
            if proxy_server:
                proxy_settings = ProxySettings(
                    server=f"{proxy_server.proxy_type.lower()}://{proxy_server.host}:{proxy_server.port}"
                )
                if proxy_server.username:
                    proxy_settings.username = proxy_server.username
                if proxy_server.password:
                    proxy_settings.password = proxy_server.password
                logger.info(
                    "Starting stealth browser with proxy: %s:%s",
                    proxy_server.host,
                    proxy_server.port,
                )
            else:
                logger.info("Starting stealth browser without proxy")

            profile = BrowserProfile(
                stealth=True,
                headless=settings.BROWSER_HEADLESS,
                user_data_dir=temp_profile_dir,
                timeout=30_000,
                no_viewport=True,
                accept_downloads=False,
                auto_download_pdfs=False,
                proxy=proxy_settings
            )

            browser_session = BrowserSession(
                browser_profile=profile,
            )
            await browser_session.start()

            llm_params = {"api_key": llm_api_key, "temperature": 0}

            if provider == "google":
                llm_params["model"] = DEFAULT_GOOGLE_MODEL
                llm = ChatGoogle(**llm_params)
            elif provider == "openrouter":
                llm_params["model"] = "z-ai/glm-4.5"
                llm_params["base_url"] = "https://openrouter.ai/api/v1"
                llm = ChatOpenAI(**llm_params)
            elif provider == "fireworks":
                llm_params["model"] = "accounts/fireworks/models/qwen3-235b-a22b-instruct-2507"
                llm_params["base_url"] = "https://api.fireworks.ai/inference/v1"
                llm = ChatOpenAI(**llm_params)
            elif provider == "anthropic":
                llm_params["model"] = "claude-sonnet-4-20250514"
                llm = ChatAnthropic(**llm_params)
            else:  # openai
                llm_params["model"] = "gpt-4.1"
                llm_params["temperature"] = 0
                llm = ChatOpenAI(**llm_params)

            # Get current time with timezone for context
            current_time = timezone.now()
            current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S %Z")

            base_prompt = (
                f"<task>{task_input}</task>\n\n"
                f"CURRENT TIME: {current_time_str}\n"
                "NOTE: All times before this current time are in the past, and all times after are in the future. "
                "Information in my training data may be outdated - always prioritize current, real-time information when available.\n\n"
                
                "IF YOU GET ERR_PROXY_CONNECTION_FAILED, "
                "JUST WAIT A FEW SECONDS AND IT WILL GO AWAY. IF IT DOESN'T, TRY AGAIN A "
                "FEW TIMES. LITERALLY JUST SKIP YOUR STEP AND REFRESH THE PAGE. YOU DONT "
                "NEED TO NAVIGATE BACK OR REFRESH UNLESS IT PERSISTS. "

                "IF YOU NEED TO SEARCH THE WEB, USE THE 'search_web' TOOL, RATHER THAN A SEARCH ENGINE. "
                "INFORMATION RETURNED FROM 'search_web' CAN BE OUTDATED, SO WHEN IN DOUBT LOOK UP MORE RESENT INFORMATION DIRECTLY ON RELEVANT WEBSITES. "
                
                "PREFER PRIMARY SOURCES --You can use 'search_web' to find primary sources, then access up-to-date information directly from the source. "
                
                "IF YOU GET A CAPTCHA CHALLENGE THAT YOU CANNOT PASS IN TWO ATTEMPTS AND THERE "
                "IS AN ALTERNATIVE WAY TO GET THE JOB DONE, JUST DO THAT INSTEAD OF FIGHTING "
                "THE CAPTCHA FOR MANY STEPS. "

                "If your task requries you to be logged in and you do not have the credentials/secrets available, simply exit early and return that as your response. "

                "BE VERY CAREFUL TO PRECISELY RETRIEVE URLS WHEN ASKED --DO NOT HALLUCINATE URLS!!! "
                "WHEN IN DOUBT, TAKE TIME TO LOOK UP COMPLETE, ACCURATE URLs FOR ALL SOURCES. "
            )

            if output_schema:
                schema_json = json.dumps(output_schema, indent=2)
                structured_prompt = (
                    "When you have completed the research, YOU MUST call the `done` action. "
                    "The inputs for this action will be generated dynamically to match the required output format. "
                    "Provide the information you have gathered as arguments to the `done` action. "
                    "Do NOT output the final answer as a normal message outside of the `done` action. "
                    "NEST/EMBED YOUR JSON IN THE done ACTION. "
                    "YOU MUST INCLUDE ALL REQUIRED FIELDS IN THE data FIELD OF THE DONE ACTION ACCORDING TO THE SHEMA!! "
                )
                task_prompt = base_prompt + structured_prompt
            else:
                unstructured_prompt = (
                    "When you have completed the research, YOU MUST call the done(success=True) action with YOUR FULL ANSWER "
                    "INCLUDING LINKS AND ALL DETAILS in the text field (and include any file names in files_to_display "
                    "if you wrote results to a file). Do NOT output the final answer as a normal message outside of the done function."
                )
                task_prompt = base_prompt + unstructured_prompt

            agent_kwargs = {
                "task": task_prompt,
                "llm": llm,
                "browser": browser_session,
                "enable_memory": False,
            }

            if controller:
                agent_kwargs["controller"] = controller
            if sensitive_data:
                agent_kwargs["sensitive_data"] = sensitive_data

                # Count total secrets across all domains
                total_secrets = 0
                domain_summary = {}
                for domain, secrets in sensitive_data.items():
                    domain_secrets_count = len(secrets) if isinstance(secrets, dict) else 0
                    total_secrets += domain_secrets_count
                    domain_summary[domain] = list(secrets.keys()) if isinstance(secrets, dict) else []

                logger.info(
                    "Running task %s with %d secrets across %d domains",
                    task_id,
                    total_secrets,
                    len(sensitive_data),
                    extra={"task_id": task_id, "domain_secrets": domain_summary},
                )

            agent = BUAgent(**agent_kwargs)
            history = await agent.run()

            # Extract usage details (if available) and annotate tracing
            token_usage = None
            try:
                token_usage = {
                    "model": llm_params.get("model"),
                    "provider": provider
                }

                if getattr(history, "usage", None):
                    token_usage.update({
                        "prompt_tokens": getattr(history.usage, "total_prompt_tokens", None),
                        "completion_tokens": getattr(history.usage, "total_completion_tokens", None),
                        "total_tokens": getattr(history.usage, "total_tokens", None),
                        "cached_tokens": getattr(history.usage, "total_prompt_cached_tokens", None),
                    })
                    # Add to span for observability
                    agent_span.set_attributes({
                        "llm.model": token_usage["model"],
                        "llm.provider": token_usage["provider"],
                        "llm.usage.prompt_tokens": token_usage["prompt_tokens"],
                        "llm.usage.completion_tokens": token_usage["completion_tokens"],
                        "llm.usage.total_tokens": token_usage["total_tokens"],
                        "llm.usage.cached_tokens": token_usage["cached_tokens"],
                    })
            except Exception as e:
                logger.warning("Usage logging failed with exception", exc_info=e)

            return history.final_result(), token_usage

        finally:
            await _safe_aclose(browser_session, "stop")
            await _safe_aclose(browser_session, "kill")

            # --------------------------------------------------------------
            #  Browser profile save (if applicable)
            # --------------------------------------------------------------
            if browser_use_agent_id:
                with traced("Browser Profile Save") as save_span:
                    save_span.set_attribute("browser_use_agent.id", browser_use_agent_id)
                    storage_key = _profile_storage_key(browser_use_agent_id)
                    save_span.set_attribute("storage.key", storage_key)
                    save_span.set_attribute("storage.backend", str(type(default_storage).__name__))

                    start_time = time.time()
                    tmp_tar_path = None
                    tmp_zst_path = None

                    try:
                        # Check source directory size and file count
                        try:
                            # Prune unnecessary cache/temp data before archiving
                            _prune_chrome_profile(temp_profile_dir)
                            save_span.set_attribute("profile.pruned", True)

                            source_size = 0
                            file_count = 0
                            for dirpath, dirnames, filenames in os.walk(temp_profile_dir):
                                for filename in filenames:
                                    filepath = os.path.join(dirpath, filename)
                                    source_size += os.path.getsize(filepath)
                                    file_count += 1

                            save_span.set_attribute("source_profile.size_bytes", source_size)
                            save_span.set_attribute("source_profile.file_count", file_count)
                            logger.info(
                                "Starting browser profile save for agent %s: %d files, %d bytes",
                                browser_use_agent_id,
                                file_count,
                                source_size
                            )
                        except Exception:
                            logger.debug("Could not calculate source directory stats")

                        tmp_tar_path = tempfile.mktemp(suffix=".tar")
                        tmp_zst_path = tmp_tar_path + ".zst"
                        save_span.set_attribute("temp_tar_path", tmp_tar_path)
                        save_span.set_attribute("temp_zst_path", tmp_zst_path)

                        try:
                            # Create tar archive
                            tar_start = time.time()
                            with tarfile.open(tmp_tar_path, "w") as tar:
                                tar.add(temp_profile_dir, arcname=".")

                            tar_time = time.time() - tar_start
                            tar_size = os.path.getsize(tmp_tar_path)
                            save_span.set_attribute("tar.duration_seconds", tar_time)
                            save_span.set_attribute("tar.size_bytes", tar_size)

                            logger.info(
                                "Tar archive created for agent %s: %d bytes in %.2fs",
                                browser_use_agent_id,
                                tar_size,
                                tar_time
                            )

                            # Compress with zstd
                            compress_start = time.time()
                            cctx = zstd.ZstdCompressor(level=3)
                            with open(tmp_tar_path, "rb") as f_in, open(tmp_zst_path, "wb") as f_out:
                                cctx.copy_stream(f_in, f_out)

                            compress_time = time.time() - compress_start
                            compressed_size = os.path.getsize(tmp_zst_path)
                            compression_ratio = compressed_size / tar_size if tar_size > 0 else 0

                            save_span.set_attribute("compression.duration_seconds", compress_time)
                            save_span.set_attribute("compressed.size_bytes", compressed_size)
                            save_span.set_attribute("compression.ratio", compression_ratio)

                            logger.info(
                                "Compression completed for agent %s: %d -> %d bytes (%.1f%% ratio) in %.2fs",
                                browser_use_agent_id,
                                tar_size,
                                compressed_size,
                                compression_ratio * 100,
                                compress_time
                            )

                            # Upload to storage
                            upload_start = time.time()
                            with open(tmp_zst_path, "rb") as f_in:
                                existed = default_storage.exists(storage_key)
                                if existed:
                                    logger.info("Replacing existing profile for agent %s", browser_use_agent_id)
                                    default_storage.delete(storage_key)
                                    save_span.set_attribute("replaced_existing", True)
                                else:
                                    save_span.set_attribute("replaced_existing", False)

                                # Stream upload to storage to avoid loading entire archive in memory
                                default_storage.save(storage_key, File(f_in))

                            upload_time = time.time() - upload_start
                            save_span.set_attribute("upload.duration_seconds", upload_time)

                            logger.info(
                                "Upload completed for agent %s: %d bytes in %.2fs",
                                browser_use_agent_id,
                                compressed_size,
                                upload_time
                            )

                        finally:
                            # Clean up temporary files
                            cleanup_start = time.time()
                            if tmp_tar_path and os.path.exists(tmp_tar_path):
                                os.unlink(tmp_tar_path)
                            if tmp_zst_path and os.path.exists(tmp_zst_path):
                                os.unlink(tmp_zst_path)
                            cleanup_time = time.time() - cleanup_start
                            save_span.set_attribute("cleanup.duration_seconds", cleanup_time)

                        total_time = time.time() - start_time
                        save_span.set_attribute("save.total_duration_seconds", total_time)
                        save_span.set_attribute("save.success", True)

                        logger.info(
                            "Browser profile saved successfully for agent %s: total time %.2fs",
                            browser_use_agent_id,
                            total_time
                        )

                    except Exception as e:  # noqa: BLE001
                        error_time = time.time() - start_time
                        save_span.set_attribute("save.success", False)
                        save_span.set_attribute("save.error_duration_seconds", error_time)
                        save_span.set_attribute("error.message", str(e))

                        # Emergency cleanup in case of error
                        try:
                            if tmp_tar_path and os.path.exists(tmp_tar_path):
                                os.unlink(tmp_tar_path)
                            if tmp_zst_path and os.path.exists(tmp_zst_path):
                                os.unlink(tmp_zst_path)
                        except Exception:
                            pass

                        logger.exception(
                            "Failed to save browser profile for agent %s after %.2fs: %s",
                            browser_use_agent_id,
                            error_time,
                            str(e)
                        )
            else:
                logger.debug("Browser profile persistence disabled for task %s (no browser_use_agent_id)", task_id)

            if llm is not None and getattr(llm, "async_client", None):
                await _safe_aclose(llm.async_client)  # type: ignore[arg-type]
            try:
                _robust_rmtree(temp_profile_dir)
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to remove temp profile dir %s: %s",
                    temp_profile_dir,
                    cleanup_exc,
                )
            if xvfb_manager is not None:
                xvfb_manager.stop()


def _execute_agent_with_failover(
    *,
    task_input: str,
    task_id: str,
    proxy_server=None,
    controller: Any = None,
    sensitive_data: Optional[dict] = None,
    provider_priority: Any = None,
    output_schema: Optional[dict] = None,
    browser_use_agent_id: Optional[str] = None,
    persistent_agent_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """
    Execute the agent with tiered, weighted load-balancing and fail-over.

    * Each entry in ``provider_priority`` is considered a *tier*.
    * Providers inside each tier are selected based on their assigned weight.
      If no weights are provided (legacy format), they are treated as equal.
    * We only advance to the next tier if **every** provider in the current
      tier either fails or lacks a configured API key.
    """
    provider_priority = provider_priority or PROVIDER_PRIORITY

    # Normalize legacy flat list or unweighted configs into a weighted structure.
    if provider_priority:
        is_legacy_flat_list = not any(isinstance(item, (list, tuple)) for item in provider_priority)
        if is_legacy_flat_list:
            provider_priority = [provider_priority]  # type: ignore[list-item]

        is_new_format = (
            provider_priority
            and isinstance(provider_priority[0], (list, tuple))
            and provider_priority[0]
            and isinstance(provider_priority[0][0], (list, tuple))
        )

        if not is_new_format:
            new_priority = []
            for tier in provider_priority:
                new_tier = [(provider, 1.0) for provider in tier]  # type: ignore[union-attr]
                new_priority.append(new_tier)
            provider_priority = new_priority

    last_exc: Optional[Exception] = None

    for tier_idx, tier in enumerate(provider_priority, start=1):
        # Build list of usable providers in this tier.
        tier_providers_with_weights: List[Tuple[str, float]] = []
        for provider_config in tier:
            provider, weight = provider_config
            env_var = PROVIDER_CONFIG.get(provider, {}).get("env_var")
            if not env_var:
                logger.warning("Unknown provider %s; skipping.", provider)
                continue
            if not os.getenv(env_var):
                logger.info(
                    "Skipping provider %s for task %s — missing env %s",
                    provider,
                    task_id,
                    env_var,
                )
                continue
            tier_providers_with_weights.append((provider, weight))

        if not tier_providers_with_weights:
            logger.info(
                "No usable providers in tier %d for task %s; moving to next tier.",
                tier_idx,
                task_id,
            )
            continue

        # Create a weighted-random order of providers to attempt for this tier.
        providers_to_attempt = []
        remaining_providers = tier_providers_with_weights.copy()
        while remaining_providers:
            providers = [p[0] for p in remaining_providers]
            weights = [p[1] for p in remaining_providers]
            selected_provider = random.choices(providers, weights=weights, k=1)[0]
            providers_to_attempt.append(selected_provider)
            remaining_providers = [p for p in remaining_providers if p[0] != selected_provider]

        for provider in providers_to_attempt:
            env_var = PROVIDER_CONFIG[provider]["env_var"]
            llm_api_key = os.getenv(env_var)

            logger.info(
                "Attempting provider %s (tier %d) for task %s",
                provider,
                tier_idx,
                task_id,
            )
            try:
                result, token_usage = asyncio.run(
                    _run_agent(
                        task_input=task_input,
                        llm_api_key=llm_api_key,
                        task_id=task_id,
                        proxy_server=proxy_server,
                        provider=provider,
                        controller=controller,
                        sensitive_data=sensitive_data,
                        output_schema=output_schema,
                        browser_use_agent_id=browser_use_agent_id,
                        persistent_agent_id=persistent_agent_id,
                    )
                )

                if _result_is_invalid(result):
                    raise RuntimeError("Provider returned empty or invalid result")

                logger.info(
                    "Provider %s succeeded for task %s (tier %d)",
                    provider,
                    task_id,
                    tier_idx,
                )
                return result, token_usage

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception(
                    "Provider %s failed for task %s (tier %d); trying next provider in tier.",
                    provider,
                    task_id,
                    tier_idx,
                )

        logger.info(
            "All providers in tier %d failed for task %s; falling back to next tier.",
            tier_idx,
            task_id,
        )

    if last_exc:
        raise last_exc
    raise RuntimeError("No provider with a valid API key available")


# --------------------------------------------------------------------------- #
#  Celery entry‑point
# --------------------------------------------------------------------------- #
def _process_browser_use_task_core(
    browser_use_agent_task_id: str,
    override_proxy_id: str = None,
    persistent_agent_id: str = None,
    *,
    budget_id: str | None = None,
    branch_id: str | None = None,
    depth: int | None = None,
) -> None:
    """Core task processing logic that can be called directly or from Celery."""
    if baggage:
        baggage.set_baggage("task.id", str(browser_use_agent_task_id))

    with traced("PROCESS Browser Use Task Core") as span:
        span.set_attribute('task.id', str(browser_use_agent_task_id))
        try:
            task_obj = BrowserUseAgentTask.objects.get(id=browser_use_agent_task_id)

            if baggage:
                if task_obj.user and task_obj.user.id:
                    baggage.set_baggage("user.id", str(task_obj.user.id))
                    span.set_attribute('user.id', str(task_obj.user.id))
                if task_obj.agent and task_obj.agent.id:
                    baggage.set_baggage("agent.id", str(task_obj.agent.id))
                    span.set_attribute('agent.id', str(task_obj.agent.id))

        except BrowserUseAgentTask.DoesNotExist:
            logger.error("BrowserUseAgentTask %s not found", browser_use_agent_task_id)
            return

        task_obj.status = BrowserUseAgentTask.StatusChoices.IN_PROGRESS
        task_obj.updated_at = timezone.now()
        task_obj.save(update_fields=["status", "updated_at"])

        span.set_attribute('task.updated_at', str(task_obj.updated_at))

        if not LIBS_AVAILABLE:
            err = f"Import failed: {IMPORT_ERROR}"
            task_obj.status = BrowserUseAgentTask.StatusChoices.FAILED
            task_obj.error_message = err
            task_obj.save(update_fields=["status", "error_message"])
            logger.error(err)
            return

        sensitive_data = None
        if task_obj.encrypted_secrets:
            span.set_attribute('task.has_encrypted_secrets', True) # Never include secret keys in attrs, just flag if they exist
            try:
                from ..encryption import SecretsEncryption

                sensitive_data = SecretsEncryption.decrypt_secrets(
                    task_obj.encrypted_secrets
                )
                logger.info(
                    "Decrypted %d secrets for task %s",
                    len(sensitive_data),
                    task_obj.id,
                    extra={"task_id": str(task_obj.id), "secret_keys": task_obj.secret_keys},
                )
            except Exception:
                err = "Failed to decrypt task secrets"
                logger.exception(err)
                task_obj.status = BrowserUseAgentTask.StatusChoices.FAILED
                task_obj.error_message = err
                task_obj.save(update_fields=["status", "error_message"])
                return

        try:
            override_proxy = None
            span.set_attribute('task.uses_override_proxy', override_proxy_id is not None)
            if override_proxy_id:
                span.set_attribute('task.override_proxy_id', override_proxy_id)
                try:
                    override_proxy = ProxyServer.objects.get(id=override_proxy_id)
                except ProxyServer.DoesNotExist:
                    logger.warning(
                        "Override proxy %s not found; using normal selection",
                        override_proxy_id,
                    )

            proxy_server = select_proxy_for_task(task_obj, override_proxy=override_proxy)

            # Get the browser use agent ID for profile persistence
            browser_use_agent_id = None
            if task_obj.agent:
                browser_use_agent_id = str(task_obj.agent.id)
                span.set_attribute("browser_use_agent.id", browser_use_agent_id)
                logger.info("Browser profile persistence enabled for task %s with agent %s", task_obj.id, browser_use_agent_id)
            else:
                logger.info("Browser profile persistence disabled for task %s (no associated agent)", task_obj.id)
                span.set_attribute("browser_use_agent.missing", True)

            controller = None
            if task_obj.output_schema:
                span.set_attribute('task.has_output_schema', True)
                span.set_attribute('task.output_schema', str(task_obj.output_schema))
                try:
                    schema_str = json.dumps(task_obj.output_schema, sort_keys=True)
                    schema_hash = hashlib.sha256(schema_str.encode()).hexdigest()[:8]
                    model_name = f"DynamicModel_{schema_hash}"
                    logger.info("Creating dynamic output model for task %s", task_obj.id)
                    model_class = create_model(task_obj.output_schema)
                    controller = Controller(output_model=model_class)
                except Exception as exc:
                    err = f"Failed to create output model: {str(exc)}"
                    logger.exception(err)
                    task_obj.status = BrowserUseAgentTask.StatusChoices.FAILED
                    task_obj.error_message = err
                    task_obj.save(update_fields=["status", "error_message"])
                    return
            else:
                controller = Controller()

            # Register custom actions
            try:
                from ..agent.browser_actions import (
                    register_web_search_action,
                )
                actions = ['web_search']
                register_web_search_action(controller)
                #TODO: Add upload action registration here

                logger.debug(f"Registered custom action(s) {",".join(actions)} for task %s", task_obj.id)
            except Exception as exc:
                logger.warning("Failed to register custom actions for task %s: %s", task_obj.id, str(exc))

            with traced("Execute Agent") as agent_span:
                raw_result, token_usage = _execute_agent_with_failover(
                    task_input=task_obj.prompt,
                    task_id=str(task_obj.id),
                    proxy_server=proxy_server,
                    controller=controller,
                    sensitive_data=sensitive_data,
                    provider_priority=PROVIDER_PRIORITY,
                    output_schema=task_obj.output_schema,
                    browser_use_agent_id=browser_use_agent_id,
                    persistent_agent_id=persistent_agent_id
                )

                safe_result = _jsonify(raw_result)
                if isinstance(raw_result, str) and task_obj.output_schema:
                    try:
                        parsed_json = json.loads(raw_result)
                        if isinstance(parsed_json, dict):
                            safe_result = parsed_json
                    except json.JSONDecodeError:
                        pass

                # Ensure a fresh/healthy DB connection before post‑execution ORM writes
                close_old_connections()
                try:
                    BrowserUseAgentTaskStep.objects.create(
                        task=task_obj,
                        step_number=1,
                        description="Task execution completed.",
                        is_result=True,
                        result_value=safe_result,
                    )
                except OperationalError:
                    # Retry once using idempotent upsert semantics
                    close_old_connections()
                    BrowserUseAgentTaskStep.objects.update_or_create(
                        task=task_obj,
                        step_number=1,
                        defaults={
                            "description": "Task execution completed.",
                            "is_result": True,
                            "result_value": safe_result,
                        },
                    )

                # Record LLM usage and metadata if available
                if token_usage:
                    try:
                        task_obj.prompt_tokens = token_usage.get("prompt_tokens")
                        task_obj.completion_tokens = token_usage.get("completion_tokens")
                        task_obj.total_tokens = token_usage.get("total_tokens")
                        task_obj.cached_tokens = token_usage.get("cached_tokens")
                        task_obj.llm_model = token_usage.get("model")
                        task_obj.llm_provider = token_usage.get("provider")
                    except Exception:
                        logger.warning("Failed to assign usage metadata to task %s", task_obj.id, exc_info=True)

                task_obj.status = BrowserUseAgentTask.StatusChoices.COMPLETED
                task_obj.error_message = None

                agent_span.set_attribute('task.id', str(task_obj.id))
                agent_span.set_attribute('task.status', str(BrowserUseAgentTask.StatusChoices.COMPLETED))

            # (no scheduling here; we decrement in finally and schedule once below)

        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            logger.exception("Task %s failed: %s", task_obj.id, error_message)
            span.set_attribute('task.error_message', error_message)
            span.add_event('task_failed', {
                'error.message': error_message,
                'task.id': str(task_obj.id),
            })
            task_obj.status = BrowserUseAgentTask.StatusChoices.FAILED
            task_obj.error_message = error_message

            # Ensure a fresh/healthy DB connection before writing failure step
            close_old_connections()
            try:
                BrowserUseAgentTaskStep.objects.create(
                    task=task_obj,
                    step_number=1,
                    description=f"Task failed: {error_message}",
                    is_result=False,
                )
            except OperationalError:
                # Retry once using idempotent upsert semantics
                close_old_connections()
                BrowserUseAgentTaskStep.objects.update_or_create(
                    task=task_obj,
                    step_number=1,
                    defaults={
                        "description": f"Task failed: {error_message}",
                        "is_result": False,
                        "result_value": None,
                    },
                )

        finally:
            # Decrement outstanding-children counter regardless of success/failure
            if branch_id and task_obj.agent and hasattr(task_obj.agent, 'persistent_agent'):
                try:
                    AgentBudgetManager.bump_branch_depth(
                        agent_id=str(task_obj.agent.persistent_agent.id),
                        branch_id=str(branch_id),
                        delta=-1,
                    )
                    logger.info(
                        "Decremented outstanding children for agent %s branch %s after task %s",
                        task_obj.agent.persistent_agent.id,
                        branch_id,
                        task_obj.id,
                    )
                except Exception as e:
                    logger.warning("Failed to decrement outstanding children for branch %s: %s", branch_id, e)

            # Refresh/validate DB connection before final status save
            close_old_connections()
            task_obj.updated_at = timezone.now()
            try:
                task_obj.save(update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cached_tokens",
                    "llm_model",
                    "llm_provider",
                ])
            except OperationalError:
                close_old_connections()
                task_obj.save(update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cached_tokens",
                    "llm_model",
                    "llm_provider",
                ])

            # Trigger agent event processing if this task belongs to a persistent agent
            if task_obj.agent and hasattr(task_obj.agent, 'persistent_agent'):
                try:
                    from api.agent.tasks.process_events import process_agent_events_task
                    # Calculate parent depth by subtracting 1 from current depth
                    # Since we spawned at depth+1, returning to parent means depth-1
                    parent_depth = max((depth or 1) - 1, 0)
                    # Skip follow-up if the active cycle no longer matches the context budget
                    status = AgentBudgetManager.get_cycle_status(agent_id=str(task_obj.agent.persistent_agent.id))
                    active_id = AgentBudgetManager.get_active_budget_id(agent_id=str(task_obj.agent.persistent_agent.id))
                    if status != "active" or (budget_id is not None and active_id != budget_id):
                        logger.info(
                            "Skipping follow-up; cycle status=%s active_id=%s ctx_id=%s",
                            status,
                            active_id,
                            budget_id,
                        )
                        return
                    process_agent_events_task.delay(
                        str(task_obj.agent.persistent_agent.id),
                        budget_id=budget_id,
                        branch_id=branch_id,
                        depth=parent_depth,
                    )
                    logger.info("Triggered agent event processing for persistent agent %s after task %s completion",
                               task_obj.agent.persistent_agent.id, task_obj.id)
                except Exception as e:
                    logger.error("Failed to trigger agent event processing for task %s: %s", task_obj.id, e)


@shared_task(bind=True, name="gobii_platform.api.tasks.process_browser_use_task")
def process_browser_use_task(
    self,
    browser_use_agent_task_id: str,
    override_proxy_id: str = None,
    persistent_agent_id: str = None,
    budget_id: str | None = None,
    branch_id: str | None = None,
    depth: int | None = None,
) -> None:
    """Celery task wrapper for browser‑use task processing."""
    # Get the Celery-provided span and rename it for clarity
    span = trace.get_current_span()
    span.update_name("PROCESS Browser Use Task")
    span.set_attribute("task.id", str(browser_use_agent_task_id))

    return _process_browser_use_task_core(
        browser_use_agent_task_id,
        override_proxy_id,
        persistent_agent_id,
        budget_id=budget_id,
        branch_id=branch_id,
        depth=depth,
    )
