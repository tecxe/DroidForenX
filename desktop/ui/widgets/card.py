"""Reusable card frame that keeps its natural height.

Cards used to be inline ``QFrame``s with default size policies. A default
``QFrame`` under a vertical layout is ``Preferred``/``Preferred``, which means
it is freely shrinkable. Inside a ``QScrollArea`` built with
``setWidgetResizable(True)`` the container is forced down to the viewport
height in both axes, the trailing ``addStretch()`` collapses to nothing, and
the layout then takes the remaining deficit out of the child widgets in
proportion to the slack each one happened to have.

That is why the cards did not all shrink by the same amount: a card holding
two labels has a lot of slack and collapses hard, while a card holding a grid
of fixed-height buttons has almost none and barely moves. The visible symptom
was cards of mismatched heights with clipped text, appearing only when the
window was short.

``Card`` pins the vertical policy to ``Fixed`` and reports its full natural
height as its minimum, so the page keeps its natural size and the scroll area
scrolls instead of compressing the cards.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QFrame, QSizePolicy

#: Must stay in step with the ``padding`` written into the card stylesheet.
CARD_PADDING = 20

_SURFACE = "background: #111827; border: 1px solid #1e293b;"


class Card(QFrame):
    """A ``QFrame`` that will not be squashed by its parent layout.

    Args:
        accent: optional colour for a 3px left border.
        radius: corner radius in pixels.
        padding: inner padding in pixels; also added to the reported height.
    """

    def __init__(self, *, accent: str | None = None, radius: int = 8,
                 padding: int = CARD_PADDING, parent=None):
        super().__init__(parent)
        self._padding = padding
        # QVariant round-trip: setProperty stores, property() retrieves.
        self.setProperty("accent", accent)
        self.setProperty("radius", radius)
        # Fixed vertically = keep natural height. Preferred horizontally = still
        # share the row's width with its siblings.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.apply_style()

    def apply_style(self) -> None:
        accent = self.property("accent")
        radius = self.property("radius")
        left = f" border-left: 3px solid {accent};" if accent else ""
        self.setStyleSheet(
            f"QFrame {{ {_SURFACE}{left} "
            f"border-radius: {radius}px; padding: {self._padding}px; }}"
        )

    def sizeHint(self) -> QSize:
        # Qt does not fold stylesheet padding into the size that a layout
        # computes for a QFrame, so it is added back here. Without this the
        # fixed height would clip the content by the padding on each edge.
        base = super().sizeHint()
        return QSize(base.width(), base.height() + self._padding)

    def minimumSizeHint(self) -> QSize:
        # Equal to sizeHint, so the layout can never shrink the card below the
        # space its contents actually need.
        return self.sizeHint()