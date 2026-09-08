from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Dict, Iterator, List, Optional


class UnityError(Exception):
    pass


@dataclass
class UnityAsset:
    path: str
    type_name: str
    byte_size: int
    _reader: object = field(repr=False)


class UnityArchive:
    def __init__(self, path: str):
        try:
            import UnityPy
        except ImportError as exc:
            raise UnityError("UnityPy is required (pip install UnityPy)") from exc
        self.path = str(path)
        self.env = UnityPy.load(self.path)
        self._types = UnityPy
        self.load_sibling_streams()

    def load_sibling_streams(self) -> int:
        """Pre-load sibling stream files (.resS / .resource / .split*).

        UnityPy resolves external streams lazily relative to the bundle's
        folder, but only for CAB names listed in m_ExternalFiles. Eagerly
        registering every matching sibling here also covers files whose
        on-disk name differs from the CAB reference, so streamed textures,
        audio and mesh data decode reliably.
        """
        import os

        loaded = 0
        directory = Path(self.path).parent
        candidates: List[Path] = []
        try:
            for file in self.env.files.values():
                for external in getattr(file, "externals", []) or []:
                    ref = str(getattr(external, "path", "") or "")
                    for prefix in ("archive:/", "library/", "resources/"):
                        if ref.lower().startswith(prefix):
                            ref = ref[len(prefix):]
                            break
                    if ref and Path(ref).name:
                        candidates.append(directory / Path(ref).name)
        except Exception:
            pass
        stem = Path(self.path).stem
        try:
            for pattern in (
                f"{stem}.resS",
                f"{stem}.resource",
                f"{stem}.split*",
                "CAB-*.resS",
                "*.resS",
                "*.resA",
                "*.resH",
                "*.resource",
            ):
                candidates.extend(directory.glob(pattern))
        except OSError:
            pass
        seen = set()
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            try:
                if not candidate.is_file() or str(candidate) in self.env.files:
                    continue
                self.env.load_file(str(candidate), is_dependency=True)
                loaded += 1
            except Exception:
                continue
        return loaded

    def engine_version(self) -> str:
        """Best-effort engine version from the bundle/serialized header."""
        for file in self._serialized_files():
            value = getattr(file, "unity_version", None)
            if value:
                return str(value).strip()
        for file in self.env.files.values():
            value = getattr(file, "unity_version", None) or getattr(file, "version", None)
            if value:
                return str(value).strip()
        return ""

    def serialized_version(self) -> int:
        """Best-effort serialized format version (bundle header or -1)."""
        for file in self._serialized_files():
            header = getattr(file, "header", None)
            if header is not None:
                value = getattr(header, "version", None)
                if isinstance(value, int):
                    return value
        for file in self.env.files.values():
            header = getattr(file, "header", None)
            if header is not None:
                value = getattr(header, "version", None)
                if isinstance(value, int):
                    return value
        return -1

    def _serialized_files(self) -> list:
        """Iterate SerializedFile instances, unwrapping bundle containers."""
        result: list = []
        for file in self.env.files.values():
            children = getattr(file, "files", None)
            if isinstance(children, dict):
                result.extend(v for v in children.values() if type(v).__name__ == "SerializedFile")
            elif type(file).__name__ == "SerializedFile":
                result.append(file)
        return result

    def set_decrypt_key(self, key: str, scheme: str = "aes-256") -> None:
        """Set the Unity bundle decryption key via UnityPy.

        Standard Unity bundles take a 16-char ASCII key (or a 32-byte AES hex
        key as bytes). ``scheme`` selects the interpretation:

        - ``aes-256`` (default): pass the key straight through (UnityPy handles
          the AES/XOR bundle decryption with hex keys of any Unreal-style
          length, and 16-char keys verbatim).
        - ``unity-cn``: expect a 16-char key; if a ``0x``-hex AES key is given,
          UnityPy still accepts it (CN games commonly expose the raw AES key).
        """
        normalized = key
        if normalized.lower().startswith("0x"):
            hex_body = normalized[2:].strip()
            if len(hex_body) in (32, 64) and all(c in "0123456789abcdefABCDEF" for c in hex_body):
                normalized = bytes.fromhex(hex_body)
            else:
                normalized = normalized[2:].strip()
        try:
            self._types.set_assetbundle_decrypt_key(normalized)
        except (AttributeError, TypeError, ValueError) as exc:
            raise UnityError(f"could not set decrypt key: {exc}") from exc

    def world_meshes(self) -> Iterator[Dict[str, object]]:
        """Yield crude mesh data for every readable Mesh in the archive.

        Each item carries ``name`` / ``vertices`` / ``triangles`` / ``uvs``
        (suitable for the dependency-free USD writer). Unreadable or empty
        meshes are skipped so a partially-damaged archive still exports.
        """
        from UnityPy.helpers import MeshHelper

        for asset in self.assets():
            if asset.type_name != "Mesh":
                continue
            try:
                obj = asset._reader.read()
                handler = MeshHelper.MeshHandler(obj)
                handler.process()
                vertices = handler.m_Vertices
                triangles = handler.get_triangles()
                if not vertices or not any(triangles):
                    continue
                yield {
                    "name": str(getattr(obj, "m_Name", "") or asset.path),
                    "vertices": [tuple(v) for v in vertices],
                    "triangles": [tuple(t) for submesh in triangles for t in submesh],
                    "uvs": [tuple(uv) for uv in (handler.m_UV0 or [])],
                }
            except Exception:
                continue

    def world_textures(self) -> Iterator[Dict[str, object]]:
        """Yield PNG payloads for every readable Texture2D in the archive."""
        import io

        for asset in self.assets():
            if asset.type_name != "Texture2D":
                continue
            try:
                image = asset._reader.read().image
            except Exception:
                continue
            if image is None:
                continue
            buffer = io.BytesIO()
            try:
                image.save(buffer, format="PNG")
            except Exception:
                continue
            yield {"name": str(Path(asset.path).name), "pixels": buffer.getvalue()}

    def assets(self) -> Iterator[UnityAsset]:
        """Iterate all readable objects.

        Bundles expose a name->reader container; plain serialized files
        (`.assets`, `level*`, `globalgamemanagers`, ...) have no container,
        so fall back to every object in the environment.
        """
        try:
            container = self.env.container.items()
            first = next(container, None)
        except Exception:
            first = None
        if first is not None:
            for path, value in chain((first,), container):
                reader = value.deref() if hasattr(value, "deref") else value
                yield UnityAsset(
                    path=path,
                    type_name=reader.type.name,
                    byte_size=reader.byte_size,
                    _reader=reader,
                )
            return
        for reader in self.env.objects:
            yield UnityAsset(
                path=str(reader.path_id),
                type_name=reader.type.name,
                byte_size=reader.byte_size,
                _reader=reader,
            )

    def extract_asset(
        self,
        asset: UnityAsset,
        out_dir: str,
        fmt: Optional[str] = None,
        formats: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        from dualforge.export.convert import (
            DEFAULT_FORMATS,
            save_font,
            save_json,
            save_shader,
            save_text,
            save_texture,
        )

        written: List[str] = []
        obj = asset._reader.read()
        type_name = asset.type_name
        chosen = fmt or (formats or {}).get(type_name) or DEFAULT_FORMATS.get(type_name, "bin")
        stem = _output_stem(out_dir, asset.path)
        if type_name == "Texture2D":
            image = obj.image
            if image is None:
                raise UnityError(f"texture has no decodable image: {asset.path}")
            written.append(save_texture(image, stem, chosen))
        elif type_name == "Sprite":
            image = obj.image
            if image is None:
                raise UnityError(f"sprite has no decodable image: {asset.path}")
            written.append(save_texture(image, stem, chosen))
        elif type_name == "AudioClip":
            written.append(_export_audio(obj, out_dir, asset.path, chosen))
        elif type_name == "Mesh":
            written.append(_export_mesh(obj, out_dir, asset.path, chosen, scope=self.env, mesh_reader=asset._reader))
        elif type_name == "AnimationClip":
            written.append(_export_animation(obj, out_dir, asset.path, chosen))
        elif type_name == "Cubemap":
            written.extend(_export_cubemap(obj, out_dir, asset.path, chosen))
        elif type_name in {"VideoClip", "MovieTexture"}:
            written.append(_export_video(obj, out_dir, asset.path, chosen, archive_path=self.path))
        elif type_name == "SpriteAtlas":
            written.extend(_export_sprite_atlas(obj, out_dir, asset.path, chosen, assets_file=_assets_file(asset._reader)))
        elif type_name == "AnimatorController":
            written.append(_export_object_summary(obj, out_dir, asset.path, kind="animator", assets_file=_assets_file(asset._reader)))
        elif type_name == "Avatar":
            written.append(_export_object_summary(obj, out_dir, asset.path, kind="avatar", assets_file=_assets_file(asset._reader)))
        elif type_name == "LightmapData":
            written.append(_export_object_summary(obj, out_dir, asset.path, kind="lightmap", assets_file=_assets_file(asset._reader)))
        elif type_name == "TextAsset":
            data = obj.m_Script
            if isinstance(data, str):
                data = data.encode("utf-8")
            written.append(save_text(data, stem, "txt"))
        elif type_name == "Font":
            written.append(save_font(asset, stem, chosen))
        elif type_name == "Shader":
            written.append(save_shader(asset, stem, chosen))
        elif type_name in {"MonoBehaviour", "Material"}:
            from dualforge.export.unity_assets import typetree_dict

            tree = typetree_dict(asset)
            if tree is not None:
                written.append(save_json(tree, stem, "json"))
            else:
                try:
                    data = obj.raw_data
                except AttributeError:
                    data = asset._reader.get_raw_data()
                written.append(_write_bytes(stem.with_suffix(".bin"), data))
        else:
            data = asset._reader.get_raw_data()
            written.append(_write_bytes(stem.with_suffix(".bin"), data))
        return written


def _mesh_to_obj(name: str, verts, tris, uvs) -> bytes:
    lines = [f"o {name or 'mesh'}"]
    for vertex in verts:
        x, y, z = vertex[0], vertex[1], vertex[2] if len(vertex) > 2 else 0.0
        lines.append(f"v {x} {y} {z}")
    if uvs:
        for uv in uvs:
            u, v = uv[0], uv[1] if len(uv) > 1 else 0.0
            lines.append(f"vt {u} {v}")
    for submesh in tris:
        for tri in submesh:
            if uvs:
                lines.append("f " + " ".join(f"{i + 1}/{i + 1}" for i in tri))
            else:
                lines.append("f " + " ".join(str(i + 1) for i in tri))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _export_mesh(obj, out_dir: str, asset_path: str, fmt: str, scope=None, mesh_reader=None) -> str:
    from dualforge.export.convert import save_mesh
    from UnityPy.helpers import MeshHelper

    handler = MeshHelper.MeshHandler(obj)
    handler.process()
    name = str(getattr(obj, "m_Name", "") or "").strip()
    verts = handler.m_Vertices
    tris = handler.get_triangles()
    if not verts or not any(tris):
        raise UnityError(f"mesh has no decodable geometry: {asset_path}")
    uvs = handler.m_UV0 or []
    stem = _output_stem(out_dir, asset_path)
    fmt = str(fmt).lower().lstrip(".")
    if fmt == "fbx":
        return _export_skinned_fbx(
            obj,
            stem,
            name,
            verts,
            tris,
            uvs,
            scope=scope,
            reader=mesh_reader,
        )
    if fmt == "gltf":
        try:
            return _export_skinned_gltf(
                obj,
                stem,
                name,
                verts,
                tris,
                uvs,
                scope=scope,
                reader=mesh_reader,
            )
        except Exception:
            # skinning is optional; degrade to a plain glTF on any problem
            data = _mesh_to_obj(name, verts, tris, uvs)
            return save_mesh(name or "mesh", data, stem, "gltf")
    data = _mesh_to_obj(name, verts, tris, uvs)
    return save_mesh(name or "mesh", data, stem, fmt)


def _export_skinned_gltf(obj, stem, name, verts, tris, uvs, scope=None, reader=None) -> str:
    """Export a mesh as skinned glTF (skeleton + morph targets) when possible."""
    from dualforge.export.gltf import write_gltf, write_gltf_skinned
    from dualforge.export.unity_skin import (
        bind_poses,
        blend_shapes,
        bone_hierarchy,
        find_skinned_mesh_renderer,
        skin_data,
    )

    triangles = [tuple(t) for submesh in tris for t in submesh]
    normals = getattr(obj, "m_Normals", None) or getattr(obj, "m_Normals4", None)

    skin = skin_data(obj)
    binds = bind_poses(obj)
    if skin is None or binds is None:
        target = stem.with_suffix(".gltf")
        write_gltf(
            str(target),
            [tuple(v) for v in verts],
            triangles,
            normals=[tuple(float(x) for x in n) for n in normals] if normals else None,
            uvs=[tuple(float(u) for u in uv) for uv in uvs] if uvs else None,
            name=name or "mesh",
        )
        return str(target)

    joints, weights = skin
    if len(joints) != len(verts):
        joints, weights = None, None

    bone_names: list = []
    bone_parents: list = []
    assets_file = None
    if reader is not None:
        try:
            assets_file = reader.assets_file
        except Exception:
            assets_file = None
    if assets_file is not None and scope is not None:
        try:
            smr = find_skinned_mesh_renderer(_iter_objects(scope), reader)
            if smr is not None:
                bone_names, bone_parents = bone_hierarchy(smr, assets_file)
        except UnityError:
            raise
        except Exception:
            bone_names, bone_parents = [], []
    if not bone_names:
        bone_names = [f"Bone_{idx}" for idx in range(len(binds))]
        bone_parents = [-1] * len(binds)

    blendshapes = blend_shapes(obj, len(verts))

    target = stem.with_suffix(".gltf")
    write_gltf_skinned(
        str(target),
        vertices=[tuple(v) for v in verts],
        triangles=triangles,
        normals=[tuple(float(x) for x in n) for n in normals] if normals else None,
        uvs=[tuple(float(u) for u in uv) for uv in uvs] if uvs else None,
        joints=joints,
        weights=weights,
        bind_matrices=binds,
        bone_names=bone_names,
        bone_parents=bone_parents,
        blendshapes=blendshapes or None,
        name=name or "mesh",
    )
    return str(target)


def _export_skinned_fbx(obj, stem, name, verts, tris, uvs, scope=None, reader=None) -> str:
    """Export a mesh (skinned + morph targets) to FBX when possible."""
    from dualforge.export.fbx import write_fbx_mesh
    from dualforge.export.unity_skin import (
        bind_poses,
        blend_shapes,
        bone_hierarchy,
        find_skinned_mesh_renderer,
        skin_data,
    )

    triangles = [tuple(t) for submesh in tris for t in submesh]
    normals = getattr(obj, "m_Normals", None) or getattr(obj, "m_Normals4", None)

    skin = skin_data(obj)
    binds = bind_poses(obj)
    if skin is None or binds is None:
        return _export_plain_fbx(stem, name, verts, triangles, normals, uvs)

    joints, weights = skin
    if len(joints) != len(verts):
        return _export_plain_fbx(stem, name, verts, triangles, normals, uvs)

    bone_names: list = []
    bone_parents: list = []
    assets_file = None
    if reader is not None:
        try:
            assets_file = reader.assets_file
        except Exception:
            assets_file = None
    if assets_file is not None and scope is not None:
        try:
            smr = find_skinned_mesh_renderer(_iter_objects(scope), reader)
            if smr is not None:
                bone_names, bone_parents = bone_hierarchy(smr, assets_file)
        except UnityError:
            raise
        except Exception:
            bone_names, bone_parents = [], []
    if not bone_names:
        bone_names = [f"Bone_{idx}" for idx in range(len(binds))]
        bone_parents = [-1] * len(binds)

    blendshapes = blend_shapes(obj, len(verts))

    target = stem.with_suffix(".fbx")
    write_fbx_mesh(
        str(target),
        name or "mesh",
        [tuple(v) for v in verts],
        triangles,
        normals=[tuple(float(x) for x in n) for n in normals] if normals else None,
        uvs=[tuple(float(u) for u in uv) for uv in uvs] if uvs else None,
        bone_names=bone_names,
        bone_parents=bone_parents,
        bind_matrices=binds,
        joints=joints,
        weights=weights,
        blendshapes=blendshapes or None,
    )
    return str(target)


def _export_plain_fbx(stem, name, verts, triangles, normals, uvs) -> str:
    from dualforge.export.fbx import write_fbx_mesh

    target = stem.with_suffix(".fbx")
    write_fbx_mesh(
        str(target),
        name or "mesh",
        [tuple(v) for v in verts],
        triangles,
        normals=[tuple(float(x) for x in n) for n in normals] if normals else None,
        uvs=[tuple(float(u) for u in uv) for uv in uvs] if uvs else None,
    )
    return str(target)


def _assets_file(reader) -> object:
    try:
        return reader.assets_file
    except Exception:
        return None


def _export_cubemap(obj, out_dir: str, asset_path: str, fmt: str) -> List[str]:
    from dualforge.export.convert import save_texture
    from dualforge.export.unity_extra import cubemap_faces

    stem = _output_stem(out_dir, asset_path)
    faces = cubemap_faces(obj)
    if not faces:
        raise UnityError(f"cubemap has no decodable faces: {asset_path}")
    written: List[str] = []
    if len(faces) == 1:
        written.append(save_texture(faces[0], stem, fmt))
        return written
    for index, face in enumerate(faces):
        face_stem = stem.with_name(f"{stem.name}_face{index}")
        written.append(save_texture(face, face_stem, "png"))
    return written


def _export_video(obj, out_dir: str, asset_path: str, fmt: str, archive_path: str) -> str:
    from dualforge.export.convert import save_json
    from dualforge.export.unity_extra import video_clip_data

    stem = _output_stem(out_dir, asset_path)
    fmt = str(fmt).lower().lstrip(".")
    if fmt in ("json", "txt"):
        metadata = {
            "name": str(getattr(obj, "m_Name", "") or asset_path),
            "width": getattr(obj, "Width", 0),
            "height": getattr(obj, "Height", 0),
            "frame_rate": getattr(obj, "m_FrameRate", 0.0),
            "frame_count": getattr(obj, "m_FrameCount", 0),
            "format": getattr(obj, "m_Format", 0),
            "original_path": str(getattr(obj, "m_OriginalPath", "") or ""),
        }
        return save_json(metadata, stem, "json")
    try:
        data, suffix = video_clip_data(obj, archive_path, None)
    except ValueError:
        data, suffix = b"", Path(getattr(obj, "m_OriginalPath", "") or "").suffix or ".mp4"
    if not data:
        raise UnityError(
            "video export failed: no streamed resource resolved "
            f"({getattr(getattr(obj, 'm_ExternalResources', None), 'm_Path', 'unknown')})"
        )
    if fmt not in ("source", "raw", "bin"):
        suffix = f".{fmt}"
    return _write_bytes(stem.with_suffix(suffix), data)


def _export_sprite_atlas(obj, out_dir: str, asset_path: str, fmt: str, assets_file) -> List[str]:
    from dualforge.export.convert import save_texture
    from dualforge.export.unity_extra import sprite_atlas_entries

    entries = sprite_atlas_entries(obj, assets_file) if assets_file is not None else []
    if not entries:
        raise UnityError(f"sprite atlas has no resolvable sprites: {asset_path}")
    written: List[str] = []
    for name, image in entries:
        entry_stem = _output_stem(out_dir, asset_path, name=name)
        written.append(save_texture(image, entry_stem, fmt))
    return written


def _export_object_summary(obj, out_dir: str, asset_path: str, kind: str, assets_file) -> str:
    from dualforge.export.convert import save_json
    from dualforge.export.unity_extra import (
        animator_summary,
        avatar_summary,
        lightmap_summary,
    )

    stem = _output_stem(out_dir, asset_path)
    if kind == "animator":
        summary = animator_summary(obj, assets_file)
    elif kind == "avatar":
        summary = avatar_summary(obj)
    elif kind == "lightmap":
        summary = lightmap_summary(obj, assets_file)
    else:
        summary = {"name": str(getattr(obj, "m_Name", "") or asset_path)}
    summary.setdefault("type", kind)
    return save_json(summary, stem, "json")


def _iter_objects(scope):
    try:
        yield from scope.get_objects()
    except (AttributeError, TypeError):
        try:
            yield from scope.objects
        except (AttributeError, TypeError):
            return


def _export_animation(obj, out_dir: str, asset_path: str, fmt: str) -> str:
    from dualforge.export.convert import save_json
    from dualforge.export.unity_skin import animation_tracks

    stem = _output_stem(out_dir, asset_path)
    fmt = str(fmt).lower().lstrip(".")
    name = str(getattr(obj, "m_Name", "") or Path(asset_path).name) or "clip"
    if fmt == "json":
        summary: Dict[str, object] = {"name": name}
        tracks = animation_tracks(obj)
        summary["tracks"] = {
            node: {
                target: [[time, list(values)] for time, values in keys]
                for target, keys in node_data.items()
            }
            for node, node_data in tracks.items()
        }
        summary["sample_rate"] = getattr(obj, "m_SampleRate", 0) or 60
        return save_json(summary, stem, "json")

    if fmt == "fbx":
        from dualforge.export.fbx import write_fbx_animation

        target = stem.with_suffix(".fbx")
        write_fbx_animation(
            str(target),
            name=name,
            tracks=animation_tracks(obj),
        )
        return str(target)

    from dualforge.export.gltf import write_gltf_animation

    target = stem.with_suffix(".gltf")
    write_gltf_animation(
        str(target),
        name=name,
        tracks=animation_tracks(obj),
    )
    return str(target)


def _export_audio(obj, out_dir: str, asset_path: str, fmt: str) -> str:
    from UnityPy.export import AudioClipConverter

    stem = _output_stem(out_dir, asset_path)
    fmt = fmt.lower().lstrip(".")
    if fmt == "raw":
        raw = getattr(obj, "m_AudioData", None)
        if raw is None:
            raw = getattr(obj, "raw_data", None)
        if raw:
            return _write_bytes(stem.with_suffix(".audio"), bytes(raw))
        raise UnityError("audio has no raw data to export")
    try:
        samples = AudioClipConverter.extract_audioclip_samples(obj)
    except Exception as exc:
        raw = getattr(obj, "m_AudioData", None) or getattr(obj, "raw_data", None)
        if raw:
            return _write_bytes(stem.with_suffix(".audio"), bytes(raw))
        raise UnityError(f"audio export failed: {exc}") from exc
    if not samples:
        raise UnityError("audio clip produced no samples")
    name, data = next(iter(samples.items()))
    if fmt in {"wav", "ogg"} and Path(name).suffix.lower() == f".{fmt}":
        return _write_bytes(stem.with_suffix(f".{fmt}"), data)
    if fmt == "flac":
        try:
            from dualforge.audio import Vgmstream

            source = _write_bytes(stem.with_suffix(Path(name).suffix or ".wav"), data)
            return Vgmstream().convert(source, str(stem.with_suffix(".flac")), "flac")
        except Exception as exc:
            raise UnityError(f"flac export requires vgmstream: {exc}") from exc
    return _write_bytes(stem.with_suffix(Path(name).suffix or ".wav"), data)


def _sanitize_name(name: str) -> str:
    clean = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in name)
    return clean.strip() or "unnamed"


def _output_stem(out_dir: str, asset_path: str, name: str | None = None) -> Path:
    stem = name or Path(asset_path).name
    target = (
        Path(out_dir)
        / _sanitize_name(Path(asset_path).parent.as_posix())
        / _sanitize_name(stem)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _write_bytes(path: Path, data: bytes) -> str:
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


__all__ = ["UnityArchive", "UnityAsset", "UnityError"]
