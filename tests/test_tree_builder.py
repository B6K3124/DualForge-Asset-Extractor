from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from dualforge.ui.tree_builder import (
    USER_ROLE,
    AssetTreeBuilder,
    apply_filter,
    checked_leaves,
    iter_leaves,
    matches,
    normalize_path,
    set_all_checkstates,
    split_parts,
)

# A QTreeWidget is a QWidget and must belong to an application instance;
# constructing one without an app crashes at interpreter shutdown on
# Windows (fail-fast abort), and test ordering varies. Same convention as
# test_preview_video.py / test_preview_helpers.py.
app = QApplication.instance() or QApplication([])


def test_normalize_path():
    assert normalize_path("Game\\Content\\a.pak") == "Game/Content/a.pak"
    assert normalize_path("/Game/Content/a.pak") == "Game/Content/a.pak"


def test_split_parts():
    assert split_parts("Game/Content//a.pak") == ["Game", "Content", "a.pak"]
    assert split_parts("") == []


def _make_tree():
    tree = QTreeWidget()
    builder = AssetTreeBuilder(tree)
    builder.add_file(
        "Game/Content/Maps/Test.umap",
        "file",
        100,
        {"path": "Game/Content/Maps/Test.umap", "kind": "file"},
    )
    builder.add_file(
        "Game/Content/Textures/Icon.png",
        "Texture2D",
        200,
        {"path": "Game/Content/Textures/Icon.png", "kind": "Texture2D"},
    )
    builder.add_file(
        "Game/Content/Textures/Sprite.png",
        "Sprite",
        150,
        {"path": "Game/Content/Textures/Sprite.png", "kind": "Sprite"},
    )
    return tree


def test_builds_hierarchy():
    tree = _make_tree()
    assert tree.topLevelItemCount() == 1
    game = tree.topLevelItem(0)
    assert game.text(0) == "Game"
    content = game.child(0)
    assert content.text(0) == "Content"
    assert content.childCount() == 2
    assert content.child(0).childCount() == 1


def test_folders_are_tristate():
    tree = _make_tree()
    game = tree.topLevelItem(0)
    assert game.flags() & Qt.ItemFlag.ItemIsAutoTristate
    assert game.checkState(0) == Qt.CheckState.Unchecked


def test_check_filters():
    tree = _make_tree()
    visible = apply_filter(tree, "icon", None, False)
    assert visible == 1
    for index in range(tree.topLevelItemCount()):
        for leaf in iter_leaves(tree.topLevelItem(index)):
            if leaf.text(0).lower() == "icon.png":
                assert not leaf.isHidden()
            else:
                assert leaf.isHidden()


def test_apply_type_filter():
    tree = _make_tree()
    visible = apply_filter(tree, "", "Texture2D", False)
    assert visible == 1


def test_regex_filter():
    tree = _make_tree()
    visible = apply_filter(tree, "sprite|umap", None, True)
    assert visible == 2


def test_checked_leaves_and_check_all():
    tree = _make_tree()
    set_all_checkstates(tree.topLevelItem(0), Qt.CheckState.Checked)
    leaves = []
    for index in range(tree.topLevelItemCount()):
        leaves += checked_leaves(tree.topLevelItem(index))
    assert len(leaves) == 3
    set_all_checkstates(tree.topLevelItem(0), Qt.CheckState.Unchecked)
    assert checked_leaves(tree.topLevelItem(0)) == []


def test_matches():
    item = QTreeWidgetItem(["Icon.png", "Texture2D", "200"])
    item.setData(0, USER_ROLE, {"kind": "Texture2D"})
    assert matches(item, "", None, False)
    assert matches(item, "icon", None, False)
    assert not matches(item, "nope", None, False)
    assert matches(item, "icon", "Texture2D", False)
    assert not matches(item, "icon", "Sprite", False)


def test_rooted_builder():
    tree = QTreeWidget()
    root = QTreeWidgetItem(["archive.pak"])
    root.setData(0, USER_ROLE, {"folder": True, "archive": "x.pak"})
    tree.addTopLevelItem(root)
    builder = AssetTreeBuilder(tree, root=root)
    builder.add_file("Game/A.pak", "file", 1, {"path": "Game/A.pak"})
    assert root.childCount() == 1
    game = root.child(0)
    assert game.text(0) == "Game"
    assert game.child(0).text(0) == "A.pak"