"""Backwards-compatibility re-exports.

All logic has moved to cli, pipeline, commands, and wizard modules.
New code should import from those modules directly.
"""
from distillate.cli import (  # noqa: F401
    main,
    _main_wrapper,
    _opt,
    _bold,
    _dim,
    _is_dark_background,
    _VERSION,
    _HELP,
    _KNOWN_FLAGS,
)
from distillate.pipeline import (  # noqa: F401
    run_sync,
    _upload_paper,
    _reprocess,
    _find_papers,
    _compute_engagement,
    _demote_and_promote,
    _auto_promote,
)
from distillate.commands import (  # noqa: F401
    _status,
    _list,
    _queue,
    _remove,
    _print_digest,
    _suggest,
    _parse_suggestions,
    _print_suggestions,
    _import,
    _refresh_metadata,
    _backfill_s2,
    _backfill_highlights,
    _new_experiment,
    _launch_experiment,
    _list_experiments,
    _attach_experiment,
    _stop_experiment,
    _install_hooks,
    _watch,
    _sync_state,
    _scan_projects,
)
from distillate.wizard import (  # noqa: F401
    _init_wizard,
    _init_step5_claude,
    _init_step6_extras,
    _init_done,
    _init_seed,
    _init_newsletter,
    _schedule,
    _schedule_macos,
    _schedule_linux,
    _install_launchd,
    _mask_value,
    _prompt_with_default,
    _SUBSCRIBE_URL,
)
