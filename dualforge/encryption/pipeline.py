"""Transform pipeline: compose ordered decryption stages.

An archive (or a single bundle) may go through several stages in sequence, e.g.
``aes-256`` then ``xor8`` for Delta Force. A ``TransformPipeline`` runs its
stages in registration order; readers that already hand us per-entry data use
``apply`` for each block.

Special kinds of entries that describe a *whole archive* (partitioned/OOTP,
dynamic GUID-keyed) are handled by the reader via ``split_stages`` helpers below
rather than block-level transforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dualforge.encryption.registry import Context, KeyMaterial, list_schemes, transform


@dataclass
class Stage:
    """A single named transform plus its parameter overrides."""

    name: str
    parameters: dict = field(default_factory=dict)

    def apply(self, data: bytes, key: KeyMaterial, ctx: Context) -> bytes:
        if self.parameters:
            merged = KeyMaterial(
                key_str=key.key_str,
                scheme=key.scheme,
                guid=key.guid,
                parameters={**key.parameters, **self.parameters},
            )
            return transform(data, self.name, merged, ctx)
        return transform(data, self.name, key, ctx)


class TransformPipeline:
    """Ordered sequence of ``Stage`` transforms applied to one data block."""

    def __init__(self, stages: list[Stage]):
        self.stages = stages

    @classmethod
    def from_scheme(cls, scheme: str, overrides: dict | None = None) -> TransformPipeline:
        """Build a pipeline from a scheme name, splitting '+' into stages.

        e.g. ``aes-256+xor8`` -> [Stage('aes-256'), Stage('xor8')].
        """
        overrides = overrides or {}
        names = scheme.split("+")
        stages = []
        for i, name in enumerate(names):
            stages.append(Stage(name, overrides.copy() if i == len(names) - 1 else {}))
        return cls(stages)

    def apply(self, data: bytes, key: KeyMaterial, ctx: Context) -> bytes:
        for stage in self.stages:
            data = stage.apply(data, key, ctx)
        return data

    def __bool__(self) -> bool:
        return bool(self.stages)

    def __repr__(self) -> str:
        return f"TransformPipeline({[s.name for s in self.stages]})"


def build_pipeline(scheme: str, key: KeyMaterial, ctx: Context | None = None) -> TransformPipeline:
    """Convenience: expand a scheme into a pipeline, unknown schemes are empty."""
    if not scheme:
        return TransformPipeline([])
    known = set(list_schemes())
    names = scheme.split("+")
    if any(n not in known for n in names):
        return TransformPipeline([])
    return TransformPipeline.from_scheme(scheme)
