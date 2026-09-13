"""A Qt tree widget that renders a Unity type tree (or any nested dict) as a
browsable "Property / Value" inspector, mirroring the object inspectors of
FModel (Properties) and AssetStudio object dumps."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

MAX_STRING = 220
MAX_CHILDREN = 2000


class InspectorTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Property", "Value"])
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 320)
        self.header().setStretchLastSection(True)

    def set_object(self, title: str, tree: dict) -> None:
        self.clear()
        root = self.invisibleRootItem()
        header = QTreeWidgetItem([title, ""])
        font = header.font(0)
        font.setBold(True)
        header.setFont(0, font)
        root.addChild(header)
        if isinstance(tree, dict):
            _build(tree, header, visited=(), depth=0)
        header.setExpanded(True)

    def clear_object(self) -> None:
        self.clear()


def _build(node: Any, parent: QTreeWidgetItem, visited: tuple, depth: int) -> None:
    if depth > 10:
        return
    children = parent.childCount()
    if children >= MAX_CHILDREN:
        parent.addChild(QTreeWidgetItem([f"... {children} children truncated ...", ""]))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = _item(key, value)
            parent.addChild(child)
            _extend(child, value, visited, depth)
            if child.childCount():
                child.setExpanded(False)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node[:500]):
            child = _item(f"[{index}]", value)
            parent.addChild(child)
            _extend(child, value, visited, depth)
    elif isinstance(node, (str, int, float, bool)) or node is None:
        parent.addChild(_item("value", node))


def _extend(item: QTreeWidgetItem, value: Any, visited: tuple, depth: int) -> None:
    if isinstance(value, dict):
        if id(value) in visited:
            item.setToolTip(1, "(recursive reference)")
            return
        _build(value, item, visited + (id(value),), depth + 1)
    elif isinstance(value, (list, tuple)):
        _build(value, item, visited, depth + 1)


def _item(key: str, value: Any) -> QTreeWidgetItem:
    text = _value_text(value)
    item = QTreeWidgetItem([key, f"{text}..."]) if _expandable(value) else QTreeWidgetItem([key, text])
    if isinstance(value, bool):
        item.setToolTip(1, "bool")
    return item


def _expandable(value: Any) -> bool:
    if isinstance(value, dict) and value:
        return True
    if isinstance(value, (list, tuple)):
        return bool(value)
    return False


def _value_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return ""
    text = str(value)
    if len(text) > MAX_STRING:
        return text[:MAX_STRING] + "..."
    return text