"""Tests for the synthetic hero Unity bundle used by docs screenshots."""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "unity3d" / "hero_mesh.unity3d"


def test_fixture_exists_and_is_unity_bundle():
    assert FIXTURE.is_file()
    header = FIXTURE.read_bytes()[:8]
    assert header == b"UnityFS\x00"


def test_fixture_is_detected_as_unity_bundle():
    from dualforge.detector.detector import detect

    detection = detect(str(FIXTURE))
    assert detection is not None
    assert detection.engine == "unity"
    assert detection.kind == "assetbundle"


def test_archive_lists_container_mesh():
    from dualforge.unity.archive import UnityArchive

    archive = UnityArchive(str(FIXTURE))
    assets = list(archive.assets())
    assert len(assets) == 1
    asset = assets[0]
    assert asset.path == "assets/meshes/hero_cube.mesh"
    assert asset.type_name == "Mesh"
    assert asset.byte_size > 0


def test_archive_exposes_engine_versions():
    from dualforge.unity.archive import UnityArchive

    archive = UnityArchive(str(FIXTURE))
    assert archive.engine_version().startswith("2022")
    assert archive.serialized_version() >= 21


def test_mesh_preview_decodes_geometry():
    from UnityPy.export import MeshExporter

    from dualforge.ui.preview_helpers import parse_obj
    from dualforge.unity.archive import UnityArchive

    archive = UnityArchive(str(FIXTURE))
    asset = list(archive.assets())[0]
    obj = asset._reader.read()
    obj_data = MeshExporter.export_mesh_obj(obj)
    mesh = parse_obj(obj_data.encode("utf-8"))
    assert mesh is not None
    verts, normals, tris, edges = mesh
    assert len(verts) == 8
    assert len(tris) == 12
    assert len(edges) == 18


def test_unity_preview_payload_contains_mesh(tmp_path: Path):
    from dualforge.ui.preview import PreviewItem, PreviewWorker
    from dualforge.unity.archive import UnityArchive

    archive = UnityArchive(str(FIXTURE))
    asset = list(archive.assets())[0]
    item = PreviewItem(
        title=asset.path,
        engine="unity",
        kind="Mesh",
        size=asset.byte_size,
        asset=asset,
        archive_path=str(FIXTURE),
        meta={"Type": "Mesh", "Engine": "Unity"},
    )
    worker = PreviewWorker(item, str(tmp_path))
    payload = worker._preview_unity()
    assert payload["kind"] == "Mesh"
    assert payload["title"].endswith("hero_cube.mesh")
    assert "mesh" in payload
    verts, normals, tris, edges = payload["mesh"]
    assert len(verts) == 8
    assert len(tris) == 12
    assert payload["meta"]["Vertices"] == "8"