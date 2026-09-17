"""The Data viewer window: the running acquisition on screen as it is written.

    python -m mesospim_view.window /path/to/data

A dropdown of the acquisitions in a data folder, newest first, and the viewer
below it. The newest acquisition is followed automatically -- a new one
appearing while the window is open is switched to, and every tile or time
point that lands in it is shown within a second -- unless an older one was
picked from the dropdown, which stays until *Latest* is pressed. The
following itself is :class:`~mesospim_view.watch.Follower`; this file only
binds it to Qt.

Written against the Qt binding mesoSPIM-control uses (PyQt5), through the same
small helper the plain widget uses, so PyQt6 and PySide work as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .viewer import Viewer, _qt
from .watch import Follower

POLL_MS = 1000


def make_window_class():
    """The window class, built once Qt is known to be importable."""
    qt = _qt()
    QtCore, QtWidgets = qt.QtCore, qt.QtWidgets

    class DataViewerWindow(QtWidgets.QWidget):
        """A dropdown of acquisitions over the viewer, following the newest."""

        def __init__(self, root: str | Path, parent=None, *, viewer: Viewer | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Data viewer")
            self.follower = Follower(viewer or Viewer(ui="simple"), root)

            self.chooser = QtWidgets.QComboBox()
            self.chooser.setMinimumWidth(240)
            self.chooser.activated.connect(self._chosen)
            self.latest = QtWidgets.QPushButton("Latest")
            self.latest.setToolTip("Follow the newest acquisition again")
            self.latest.clicked.connect(self.follow_latest)
            self.folder = QtWidgets.QLabel(str(self.follower.root))
            self.folder.setToolTip("The folder being watched")

            bar = QtWidgets.QHBoxLayout()
            bar.addWidget(QtWidgets.QLabel("Acquisition"))
            bar.addWidget(self.chooser, 1)
            bar.addWidget(self.latest)
            bar.addWidget(self.folder, 2)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.addLayout(bar)
            layout.addWidget(self.viewer.qt_widget(self), 1)

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.poll)
            self.timer.start(POLL_MS)
            self.poll()

        @property
        def viewer(self) -> Viewer:
            return self.follower.viewer

        def poll(self) -> None:
            if self.follower.poll():
                self._relist()
            self._point_at_shown()

        def follow_latest(self) -> None:
            self.follower.follow_latest()
            self._point_at_shown()

        def _chosen(self, index: int) -> None:
            self.follower.choose(index)

        def _relist(self) -> None:
            self.chooser.blockSignals(True)
            self.chooser.clear()
            for name in self.follower.names:
                self.chooser.addItem(name)
            self.chooser.blockSignals(False)

        def _point_at_shown(self) -> None:
            at = self.follower.shown_index
            if at >= 0 and self.chooser.currentIndex() != at:
                self.chooser.setCurrentIndex(at)

        def closeEvent(self, event) -> None:  # noqa: N802 -- Qt's name
            self.timer.stop()
            self.viewer.stop()
            super().closeEvent(event)

    return DataViewerWindow


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m mesospim_view.window <data folder>")
        return 2
    qt = _qt()
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication(sys.argv)
    window = make_window_class()(argv[0])
    window.resize(1200, 800)
    window.show()
    return app.exec() if hasattr(app, "exec") else app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
