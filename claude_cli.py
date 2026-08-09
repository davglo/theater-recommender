"""Claude access via the Claude Code CLI in headless mode (-p).

Runs on the Claude subscription — no ANTHROPIC_API_KEY, no metered API costs.
The desktop app bundles the CLI under a versioned path; resolve the newest one
at call time (survives app updates), with a CLAUDE_CLI env override and a
PATH fallback.
"""
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import config

logger = logging.getLogger(__name__)

CALL_RETRIES = 3  # total attempts = 1 + CALL_RETRIES
RETRY_BACKOFF_SECONDS = (5, 15, 30)  # one entry per retry (exponential)

BUNDLE_BASE = Path.home() / "Library/Application Support/Claude/claude-code"
BUNDLE_SUFFIX = "claude.app/Contents/MacOS/claude"


class ClaudeCLIError(Exception):
    """Raised when the claude CLI is missing or a call fails."""


def _version_key(d: Path) -> List[int]:
    parts = d.name.split(".")
    return [int(p) for p in parts if p.isdigit()]


def resolve_cli() -> str:
    override = os.environ.get("CLAUDE_CLI", "").strip()
    if override:
        return override
    if BUNDLE_BASE.is_dir():
        versions = sorted(
            (d for d in BUNDLE_BASE.iterdir() if (d / BUNDLE_SUFFIX).is_file()),
            key=_version_key,
        )
        if versions:
            return str(versions[-1] / BUNDLE_SUFFIX)
    return "claude"  # hope it's on PATH


def _call_once(system_prompt: str, user_prompt: str, timeout: int) -> str:
    cli = resolve_cli()
    prompt = f"{system_prompt}\n\n{user_prompt}"
    try:
        proc = subprocess.run(
            [cli, "-p", "--model", config.CLAUDE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(config.BASE_DIR),
        )
    except FileNotFoundError:
        raise ClaudeCLIError(f"claude CLI not found at {cli!r}")
    except subprocess.TimeoutExpired:
        raise ClaudeCLIError(f"claude CLI timed out after {timeout}s")
    if proc.returncode != 0:
        raise ClaudeCLIError(
            f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout.strip()


def call(system_prompt: str, user_prompt: str,
         timeout: Optional[int] = None) -> str:
    """One headless prompt -> text response. Retries a few times (re-resolving
    the CLI path each attempt) since the desktop app can swap its bundled
    binary out from under a running call mid-self-update. Raises
    ClaudeCLIError if every attempt fails."""
    last_err: Optional[ClaudeCLIError] = None
    for attempt in range(1 + CALL_RETRIES):
        try:
            return _call_once(system_prompt, user_prompt, timeout or config.CLAUDE_CLI_TIMEOUT)
        except ClaudeCLIError as exc:
            last_err = exc
            if attempt < CALL_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning("claude CLI call failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, 1 + CALL_RETRIES, wait, exc)
                time.sleep(wait)
    assert last_err is not None
    raise last_err
