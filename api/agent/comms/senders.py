"""Abstractions for outbound communication providers."""

from __future__ import annotations

from typing import Optional


class SmsSender:
    """Base class for sending SMS messages via a provider."""

    def send(self, to: str, body: str, from_: Optional[str] = None, **kwargs) -> None:  # pragma: no cover - interface
        """Send an SMS message to ``to`` with ``body``."""
        raise NotImplementedError


class EmailSender:
    """Base class for sending email messages via a provider."""

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_: Optional[str] = None,
        **kwargs,
    ) -> None:  # pragma: no cover - interface
        """Send an email message."""
        raise NotImplementedError
