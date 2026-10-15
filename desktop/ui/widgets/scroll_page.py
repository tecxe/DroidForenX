"""Make an already-built page scrollable.

Several pages build their content straight into ``QVBoxLayout(self)`` with no
``QScrollArea``. A layout with no scroll area has no way to express "the window
got shorter than the content", so Qt's only remaining move is to shrink the
children - which is how log consoles ended up a few lines tall and cards came
out at mismatched heights.

``enable_page_scroll`` lifts the page's existing layout onto a container inside
a ``QScrollArea``. It is deliberately additive: the page does not have to be
rewritten, and the layout object (with its margins, spacings and stretch items)
is reused rather than rebuilt.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget


def enable_page_scroll(page: QWidget, margins=(0, 0, 0, 0)) -> QScrollArea | None:
    """Wrap ``page``'s existing top-level layout in a ``QScrollArea``.

    Returns the scroll area, or ``None`` if the page had no layout to wrap.
    Safe to call once per page, at the end of its ``_setup_ui``.
    """
    layout = page.layout()
    if layout is None:
        return None

    # Re-homing the layout detaches it from the page and attaches it to the
    # container in one step; the layout object and its contents are untouched.
    container = QWidget()
    container.setLayout(layout)

    scroll = QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    scroll.setWidget(container)

    outer = QVBoxLayout(page)
    outer.setContentsMargins(*margins)
    outer.setSpacing(0)
    outer.addWidget(scroll)
    return scroll