from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from dualforge.ui.preview_helpers import format_bytes
from dualforge.ui.tree_builder import USER_ROLE, iter_leaves


class StatsDialog(QDialog):
    def __init__(self, tree, archive_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Asset Statistics")
        self.resize(420, 460)

        layout = QVBoxLayout(self)
        counts: dict[str, int] = defaultdict(int)
        sizes: dict[str, int] = defaultdict(int)
        total_files = 0
        total_size = 0
        for top_index in range(tree.topLevelItemCount()):
            for leaf in iter_leaves(tree.topLevelItem(top_index)):
                data = leaf.data(0, USER_ROLE) or {}
                if data.get("folder"):
                    continue
                kind = data.get("kind") or "file"
                size = int(data.get("size") or 0)
                counts[kind] += 1
                sizes[kind] += size
                total_files += 1
                total_size += size

        header = QLabel(
            f"<b>{archive_name}</b><br>"
            f"{total_files:,} files · {format_bytes(total_size)}"
        )
        header.setStyleSheet("font-size: 13px; padding-bottom: 6px;")
        layout.addWidget(header)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Type", "Files", "Size"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 80)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        for kind in sorted(counts):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(kind))
            self.table.setItem(row, 1, QTableWidgetItem(f"{counts[kind]:,}"))
            self.table.setItem(row, 2, QTableWidgetItem(format_bytes(sizes[kind])))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setProperty("role", "primary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


__all__ = ["StatsDialog"]