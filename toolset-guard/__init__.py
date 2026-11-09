"""toolset-guard — enforce a profile's declared toolset boundary at call time.

Why this exists
---------------
``tools/delegate_tool.py::_build_children`` passes ``toolsets=None`` with the
comment "always inherit the parent's toolsets", so a delegated child always runs
with the *parent* profile's toolset set. Listing toolsets in a profile's
config.yaml is therefore inert for delegated runs: the child sees whatever the
parent had.

A ``pre_tool_call`` hook cannot restore an allow-list (tools are already bound),
but it can veto the specific calls that fall outside the declared boundary. The
plugin lives in *one* profile's plugin directory, and plugin managers are cached
per resolved HERMES_HOME, so it only ever fires for calls made inside that
profile.

Configuration
-------------
``plugins.entries.toolset-guard.settings`` in the target profile's config.yaml::

    plugins:
      entries:
        toolset-guard:
          settings:
            profile_label: "device-ops"        # display name in block messages
            blocked_toolsets:                  # toolsets this profile does not own
              - code_execution
              - computer_use
              - connections
              - tts

Toolset names are expanded through ``toolsets.resolve_toolset``, so you never
hand-maintain a tool-name list. Unknown names resolve to nothing and are simply
ignored (fail-open). Both keys are read fresh on every call, so a config edit
lands without a restart.

``browser`` is deliberately left alone in every example: browser automation is
usually shared by all profiles on purpose. Add it to ``blocked_toolsets`` only if
that profile genuinely must not drive a browser.

Fail-open by design: any internal error returns ``None`` so a guard bug can never
wedge a legitimate tool call.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

PLUGIN_ID = "toolset-guard"
DEFAULT_LABEL = "this profile"

# (blocked_toolsets tuple) -> frozenset of tool names. Rebuilt whenever the
# configured list changes; resolve_toolset itself memoises per toolset.
_resolved_cache: dict[tuple[str, ...], frozenset[str]] = {}


def _blocked_names(ctx: Any) -> tuple[str, ...]:
    """Read ``blocked_toolsets`` as a normalised tuple; tolerate a bare string."""
    raw = ctx.get_config("blocked_toolsets", [])
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [chunk for chunk in raw.replace("\n", ",").split(",")]
    if not isinstance(raw, Iterable):
        return ()
    names = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _expand(names: tuple[str, ...]) -> frozenset[str]:
    """Resolve toolset names to the tool names they expand to (cached)."""
    if not names:
        return frozenset()
    cached = _resolved_cache.get(names)
    if cached is not None:
        return cached
    from toolsets import resolve_toolset

    tools: set[str] = set()
    for name in names:
        try:
            tools.update(resolve_toolset(name, include_registry=False))
        except Exception:  # an unknown/broken name must not disarm the rest
            logger.debug("toolset-guard could not resolve %r", name, exc_info=True)
    resolved = frozenset(tools)
    _resolved_cache[names] = resolved
    return resolved


def _label(ctx: Any) -> str:
    try:
        value = str(ctx.get_config("profile_label", DEFAULT_LABEL) or DEFAULT_LABEL).strip()
    except Exception:
        value = DEFAULT_LABEL
    return value or DEFAULT_LABEL


def register(ctx) -> None:
    def _guard(tool_name: str = "", args: Optional[dict] = None, **kwargs: Any) -> Optional[dict]:
        """Veto tools outside this profile's declared toolsets."""
        try:
            if tool_name not in _expand(_blocked_names(ctx)):
                return None
            label = _label(ctx)
            logger.info("toolset-guard blocked %s for profile %r", tool_name, label)
            return {
                "action": "block",
                "message": (
                    f"`{tool_name}` is disabled for the {label} profile: its toolset is not "
                    "declared for this profile (toolset-guard). Use a tool this profile does "
                    "own, or report back that the capability is unavailable so the caller can "
                    "route the step to a profile that owns it. Do not try to reach the same "
                    "effect through another tool."
                ),
            }
        except Exception:  # never let the guard break a tool call
            logger.debug("toolset-guard check failed for %s", tool_name, exc_info=True)
            return None

    ctx.register_hook("pre_tool_call", _guard)
