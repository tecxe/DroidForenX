"""A progress panel that always says how far it has actually got.

The old bars answered "is anything happening?" -- and on the longest path (the
logical ``/sdcard`` stream, which can run for forty minutes) that was all they
answered, with a busy animation instead of a number. This one answers "how far,
how fast, and how long left" in numbers, and it never moves backwards when a
writer retries a chunk.

Two honesty rules are baked in:

* when the total is genuinely unknown the bar goes indeterminate and shows bytes
  written, rather than inventing a denominator;
* the percentage is clamped so it cannot reach 100 before the writer says it is
  done, because a bar that sits on 100% for ten minutes is worse than no bar.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

#: How often the rate is resampled. Writers emit once per megabyte, which on a
#: fast link is dozens of times a second; sampling that raw would make the ETA
#: flicker between "2s" and "3h".
RATE_WINDOW_SECONDS = 0.5


def human_bytes(n: float) -> str:
    """``1536`` -> ``1.5 KB``. Binary units, matching the rest of the app."""
    n = max(0.0, float(n))
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{int(n)} B"


def human_seconds(seconds: float) -> str:
    """``3725`` -> ``1h 02m``."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def percent_of(done: int, total: int) -> Optional[int]:
    """Whole percent, or ``None`` when the total is unknown.

    ``done == total`` is the only case that returns 100. Everything short of
    that is capped at 99 so the number never claims completion early.
    """
    if total <= 0:
        return None
    if done >= total:
        return 100
    return max(0, min(99, int(done * 100 / total)))


class ProgressPanel(QWidget):
    """Progress bar with a stage line and a numeric detail line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.stage_label = QLabel("")
        self.stage_label.setStyleSheet(
            "font-size: 12px; color: #94a3b8; background: transparent; border: none;")
        layout.addWidget(self.stage_label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        layout.addWidget(self.bar)

        self.detail = QLabel("")
        self.detail.setStyleSheet(
            "font-size: 11px; color: #64748b; background: transparent; border: none;")
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)

        self._last_done = 0
        self._sample_done = 0
        self._sample_time = time.monotonic()
        self._rate = 0.0
        self._total = 0
        self._indeterminate = False
        self._finished = False

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        self._last_done = 0
        self._sample_done = 0
        self._sample_time = time.monotonic()
        self._rate = 0.0
        self._total = 0
        self._indeterminate = False
        self._finished = False
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        self.stage_label.setText("")
        self.detail.setText("")

    def set_stage(self, text: str) -> None:
        if text:
            self.stage_label.setText(text)

    def set_indeterminate(self, stage: Optional[str] = None) -> None:
        self._indeterminate = True
        self.bar.setRange(0, 0)
        self.bar.setFormat("")
        if stage:
            self.stage_label.setText(stage)
        self.detail.setText("")

    def finish(self) -> None:
        self._finished = True
        self._indeterminate = False
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.bar.setFormat("%p%")
        total = self._total or self._last_done
        if total:
            self.detail.setText(f"100%  \u00b7  {human_bytes(total)}  \u00b7  done")
        else:
            self.detail.setText("done")
        self.stage_label.setText(self.stage_label.text() or "Complete")

    def stop_with(self, text: str) -> None:
        """Freeze the bar where it stopped and say why."""
        self._finished = True
        self._indeterminate = False
        self.bar.setRange(0, 100)
        self.bar.setFormat("%p%")
        self.stage_label.setText(text)

    # -- the hot path ------------------------------------------------------

    def set_progress(self, done: int, total: int,
                     stage: Optional[str] = None) -> None:
        # A finished run must not be dragged backwards by a late signal from a
        # writer that is still tearing down.
        if self._finished:
            return

        done = max(0, int(done))
        total = max(0, int(total))

        if stage:
            self.stage_label.setText(stage)

        # A writer can report a smaller counter after a retry; the bar must not
        # go backwards or the operator stops trusting it.
        if done < self._last_done:
            done = self._last_done
        # The total is allowed to grow (a tar can outrun its estimate) but never
        # to shrink, which would make the percentage jump forward.
        if total and total < self._total:
            total = self._total
        if total:
            self._total = total

        self._update_rate(done)

        if not total:
            self._indeterminate = True
            self.bar.setRange(0, 0)
            self.bar.setFormat("")
            self.detail.setText(self._bytes_only(done))
            return

        pct = percent_of(done, total)
        self._indeterminate = False
        self.bar.setRange(0, 100)
        self.bar.setValue(pct or 0)
        self.bar.setFormat("%p%")
        self.detail.setText(self._detail_text(done, total, pct or 0))

    # -- internals ---------------------------------------------------------

    def _update_rate(self, done: int) -> None:
        now = time.monotonic()
        elapsed = now - self._sample_time
        if elapsed < RATE_WINDOW_SECONDS:
            self._last_done = max(self._last_done, done)
            return
        instant = max(0.0, (done - self._sample_done) / elapsed)
        # Exponential smoothing: a stall drags the figure down gradually instead
        # of snapping the ETA to infinity on one quiet half-second.
        self._rate = instant if self._rate <= 0 else (0.7 * self._rate + 0.3 * instant)
        self._sample_done = done
        self._sample_time = now
        self._last_done = max(self._last_done, done)

    def _bytes_only(self, done: int) -> str:
        parts = [f"{human_bytes(done)} written"]
        if self._rate > 0:
            parts.append(f"{human_bytes(self._rate)}/s")
        parts.append("size unknown")
        return "  \u00b7  ".join(parts)

    def _detail_text(self, done: int, total: int, pct: int) -> str:
        parts = [f"{pct}%", f"{human_bytes(done)} of {human_bytes(total)}"]
        if self._rate > 0:
            parts.append(f"{human_bytes(self._rate)}/s")
            if done < total:
                parts.append(f"ETA {human_seconds((total - done) / self._rate)}")
        return "  \u00b7  ".join(parts)


__all__ = ["ProgressPanel", "human_bytes", "human_seconds", "percent_of"]
