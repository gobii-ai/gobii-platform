import os
import subprocess
import time
import signal
import atexit
import fcntl
import errno  # Needed for robust PID liveness detection
from contextlib import AbstractContextManager, contextmanager

__all__ = [
    "EphemeralXvfb",
    "should_use_ephemeral_xvfb",
    "xvfb_lock",
]


DISPLAY_RANGE_START = 1000
DISPLAY_RANGE_END = 1100


@contextmanager
def xvfb_lock(path="/tmp/.xvfb_lock"):
    """Cross-process advisory file lock for serializing Xvfb startup.
    
    Uses fcntl.flock() which works across processes, unlike threading locks.
    This prevents race conditions when multiple Celery workers try to find
    available displays simultaneously.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # blocks until lock acquired
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _pid_alive(pid: int) -> bool:
    """Return True iff *pid* exists (or we lack permission) – basically 'alive'."""
    try:
        os.kill(pid, 0)
    except OSError as e:
        # EPERM → process exists but belongs to another user / container
        return e.errno == errno.EPERM
    return True


def _is_display_in_use(display_num: int) -> bool:
    """Return True only if the Xvfb that created the lock is still running.

    Reads the PID stored in the *.X??-lock* file and verifies that the process
    is alive.  Stale lock files are removed automatically so they don’t block
    future display allocation.
    """
    lock_path = f"/tmp/.X{display_num}-lock"

    # Fast-path: file doesn't exist
    if not os.path.exists(lock_path):
        return False

    # Attempt to read PID from lock file
    try:
        with open(lock_path) as f:
            pid_str = f.read().strip()
            pid = int(pid_str) if pid_str else 0
    except Exception:
        # Corrupt or unreadable → assume stale and remove
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
        return False

    # If the PID looks alive, display is in use.
    if pid and _pid_alive(pid):
        return True

    # Otherwise treat as stale and clean it up.
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass
    return False


def _find_available_display() -> int:
    """Return an available display number in the configured range."""
    for display_num in range(DISPLAY_RANGE_START, DISPLAY_RANGE_END):
        if not _is_display_in_use(display_num):
            return display_num
    raise RuntimeError("No available Xvfb display numbers in configured range")


class EphemeralXvfb(AbstractContextManager):
    """Context manager that starts a dedicated Xvfb server for the lifetime of the context.

    Designed for per-task/browser use automation where *headless: False* is set
    but there is no system display (e.g. Kubernetes workers).
    """

    def __init__(self, width: int = 1920, height: int = 1080, depth: int = 24):
        self.width = width
        self.height = height
        self.depth = depth
        self.display_num: int | None = None
        self._proc: subprocess.Popen | None = None
        self._old_display: str | None = None

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #
    def start(self) -> None:  # noqa: D401  # Simple verb
        """Start Xvfb and update the DISPLAY env var."""
        if self._proc is not None:
            # Already started – no-op
            return

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                # Use cross-process file lock to serialize display allocation
                with xvfb_lock():
                    self.display_num = _find_available_display()
                    cmd = [
                        "Xvfb",
                        f":{self.display_num}",
                        "-screen",
                        "0",
                        f"{self.width}x{self.height}x{self.depth}",
                        "-dpi",
                        "96",
                        "-ac",  # Disable access control
                        "-nolisten",
                        "tcp",
                        "+extension",
                        "GLX",
                        "+extension",
                        "RANDR",
                    ]
                    # Start Xvfb detached from any controlling terminal
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setpgrp,  # Start new process group for reliable kill
                    )

                    # Basic health check: wait a short time and ensure the lock file exists
                    lock_path = f"/tmp/.X{self.display_num}-lock"
                    timeout_sec = 5
                    start_time = time.time()

                    # Poll for both process liveness and X lock file creation.
                    # We must check the process *first* so that a process which exits
                    # immediately after spawning (before creating the lock file) is
                    # treated as a startup failure. This ordering is important for the
                    # test suite and real-world robustness.
                    while time.time() - start_time < timeout_sec:
                        # 1. If the Xvfb process has already exited, treat it as a
                        #    startup failure regardless of the lock file state.
                        if self._proc.poll() is not None:
                            raise RuntimeError("Xvfb failed to start; process exited early")

                        # 2. Check if the X lock file has been created, indicating the
                        #    display is ready for use.
                        if os.path.exists(lock_path):
                            break

                        time.sleep(0.1)
                    else:
                        self.stop()
                        raise RuntimeError("Timed out waiting for Xvfb to start")

                # Only after Xvfb is confirmed running do we release the lock and set DISPLAY
                # Swap DISPLAY
                self._old_display = os.environ.get("DISPLAY")
                os.environ["DISPLAY"] = f":{self.display_num}"

                # Register finaliser in case of hard crashes
                atexit.register(self.stop)
                
                # Success - break out of retry loop
                break
                
            except Exception as exc:
                # Clean up on failure
                if self._proc is not None:
                    try:
                        pid = getattr(self._proc, "pid", None)
                        # Only attempt to terminate a real child process. Guard against mocks/invalid PIDs
                        if isinstance(pid, int) and pid > 1:
                            os.killpg(os.getpgid(pid), signal.SIGTERM)
                            self._proc.wait(timeout=2)
                    except Exception:
                        pass
                    self._proc = None
                    self.display_num = None
                
                # If this was the last attempt, re-raise the exception
                if attempt == max_attempts - 1:
                    raise RuntimeError(f"Failed to start Xvfb after {max_attempts} attempts: {exc}") from exc
                
                # Wait a bit before retrying
                time.sleep(0.5)

    def stop(self) -> None:  # noqa: D401
        """Terminate Xvfb and restore DISPLAY."""
        if self._proc is None:
            return

        # Store display_num for cleanup before setting to None
        display_to_cleanup = self.display_num

        try:
        # Terminate gracefully (only if we have a valid child PID)
        pid = getattr(self._proc, "pid", None)
        if isinstance(pid, int) and pid > 1:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if isinstance(pid, int) and pid > 1:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                self._proc.wait(timeout=2)
        except Exception:
            # Ignore all cleanup errors
            pass

        # Restore DISPLAY
        if self._old_display is not None:
            os.environ["DISPLAY"] = self._old_display
        else:
            os.environ.pop("DISPLAY", None)

        # Remove lock file if lingering
        if display_to_cleanup is not None:
            try:
                os.remove(f"/tmp/.X{display_to_cleanup}-lock")
            except FileNotFoundError:
                pass

        self._proc = None
        self.display_num = None

    # ------------------------------------------------------------------ #
    #  Context manager protocol                                          #
    # ------------------------------------------------------------------ #
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False  # Do not suppress exceptions


# ---------------------------------------------------------------------- #
#  Convenience helper
# ---------------------------------------------------------------------- #

def should_use_ephemeral_xvfb() -> bool:
    """Return True if USE_EPHEMERAL_XVFB env var is truthy."""
    return os.getenv("USE_EPHEMERAL_XVFB", "false").lower() in {"1", "true", "yes"} 
