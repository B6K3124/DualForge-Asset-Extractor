from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    window: str
    base: str
    alt_base: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_dim: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    warning: str
    danger: str
    selection: str
    selection_text: str
    tooltip_bg: str
    tooltip_text: str


DARK = Palette(
    name="dark",
    window="#1b1c22",
    base="#15161b",
    alt_base="#1a1c24",
    surface="#22242e",
    surface_alt="#2a2d3a",
    border="#343747",
    text="#d7dae3",
    text_dim="#8b90a3",
    accent="#e88b3a",
    accent_hover="#f09d55",
    accent_text="#16171c",
    success="#4fae6d",
    warning="#d9a13c",
    danger="#e05252",
    selection="#3b4c66",
    selection_text="#ffffff",
    tooltip_bg="#2a2d3a",
    tooltip_text="#e8eaf1",
)

LIGHT = Palette(
    name="light",
    window="#f4f5f7",
    base="#ffffff",
    alt_base="#eef0f4",
    surface="#fafbfc",
    surface_alt="#e8eaef",
    border="#c9cdd6",
    text="#23262e",
    text_dim="#6b7180",
    accent="#c46a1f",
    accent_hover="#a95814",
    accent_text="#ffffff",
    success="#2f8f4e",
    warning="#a87b12",
    danger="#c93a3a",
    selection="#cfe0f5",
    selection_text="#12233c",
    tooltip_bg="#3a3d47",
    tooltip_text="#f2f3f6",
)


def qss(p: Palette) -> str:
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog {{
    background: {p.window};
    color: {p.text};
}}
QWidget {{ color: {p.text}; font-size: 13px; }}

QMenuBar {{
    background: {p.window};
    border-bottom: 1px solid {p.border};
    padding: 2px 6px;
}}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {p.surface_alt}; }}
QMenuBar::item:pressed {{ background: {p.accent}; color: {p.accent_text}; }}

QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    padding: 5px;
}}
QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QMenu::item:disabled {{ color: {p.text_dim}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

QToolBar {{
    background: {p.window};
    border-bottom: 1px solid {p.border};
    spacing: 4px;
    padding: 4px;
}}
QToolBar::separator {{ width: 1px; background: {p.border}; margin: 4px 6px; }}
QToolButton {{ padding: 5px 8px; border-radius: 5px; }}
QToolButton:hover {{ background: {p.surface_alt}; }}
QToolButton:pressed {{ background: {p.selection}; }}
QToolButton:checked {{ background: {p.selection}; }}

QStatusBar {{ background: {p.window}; border-top: 1px solid {p.border}; color: {p.text_dim}; }}
QStatusBar::item {{ border: none; }}

QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {p.window};
    border-bottom: 1px solid {p.border};
    padding: 6px 10px;
    font-weight: 600;
}}

QTreeWidget, QTreeView, QListWidget, QTableWidget, QTableView {{
    background: {p.base};
    alternate-background-color: {p.alt_base};
    border: 1px solid {p.border};
    border-radius: 5px;
    selection-background-color: {p.selection};
    selection-color: {p.selection_text};
}}
QTreeWidget::item, QTreeView::item, QListWidget::item, QTableWidget::item {{
    padding: 3px 4px;
}}
QHeaderView::section {{
    background: {p.surface};
    color: {p.text_dim};
    border: none;
    border-right: 1px solid {p.border};
    border-bottom: 1px solid {p.border};
    padding: 5px 6px;
    font-weight: 600;
}}
QTreeWidget::branch {{ background: transparent; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.base};
    border: 1px solid {p.border};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {p.selection};
    selection-color: {p.selection_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {p.text_dim}; }}

QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}

QPushButton {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 5px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {p.accent}; color: {p.accent_text}; border-color: {p.accent}; }}
QPushButton:pressed {{ background: {p.accent_hover}; color: {p.accent_text}; }}
QPushButton:disabled {{ color: {p.text_dim}; background: {p.surface}; }}
QPushButton[role="primary"] {{
    background: {p.accent};
    color: {p.accent_text};
    border: 1px solid {p.accent};
}}
QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[role="danger"] {{
    background: transparent;
    color: {p.danger};
    border: 1px solid {p.danger};
}}
QPushButton[role="danger"]:hover {{ background: {p.danger}; color: {p.window}; }}

QProgressBar {{
    background: {p.base};
    border: 1px solid {p.border};
    border-radius: 5px;
    height: 14px;
    text-align: center;
    color: {p.text_dim};
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QProgressBar[busy="true"] {{ }}
QProgressBar[busy="true"]::chunk {{
    background: {p.accent};
    border-radius: 4px;
}}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.base}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}

QRadioButton {{ spacing: 7px; }}
QRadioButton::indicator {{ width: 15px; height: 15px; border-radius: 8px; border: 1px solid {p.border}; background: {p.base}; }}
QRadioButton::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.surface_alt}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text_dim}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.surface_alt}; border-radius: 4px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {p.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.surface_alt}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    background: {p.accent}; border: none;
}}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}

QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QToolTip {{
    background: {p.tooltip_bg};
    color: {p.tooltip_text};
    border: 1px solid {p.border};
    padding: 5px 8px;
    border-radius: 4px;
}}

QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 5px; }}
QTabBar::tab {{
    background: {p.surface};
    padding: 6px 16px;
    border: 1px solid {p.border};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QTabBar::tab:hover:!selected {{ background: {p.surface_alt}; }}

QGroupBox {{
    border: 1px solid {p.border};
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {p.text_dim}; }}

QLabel[role="badge"] {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 12px;
}}

QLabel[role="hero-title"] {{
    font-size: 22px;
    font-weight: 700;
    color: {p.text};
}}
QLabel[role="hero-subtitle"] {{
    font-size: 13px;
    color: {p.text_dim};
}}

QPlainTextEdit[role="text-page"] {{
    background: {p.base};
    color: {p.text};
}}
"""


def apply_theme(app: QApplication, name: str) -> None:
    palette = DARK if name == "dark" else LIGHT
    app.setStyleSheet(qss(palette))


def available_themes() -> dict[str, str]:
    return {DARK.name: DARK.name.title(), LIGHT.name: LIGHT.name.title()}


__all__ = ["DARK", "LIGHT", "Palette", "apply_theme", "available_themes", "qss"]
