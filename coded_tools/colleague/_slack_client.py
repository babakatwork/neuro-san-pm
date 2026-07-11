"""Minimal Slack Web API client with bounded requests and sanitized errors."""

from __future__ import annotations

import os
from typing import Any

import requests


class SlackApiError(RuntimeError):
    """A sanitized Slack API failure."""

    def __init__(self, message: str, *, code: str = "unknown"):
        super().__init__(message)
        self.code = code


class SlackApiClient:
    """Call only explicitly selected Slack Web API methods."""

    def __init__(self, token: str, session: requests.Session | None = None):
        self.token = token
        self.session = session or requests.Session()
        try:
            timeout = float(os.getenv("SLACK_HTTP_TIMEOUT_SECONDS", "10"))
        except ValueError as exc:
            raise SlackApiError("SLACK_HTTP_TIMEOUT_SECONDS must be numeric", code="invalid_timeout") from exc
        if timeout <= 0:
            raise SlackApiError("SLACK_HTTP_TIMEOUT_SECONDS must be positive", code="invalid_timeout")
        self.timeout = min(30.0, max(1.0, timeout))

    def call(self, method: str, *, http_method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://slack.com/api/{method}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            if http_method == "GET":
                response = self.session.get(url, headers=headers, params=payload, timeout=self.timeout)
            else:
                headers["Content-Type"] = "application/json; charset=utf-8"
                response = self.session.post(url, headers=headers, json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SlackApiError("Slack API request failed", code="request_failed") from exc
        if not isinstance(body, dict) or not body.get("ok"):
            code = body.get("error", "unknown_error") if isinstance(body, dict) else "invalid_response"
            raise SlackApiError(f"Slack API rejected the request: {code}", code=str(code))
        return body
