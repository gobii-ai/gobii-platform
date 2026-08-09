class CompactionSummaryError(RuntimeError):
    """Raised when history cannot be summarized safely enough to advance a snapshot."""


__all__ = ["CompactionSummaryError"]
