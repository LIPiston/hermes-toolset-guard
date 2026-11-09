"""router-guard — keep the front profile a pure router.

Why this exists
---------------
Hermes derives a delegated child's toolset from its *parent's* toolset list
(``tools/delegate_tool.py::_build_children`` passes ``toolsets=None`` with the
comment "always inherit the parent's toolsets"). So ``hermes tools disable file``
on the front profile disarms every subagent too — configuration cannot express
"the router may not write, its workers may".

A ``pre_tool_call`` hook can, because it fires at call time with the calling
session's context. This plugin is installed ONLY in the front profile's plugin
dir, and plugin managers are keyed by HERMES_HOME, so:

  * top-level agent of this profile  -> the hook runs       -> blocked
  * delegate child on a profile route -> child's own home    -> hook absent -> allowed

The delegated-child escape hatch below is a second belt: an in-process child
that somehow still runs under this home is allowed too (children are the workers).

Modes (``plugins.entries.router-guard.settings.mode``)
------------------------------------------------------
``edits``  (default) block write_file + patch. Terminal stays available so the
           router can read back artifacts and verify a child's claims
           (``git status``, ``git diff``, ``date``). This is what OpenCode's
           orchestrator does: ``edit: * -> deny``, shell left open.
``strict`` also block terminal + execute_code. Harder, but the router then
           cannot independently verify anything it dispatched, and harness
           commands like ``git diff`` are gone too.

Note the honest limit, identical to OpenCode's: a shell is a write channel
(``echo > f``) and ``execute_code`` runs raw Python. ``edits`` is a discipline
barrier that makes the wrong action require deliberate circumvention; only
``strict`` closes the channel, at the cost of read-back verification.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

PLUGIN_ID = "router-guard"

# Toolsets whose tools mutate files. Kept as explicit tool names because the
# hook receives a tool name, not a toolset.
EDIT_TOOLS = frozenset({"write_file", "patch"})
# Escape hatches a strict router must not hold.
EXEC_TOOLS = frozenset({"terminal", "execute_code"})

_READ_HINT = (
    "Read back artifacts with read_file / search_files, or verify with "
    "terminal (git status, git diff, command output)."
)


def _mode(ctx: Any) -> str:
    """Plugin setting ``plugins.entries.router-guard.settings.mode``, read fresh each call so a
    config edit lands without a restart. Unknown values fall back to ``edits``."""
    try:
        raw = ctx.get_config("mode", "edits")
    except Exception:  # never let settings resolution break a tool call
        raw = "edits"
    mode = str(raw or "edits").strip().lower()
    return mode if mode in {"edits", "strict"} else "edits"


def _blocked_tools(ctx: Any) -> frozenset[str]:
    mode = _mode(ctx)
    return EDIT_TOOLS | EXEC_TOOLS if mode == "strict" else EDIT_TOOLS


def _is_delegated_child() -> bool:
    """True while the call belongs to a delegate_task child (its own profile scope)."""
    try:
        from agent.delegation_context import is_delegated_child_context

        return bool(is_delegated_child_context())
    except Exception:
        return False


def _profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name()
    except Exception:
        return ""


def make_guard(ctx: Any):
    """Build the ``pre_tool_call`` callback bound to this plugin's context."""

    def _guard(tool_name: str = "", args: Optional[dict] = None, **kwargs: Any) -> Optional[dict]:
        if tool_name not in _blocked_tools(ctx):
            return None
        if _is_delegated_child():
            return None  # a worker, not the router
        mode = _mode(ctx)
        profile = _profile_name()
        logger.info("router-guard blocked %s for profile %r (mode=%s)", tool_name, profile, mode)
        return {
            "action": "block",
            "message": (
                f"`{tool_name}` is disabled for the {profile or 'front'}-profile router "
                f"(router-guard, mode={mode}). This profile only dispatches and verifies; "
                "it does not modify files itself. Delegate the change with delegate_task to "
                "the profile that owns the surface (see your delegation routes), then verify "
                f"the result. {_READ_HINT}"
            ),
        }

    return _guard


def register(ctx) -> None:  # noqa: ARG001 — ctx is captured by the guard closure
    guard = make_guard(ctx)
    ctx.register_hook("pre_tool_call", guard)
