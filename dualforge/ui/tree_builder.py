from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

USER_ROLE = Qt.ItemDataRole.UserRole


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def split_parts(path: str) -> list[str]:
    return [part for part in normalize_path(path).split("/") if part]


def folder_key(parts: list[str]) -> str:
    return "/".join(parts)


class AssetTreeBuilder:
    """Builds a hierarchical folder tree from flat archive entry paths.

    Optionally root the tree under a specific parent item (for multi-archive
    folder mode), and supply a root data entry that the tree's top-level
    items are attached to.
    """

    def __init__(self, tree: QTreeWidget, root: QTreeWidgetItem | None = None):
        self.tree = tree
        self.root = root
        self._folders: dict[str, QTreeWidgetItem] = {}

    def reset(self, root: QTreeWidgetItem | None = None) -> None:
        self.root = root
        self._folders = {}

    def _folder_item(self, parts: list[str]) -> QTreeWidgetItem:
        if not parts:
            raise ValueError("folder requires at least one part")
        key = folder_key(parts)
        existing = self._folders.get(key)
        if existing is not None:
            return existing
        parent = None
        if len(parts) > 1:
            parent = self._folder_item(parts[:-1])
        item = QTreeWidgetItem([parts[-1]])
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsAutoTristate
        )
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setData(0, USER_ROLE, {"folder": True, "path": key})
        item.setData(1, USER_ROLE, {"folder": True, "path": key})
        item.setData(2, USER_ROLE, {"folder": True, "path": key})
        if parent is None:
            if self.root is None:
                self.tree.addTopLevelItem(item)
            else:
                self.root.addChild(item)
        else:
            parent.addChild(item)
        self._folders[key] = item
        return item

    def add_file(self, path: str, kind: str, size: int, data: dict, icon=None) -> QTreeWidgetItem:
        parts = split_parts(path)
        if not parts:
            parts = ["unnamed"]
        parent = None
        if len(parts) > 1:
            parent = self._folder_item(parts[:-1])
        item = QTreeWidgetItem([parts[-1], kind, f"{size:,}" if size else ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        if icon is not None:
            item.setIcon(0, icon)
        item.setData(0, USER_ROLE, data)
        if parent is None:
            if self.root is None:
                self.tree.addTopLevelItem(item)
            else:
                self.root.addChild(item)
        else:
            parent.addChild(item)
        return item


def iter_leaves(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    out: list[QTreeWidgetItem] = []
    for index in range(item.childCount()):
        child = item.child(index)
        data = child.data(0, USER_ROLE)
        if data and data.get("folder"):
            out.extend(iter_leaves(child))
        else:
            out.append(child)
    return out


def checked_leaves(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    leaves = iter_leaves(item)
    return [leaf for leaf in leaves if leaf.checkState(0) != Qt.CheckState.Unchecked]


def set_all_checkstates(item: QTreeWidgetItem, state: Qt.CheckState) -> None:
    for index in range(item.childCount()):
        child = item.child(index)
        child.setCheckState(0, state)
        set_all_checkstates(child, state)


def matches(item: QTreeWidgetItem, text: str, type_filter: str | None, use_regex: bool) -> bool:
    data = item.data(0, USER_ROLE)
    if data and data.get("folder"):
        return False
    if type_filter and type_filter != "All" and item.text(1) != type_filter:
        return False
    name = item.text(0)
    if not text:
        return True
    if use_regex:
        import re

        try:
            return re.search(text, name, re.IGNORECASE) is not None
        except re.error:
            return text.lower() in name.lower()
    return text.lower() in name.lower()


def apply_filter(
    tree: QTreeWidget,
    text: str,
    type_filter: str | None,
    use_regex: bool,
) -> int:
    """Hide non-matching items recursively; returns the number of visible leaves."""
    visible = 0

    def visit(item: QTreeWidgetItem) -> bool:
        nonlocal visible
        data = item.data(0, USER_ROLE)
        is_folder = bool(data and data.get("folder"))
        if is_folder:
            any_visible = False
            for index in range(item.childCount()):
                if visit(item.child(index)):
                    any_visible = True
            item.setHidden(not any_visible)
            return any_visible
        match = matches(item, text, type_filter, use_regex)
        item.setHidden(not match)
        if match:
            visible += 1
        return match

    for index in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(index))
    return visible


def collect_entry_data(item: QTreeWidgetItem) -> tuple[str, dict]:
    data = item.data(0, USER_ROLE)
    return item.text(0), data


__all__ = [
    "USER_ROLE",
    "AssetTreeBuilder",
    "apply_filter",
    "checked_leaves",
    "collect_entry_data",
    "folder_key",
    "iter_leaves",
    "matches",
    "normalize_path",
    "set_all_checkstates",
    "split_parts",
]