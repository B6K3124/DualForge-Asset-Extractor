"""Import all built-in schemes so they self-register in the registry."""

from __future__ import annotations

from dualforge.encryption.schemes import aes  # noqa: F401
from dualforge.encryption.schemes import derived  # noqa: F401
from dualforge.encryption.schemes import games  # noqa: F401  (per-game schemes)
from dualforge.encryption.schemes import partial  # noqa: F401
from dualforge.encryption.schemes import sm4  # noqa: F401  (SM4 / ABI key tables)
from dualforge.encryption.schemes import unity_cn  # noqa: F401
from dualforge.encryption.schemes import xor  # noqa: F401
