"""Desktop-only colours and typography; CLI reports keep their own defaults."""

from html import escape
from pathlib import Path
from PySide6.QtGui import QColor, QPalette


def dark_palette():
    palette = QPalette()
    colours = {
        "Window": "#111318", "WindowText": "#F4F6FA", "Base": "#1B2028",
        "AlternateBase": "#202630", "Text": "#F4F6FA", "Button": "#252C36",
        "ButtonText": "#F4F6FA", "Highlight": "#244946", "HighlightedText": "#F4F6FA",
        "ToolTipBase": "#252C36", "ToolTipText": "#F4F6FA", "PlaceholderText": "#A7B0BE",
        "Link": "#3BCBC4", "Light": "#394451", "Mid": "#2C3440", "Dark": "#111318",
    }
    for role, colour in colours.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colour))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#697482"))
    return palette


STYLESHEET = """
QWidget { color: #F4F6FA; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog, QWidget#workspace, QWidget#welcomePage { background: #111318; }
QLabel { background: transparent; }
QLabel#muted { color: #A7B0BE; }
QLabel#eyebrow { color: #3BCBC4; font-size: 11px; font-weight: 600; }
QLabel#welcomeTitle { font-size: 32px; font-weight: 600; }
QLabel#welcomeSubtitle { color: #A7B0BE; font-size: 15px; }
QLabel#heading { font-size: 16px; font-weight: 600; }
QLabel#carTitle { font-size: 19px; font-weight: 600; }
QLabel#fieldLabel { color: #A7B0BE; font-size: 11px; }
QLabel#stateTitle { font-size: 21px; font-weight: 600; }
QFrame#panel, QFrame#welcomePanel { background: #1B2028; border: 1px solid #2B333F; border-radius: 8px; }
QLabel#notice { color: #A7B0BE; font-size: 12px; }
QLabel#error { color: #F1ACB5; background: #32212A; border: 1px solid #613440; border-radius: 5px; padding: 10px; }
QPushButton { background: #242B35; border: 1px solid #38424F; border-radius: 5px; padding: 7px 11px; }
QPushButton:hover { background: #303946; border-color: #637184; }
QPushButton:pressed { background: #1A3032; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #637184; border-radius: 3px; background: #14191F; }
QCheckBox::indicator:checked { background: #3BCBC4; border-color: #3BCBC4; }
QCheckBox:focus { color: #3BCBC4; }
QDoubleSpinBox { background: #14191F; border: 1px solid #39434F; border-radius: 5px; padding: 5px; selection-background-color: #244946; }
QPushButton:focus, QComboBox:focus, QTableWidget:focus, QTextEdit:focus, QLineEdit:focus { border: 1px solid #3BCBC4; }
QPushButton#primary { background: #3BCBC4; border-color: #3BCBC4; color: #102125; font-weight: 600; }
QPushButton#primary:hover { background: #68D9D3; }
QPushButton:disabled, QPushButton#primary:disabled { background: #232A33; border-color: #2B333F; color: #697482; }
QComboBox, QLineEdit { background: #14191F; border: 1px solid #39434F; border-radius: 5px; padding: 7px 9px; selection-background-color: #244946; }
QComboBox:disabled { color: #697482; border-color: #2B333F; }
QComboBox::drop-down { width: 22px; border: none; }
QComboBox QAbstractItemView, QListView, QTreeView { background: #1B2028; color: #F4F6FA; border: 1px solid #39434F; selection-background-color: #244946; selection-color: #F4F6FA; outline: 0; }
QTableWidget { background: #1B2028; alternate-background-color: #1B2028; border: 1px solid transparent; gridline-color: #2B333F; selection-background-color: #233B42; outline: 0; }
QTableWidget::item { border-bottom: 1px solid #29313C; padding: 4px; }
QTableWidget::item:selected { background: #233B42; color: #F4F6FA; }
QHeaderView::section { background: #1B2028; color: #A7B0BE; font-size: 10px; padding: 7px 4px; border: none; border-bottom: 1px solid #2B333F; }
QScrollArea { background: #111318; border: none; }
QScrollArea#graphView, QScrollArea#graphView QWidget { background: #1B2028; }
QTextEdit { background: #1B2028; border: none; padding: 20px; selection-background-color: #244946; }
QTabWidget::pane { background: #1B2028; border: 1px solid #2B333F; border-radius: 5px; top: -1px; }
QTabBar::tab { background: transparent; color: #A7B0BE; padding: 11px 20px; border-bottom: 2px solid transparent; margin-right: 6px; }
QTabBar::tab:selected { color: #F4F6FA; border-bottom-color: #3BCBC4; }
QTabBar::tab:hover { color: #3BCBC4; background: #1B2028; }
QTabBar:focus { border: 1px solid #3BCBC4; }
QSplitter::handle { background: #111318; width: 10px; }
QSplitter::handle:hover { background: #2B474A; }
QScrollBar:vertical { background: #14191F; width: 10px; margin: 0; }
QScrollBar:horizontal { background: #14191F; height: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #45515F; min-height: 30px; border-radius: 4px; }
QScrollBar::handle:horizontal { background: #45515F; min-width: 30px; border-radius: 4px; }
QScrollBar::handle:hover { background: #69818A; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar { background: #29333F; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #3BCBC4; border-radius: 3px; }
QStatusBar { background: #111318; color: #A7B0BE; border-top: 1px solid #252C36; font-size: 11px; }
QToolTip { background: #252C36; color: #F4F6FA; border: 1px solid #45515F; padding: 6px; }
"""

_chevron = (Path(__file__).parent / "assets" / "chevron-down.svg").as_posix()
STYLESHEET += f'\nQComboBox::down-arrow {{ image: url("{_chevron}"); width: 14px; height: 14px; }}\n'


def feedback_html(notes):
    """Style the existing facts; escape all recording-derived text before HTML."""
    blocks = []
    for paragraph in notes.split("\n\n"):
        lines = paragraph.splitlines()
        if not lines:
            continue
        if lines[0].startswith("AC Racing Coach"):
            blocks.append("<h2>Measured lap feedback</h2>")
            continue
        content = "<br>".join(escape(line) for line in lines)
        if lines[0].startswith(("Next practice focus:", "Largest gains")):
            content = "<h3>" + escape(lines[0]) + "</h3><p>" + "<br>".join(escape(line) for line in lines[1:]) + "</p>"
            blocks.append(content)
        else:
            blocks.append("<p>" + content + "</p>")
    return ("<html><head><style>body{color:#F4F6FA;font-family:'Segoe UI';font-size:14px;}"
            "h2{font-size:23px;margin-bottom:20px;}h3{color:#3BCBC4;font-size:16px;margin-top:24px;}"
            "p{margin-top:10px;margin-bottom:20px;line-height:150%;}</style></head><body>"
            + "".join(blocks) + "</body></html>")
