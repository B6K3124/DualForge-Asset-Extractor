"""CLI command handlers, split into one module per sub-command.

:mod:`dualforge.cli` owns argument parsing (:func:`~dualforge.cli.build_parser`)
and dispatch (:func:`~dualforge.cli.main`); every ``_cmd_*`` handler lives here.
The handler names stay accessible as ``dualforge.cli._cmd_*`` via the re-exports
in :mod:`dualforge.cli`.
"""

from __future__ import annotations

from dualforge.cli_commands.codecs import _cmd_codecs
from dualforge.cli_commands.crack import _cmd_crack_run, _cmd_crack_status
from dualforge.cli_commands.detect import _cmd_detect
from dualforge.cli_commands.drivers import (
    _cmd_drivers_create,
    _cmd_drivers_export,
    _cmd_drivers_import,
    _cmd_drivers_list,
    _cmd_drivers_match,
    _cmd_drivers_show,
)
from dualforge.cli_commands.extract import _cmd_extract
from dualforge.cli_commands.il2cpp import _cmd_il2cpp_inspect, _cmd_il2cpp_strings
from dualforge.cli_commands.keys import (
    _cmd_keys_add,
    _cmd_keys_import,
    _cmd_keys_list,
    _cmd_keys_remove,
    _cmd_keys_schemes,
    _cmd_keys_sync,
    _cmd_keys_test,
)
from dualforge.cli_commands.locres import _cmd_locres_dump, _cmd_locres_edit
from dualforge.cli_commands.repack import (
    _cmd_repack_font,
    _cmd_repack_text,
    _cmd_repack_texture,
)
from dualforge.cli_commands.usmap import (
    _cmd_usmap_dump,
    _cmd_usmap_names,
    _cmd_usmap_repack,
    _cmd_usmap_validate,
)
from dualforge.cli_commands.world import _cmd_world

__all__ = [
    "_cmd_codecs",
    "_cmd_crack_run",
    "_cmd_crack_status",
    "_cmd_detect",
    "_cmd_drivers_create",
    "_cmd_drivers_export",
    "_cmd_drivers_import",
    "_cmd_drivers_list",
    "_cmd_drivers_match",
    "_cmd_drivers_show",
    "_cmd_extract",
    "_cmd_il2cpp_inspect",
    "_cmd_il2cpp_strings",
    "_cmd_keys_add",
    "_cmd_keys_import",
    "_cmd_keys_list",
    "_cmd_keys_remove",
    "_cmd_keys_schemes",
    "_cmd_keys_sync",
    "_cmd_keys_test",
    "_cmd_locres_dump",
    "_cmd_locres_edit",
    "_cmd_repack_font",
    "_cmd_repack_text",
    "_cmd_repack_texture",
    "_cmd_usmap_dump",
    "_cmd_usmap_names",
    "_cmd_usmap_repack",
    "_cmd_usmap_validate",
    "_cmd_world",
]