"""Native popups for composer controls.

The composer surface is rendered by QWebEngine, whose document is clipped to
its QWidget viewport.  Picker menus therefore live in a native Qt popup so
they can layer above the graph and the composer without resizing or overflowing
the web surface.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PICKER_MIN_WIDTH = 380
PICKER_MAX_WIDTH = 440
PICKER_LIST_MAX_HEIGHT = 300
PICKER_ROW_MIN_HEIGHT = 50


def composer_picker_position(
    viewport_rect: QRect,
    composer_rect: QRect,
    popup_size: QSize,
    margin: int = 8,
) -> QPoint:
    """Return a global popup position that stays inside the graph viewport.

    All rectangles are expected to be in global screen coordinates. The picker
    prefers the space above the composer, then below it, and finally clamps to
    the viewport when neither side has enough room.
    """
    margin = max(0, int(margin))
    popup_width = max(0, int(popup_size.width()))
    popup_height = max(0, int(popup_size.height()))

    min_x = viewport_rect.left() + margin
    max_x = viewport_rect.right() - popup_width + 1 - margin
    anchor_x = composer_rect.right() - popup_width + 1 - 10
    x = min(max(min_x, anchor_x), max_x) if max_x >= min_x else min_x

    min_y = viewport_rect.top() + margin
    max_y = viewport_rect.bottom() - popup_height + 1 - margin
    above_y = composer_rect.top() - popup_height - margin
    below_y = composer_rect.bottom() + 1 + margin
    if above_y >= min_y:
        y = above_y
    elif below_y <= max_y:
        y = below_y
    else:
        y = min(max(min_y, above_y), max_y) if max_y >= min_y else min_y

    return QPoint(int(x), int(y))


def composer_picker_list_height(
    item_heights: list[int],
    spacing: int = 4,
    max_height: int = PICKER_LIST_MAX_HEIGHT,
) -> int:
    """Return a content-sized list height with a bounded scrolling ceiling."""
    heights = [max(0, int(height)) for height in item_heights]
    if not heights:
        return 0
    # Account for list viewport padding and the frame around it so a small
    # reasoning catalog does not display an unnecessary scrollbar.
    content_height = sum(heights) + max(0, len(heights) - 1) * max(0, int(spacing)) + 16
    return min(max(0, int(max_height)), content_height)


class _PickerRow(QWidget):
    """Accessible two-line option row used inside the native list."""

    def __init__(self, label: str, meta: str, current: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("composerPickerRow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)
        self.setMinimumHeight(PICKER_ROW_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        copy_layout = QVBoxLayout()
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(2)

        label_widget = QLabel(label, self)
        label_widget.setObjectName("pickerOptionLabel")
        label_widget.setWordWrap(False)
        label_widget.setMinimumWidth(0)
        label_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label_widget.setToolTip(label)
        label_widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        copy_layout.addWidget(label_widget)

        meta_widget = QLabel(meta, self)
        meta_widget.setObjectName("pickerOptionMeta")
        meta_widget.setWordWrap(False)
        meta_widget.setMinimumWidth(0)
        meta_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta_widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        copy_layout.addWidget(meta_widget)

        layout.addLayout(copy_layout, 1)

        if current:
            current_widget = QLabel("Current", self)
            current_widget.setObjectName("pickerCurrentBadge")
            current_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            current_widget.setFixedSize(60, 22)
            current_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            current_widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(
                current_widget,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )


class ComposerPickerPopup(QFrame):
    """A single native popup shared by model and reasoning selectors."""

    modelSelected = Signal(str)
    reasoningSelected = Signal(str)
    settingsRequested = Signal()

    def __init__(self, kind: str, route: dict[str, Any], parent=None):
        super().__init__(parent)
        self.kind = "reasoning" if kind == "reasoning" else "model"
        self.route = route if isinstance(route, dict) else {}
        self._options: list[dict[str, Any]] = []

        self.setObjectName("composerPicker")
        # Qt.Popup grabs the native popup surface and, with QWebEngine in the
        # owner window, can trigger a black repaint of the underlying GPU
        # surfaces while the menu is open. An owned tool window preserves the
        # z-order without engaging that popup modality; the app event filter
        # below supplies the expected outside-click dismissal.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._application = QApplication.instance()
        self._event_filter_installed = False
        self.setMinimumWidth(PICKER_MIN_WIDTH)
        self.setMaximumWidth(PICKER_MAX_WIDTH)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        frame = QFrame(self)
        frame.setObjectName("composerPickerFrame")
        outer_layout.addWidget(frame)

        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(16, 14, 16, 14)
        frame_layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)

        heading_copy = QVBoxLayout()
        heading_copy.setContentsMargins(0, 0, 0, 0)
        heading_copy.setSpacing(4)

        eyebrow = QLabel("Model" if self.kind == "model" else "Reasoning", frame)
        eyebrow.setObjectName("pickerEyebrow")
        heading_copy.addWidget(eyebrow)

        title = QLabel(
            str(self.route.get("provider") or "Choose a model")
            if self.kind == "model"
            else "Choose response depth",
            frame,
        )
        title.setObjectName("pickerTitle")
        heading_copy.addWidget(title)
        heading.addLayout(heading_copy, 1)

        close_button = QPushButton("x", frame)
        close_button.setObjectName("pickerCloseButton")
        close_button.setFixedSize(25, 25)
        close_button.setAccessibleName("Close selector")
        close_button.clicked.connect(self.close)
        heading.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        frame_layout.addLayout(heading)

        self.search = None
        if self.kind == "model":
            self.search = QLineEdit(frame)
            self.search.setObjectName("pickerSearch")
            self.search.setFixedHeight(36)
            self.search.setPlaceholderText("Search available models")
            self.search.setClearButtonEnabled(True)
            self.search.textChanged.connect(self._refresh_options)
            self.search.installEventFilter(self)
            frame_layout.addWidget(self.search)

        self.list_frame = QFrame(frame)
        self.list_frame.setObjectName("composerPickerListFrame")
        list_frame_layout = QVBoxLayout(self.list_frame)
        list_frame_layout.setContentsMargins(6, 6, 6, 6)
        list_frame_layout.setSpacing(0)

        self.list_widget = QListWidget(self.list_frame)
        self.list_widget.setObjectName("composerPickerList")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.list_widget.setSpacing(4)
        self.list_widget.setMinimumHeight(0)
        self.list_widget.itemClicked.connect(self._commit_item)
        self.list_widget.itemActivated.connect(self._commit_item)
        list_frame_layout.addWidget(self.list_widget)
        frame_layout.addWidget(self.list_frame)

        self.empty_label = QLabel("", frame)
        self.empty_label.setObjectName("pickerEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        frame_layout.addWidget(self.empty_label)

        self.settings_button = None
        if self.kind == "model":
            self.settings_button = QPushButton("Open Settings to discover models", frame)
            self.settings_button.setObjectName("pickerSettingsButton")
            self.settings_button.clicked.connect(self._request_settings)
            self.settings_button.hide()
            frame_layout.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignLeft)

        self._refresh_options("")
        self.setStyleSheet(
            """
            QFrame#composerPickerFrame {
                background: #20262e;
                border: 1px solid rgba(143, 161, 184, 0.52);
                border-radius: 12px;
            }
            QFrame#composerPickerListFrame {
                background: #151a20;
                border: 1px solid rgba(143, 161, 184, 0.20);
                border-radius: 10px;
            }
            QWidget#composerPickerRow {
                background: transparent;
            }
            QListWidget#composerPickerList::item {
                background: transparent;
            }
            QLabel#pickerEyebrow {
                color: #8e9db0;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#pickerTitle {
                color: #eaf0f8;
                font-size: 11px;
                font-weight: 650;
            }
            QPushButton#pickerCloseButton {
                color: #9aa8ba;
                background: transparent;
                border: 0;
                border-radius: 7px;
                font-size: 17px;
            }
            QPushButton#pickerCloseButton:hover {
                color: #f0f4fa;
                background: rgba(255, 255, 255, 0.08);
            }
            QLineEdit#pickerSearch {
                color: #eef3fa;
                background: #171c22;
                border: 1px solid rgba(143, 161, 184, 0.32);
                border-radius: 7px;
                padding: 0 11px;
                selection-background-color: #455d83;
            }
            QLineEdit#pickerSearch:focus {
                border-color: #7e9eee;
            }
            QListWidget#composerPickerList {
                color: #cad4e0;
                background: transparent;
                border: 0;
                outline: 0;
                padding: 2px 8px 2px 2px;
            }
            QListWidget#composerPickerList::item {
                border-radius: 7px;
                padding: 0;
            }
            QListWidget#composerPickerList::item:hover {
                background: rgba(112, 136, 159, 0.16);
            }
            QListWidget#composerPickerList::item:selected {
                background: rgba(112, 136, 159, 0.24);
                border: 1px solid rgba(126, 158, 238, 0.38);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 6px 1px 6px 4px;
            }
            QScrollBar::handle:vertical {
                background: #526177;
                min-height: 28px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #6b7b94;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QLabel#pickerOptionLabel {
                color: #e7edf5;
                background: transparent;
                font-size: 11px;
                font-weight: 650;
            }
            QLabel#pickerOptionMeta {
                color: #8492a5;
                background: transparent;
                font-size: 9px;
            }
            QLabel#pickerCurrentBadge {
                color: #d6e3ff;
                background: rgba(126, 158, 238, 0.16);
                border: 1px solid rgba(126, 158, 238, 0.34);
                border-radius: 6px;
                padding: 0 7px;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#pickerEmptyLabel {
                color: #8492a5;
                font-size: 10px;
                padding: 12px 8px;
            }
            QPushButton#pickerSettingsButton {
                color: #a9c0ff;
                background: transparent;
                border: 0;
                padding: 0 8px;
                font-size: 10px;
                font-weight: 650;
            }
            QPushButton#pickerSettingsButton:hover {
                color: #d1ddff;
            }
            """
        )

    def _raw_options(self) -> list[dict[str, Any]]:
        if self.kind == "model":
            values = self.route.get("modelOptions", [])
        else:
            reasoning = self.route.get("reasoning", {})
            values = reasoning.get("options", []) if isinstance(reasoning, dict) else []
        return [dict(value) for value in values if isinstance(value, dict)]

    def _refresh_options(self, query: str = ""):
        self._options = self._raw_options()
        query = str(query or "").strip().lower()
        self.list_widget.clear()

        active_id = ""
        if self.kind == "model":
            active_id = str(self.route.get("modelId") or "").strip()
        else:
            reasoning = self.route.get("reasoning", {})
            active_id = str(reasoning.get("level") or "").strip() if isinstance(reasoning, dict) else ""

        enabled_items = []
        for option in self._options:
            option_id = str(option.get("id") or "").strip()
            label = str(option.get("label") or option_id or "Option").strip()
            if query and query not in label.lower() and query not in option_id.lower():
                continue

            is_active = bool(option.get("active")) or option_id == active_id
            ready = bool(option.get("ready", True))
            available = bool(option.get("available", True))
            unavailable = self.kind == "model" and (
                not available or (not ready and not is_active)
            )
            if self.kind == "model":
                meta = "Selected" if is_active else (
                    "Installed" if option.get("source") == "installed" else "Available"
                )
                if not ready:
                    meta += " - verify in Settings"
            else:
                meta = str(option.get("description") or "").strip()

            item = QListWidgetItem(self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, option_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, unavailable)
            if unavailable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            row = _PickerRow(label, meta, is_active, self.list_widget)
            item.setSizeHint(row.sizeHint())
            self.list_widget.setItemWidget(item, row)
            if not unavailable:
                enabled_items.append(item)

        if enabled_items:
            active_item = next(
                (item for item in enabled_items if item.data(Qt.ItemDataRole.UserRole) == active_id),
                enabled_items[0],
            )
            active_item.setSelected(True)
            self.list_widget.setCurrentItem(active_item)

        has_options = self.list_widget.count() > 0
        self.list_frame.setVisible(has_options)
        self.empty_label.setVisible(not has_options)
        if not has_options:
            self.empty_label.setText(
                "No model matches this search."
                if query and self.kind == "model"
                else "No model catalog available yet."
                if self.kind == "model"
                else "No reasoning levels are available for this provider."
            )
        if self.settings_button is not None:
            show_settings = self.kind == "model" and not query and not has_options
            self.settings_button.setVisible(show_settings)

        self.list_widget.setFixedHeight(
            composer_picker_list_height(
                [
                    self.list_widget.item(index).sizeHint().height()
                    for index in range(self.list_widget.count())
                ]
            )
        )
        self.adjustSize()
        self._request_reposition()

    def _request_reposition(self):
        """Re-clamp the top-level surface after filtering changes its size."""
        QTimer.singleShot(0, self._reposition_owner)

    def _reposition_owner(self):
        if not self.isVisible():
            return
        reposition = getattr(self.parentWidget(), "_position_composer_picker", None)
        if callable(reposition):
            reposition()

    def _commit_item(self, item: QListWidgetItem | None):
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        value = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not value:
            return
        if self.kind == "model":
            self.modelSelected.emit(value)
        else:
            self.reasoningSelected.emit(value)
        self.close()

    def _request_settings(self):
        self.settingsRequested.emit()
        self.close()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            global_position = event.globalPosition().toPoint()
            if not self.rect().contains(self.mapFromGlobal(global_position)):
                self.close()
                return False
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.list_widget.setFocus()
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self._application is not None and self._event_filter_installed:
            self._application.removeEventFilter(self)
            self._event_filter_installed = False
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._application is not None and not self._event_filter_installed:
            QTimer.singleShot(0, self._install_event_filter)
        QTimer.singleShot(0, self._focus_default)

    def _install_event_filter(self):
        if self.isVisible() and self._application is not None and not self._event_filter_installed:
            self._application.installEventFilter(self)
            self._event_filter_installed = True

    def _focus_default(self):
        if self.search is not None:
            self.search.setFocus(Qt.FocusReason.PopupFocusReason)
        else:
            self.list_widget.setFocus(Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


class _ContextRow(QWidget):
    """Accessible native context row used by the window-level review popup."""

    removed = Signal()

    def __init__(self, kind: str, label: str, removable: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("composerContextRow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(12)

        kind_label = QLabel(kind, self)
        kind_label.setObjectName("composerContextKind")
        kind_label.setMinimumWidth(58)
        kind_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout.addWidget(kind_label, 0, Qt.AlignmentFlag.AlignTop)

        name_label = QLabel(label, self)
        name_label.setObjectName("composerContextName")
        name_label.setWordWrap(True)
        name_label.setToolTip(label)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(name_label, 1, Qt.AlignmentFlag.AlignTop)

        if removable:
            remove_button = QPushButton("Remove", self)
            remove_button.setObjectName("composerContextRemove")
            remove_button.setFixedHeight(26)
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.clicked.connect(self.removed)
            layout.addWidget(remove_button, 0, Qt.AlignmentFlag.AlignTop)


class ComposerContextPopup(QFrame):
    """Native, unclipped context review surface for the React composer."""

    contextItemRemoved = Signal(str)

    def __init__(self, context: dict[str, Any], parent=None):
        super().__init__(parent)
        self.context = context if isinstance(context, dict) else {}
        self.setObjectName("composerContextPopup")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._application = QApplication.instance()
        self._event_filter_installed = False
        self.setMinimumWidth(400)
        self.setMaximumWidth(520)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        frame = QFrame(self)
        frame.setObjectName("composerContextFrame")
        outer_layout.addWidget(frame)

        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(16, 14, 16, 14)
        frame_layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)
        heading_copy = QVBoxLayout()
        heading_copy.setContentsMargins(0, 0, 0, 0)
        heading_copy.setSpacing(4)

        eyebrow = QLabel("CONTEXT", frame)
        eyebrow.setObjectName("composerContextEyebrow")
        heading_copy.addWidget(eyebrow)
        title = QLabel("Included context", frame)
        title.setObjectName("composerContextTitle")
        heading_copy.addWidget(title)
        heading.addLayout(heading_copy, 1)

        close_button = QPushButton("x", frame)
        close_button.setObjectName("composerContextClose")
        close_button.setFixedSize(25, 25)
        close_button.setAccessibleName("Close context review")
        close_button.clicked.connect(self.close)
        heading.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        frame_layout.addLayout(heading)

        self.list_frame = QFrame(frame)
        self.list_frame.setObjectName("composerContextListFrame")
        list_layout = QVBoxLayout(self.list_frame)
        list_layout.setContentsMargins(6, 6, 6, 6)
        list_layout.setSpacing(4)
        self.list_widget = QListWidget(self.list_frame)
        self.list_widget.setObjectName("composerContextList")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.list_widget.setSpacing(4)
        list_layout.addWidget(self.list_widget)
        frame_layout.addWidget(self.list_frame)

        self.total_label = QLabel("", frame)
        self.total_label.setObjectName("composerContextTotal")
        frame_layout.addWidget(self.total_label)

        self.setStyleSheet(
            """
            QFrame#composerContextFrame {
                background: #20262e;
                border: 1px solid rgba(143, 161, 184, 0.52);
                border-radius: 12px;
            }
            QFrame#composerContextListFrame {
                background: #151a20;
                border: 1px solid rgba(143, 161, 184, 0.20);
                border-radius: 10px;
            }
            QListWidget#composerContextList {
                color: #cad4e0;
                background: transparent;
                border: 0;
                outline: 0;
                padding: 2px 8px 2px 2px;
            }
            QWidget#composerContextRow {
                background: rgba(112, 136, 159, 0.08);
                border: 1px solid rgba(143, 161, 184, 0.12);
                border-radius: 8px;
            }
            QLabel#composerContextEyebrow {
                color: #8e9db0;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#composerContextTitle {
                color: #eaf0f8;
                font-size: 12px;
                font-weight: 650;
            }
            QLabel#composerContextKind {
                color: #8e9db0;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#composerContextName {
                color: #e7edf5;
                font-size: 11px;
            }
            QLabel#composerContextTotal {
                color: #8492a5;
                font-size: 10px;
                padding: 0 4px;
            }
            QPushButton#composerContextClose {
                color: #9aa8ba;
                background: transparent;
                border: 0;
                border-radius: 7px;
                font-size: 17px;
            }
            QPushButton#composerContextClose:hover {
                color: #f0f4fa;
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton#composerContextRemove {
                color: #b9c8df;
                background: #252d39;
                border: 1px solid #3a4657;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 9px;
                font-weight: 650;
            }
            QPushButton#composerContextRemove:hover {
                color: #f2f6ff;
                background: #34445a;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 6px 1px 6px 4px;
            }
            QScrollBar::handle:vertical {
                background: #526177;
                min-height: 28px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                height: 0;
                background: transparent;
            }
            """
        )
        self._populate()

    def _populate(self):
        self.list_widget.clear()
        rows: list[tuple[str, str, bool, str]] = []
        anchor = self.context.get("anchor")
        if isinstance(anchor, dict) and anchor.get("label"):
            rows.append((str(anchor.get("type") or "Graph"), str(anchor["label"]), False, ""))
        for item in self.context.get("items", []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            item_id = str(item.get("id") or "")
            rows.append((str(item.get("kind") or "Context"), str(item["name"]), bool(item_id), item_id))

        for kind, label, removable, item_id in rows:
            list_item = QListWidgetItem(self.list_widget)
            row = _ContextRow(kind, label, removable, self.list_widget)
            if removable:
                row.removed.connect(lambda item_id=item_id: self._remove_item(item_id))
            list_item.setSizeHint(row.sizeHint())
            self.list_widget.setItemWidget(list_item, row)

        self.list_frame.setVisible(bool(rows))
        self.total_label.setText(
            f"Estimated context · {int(self.context.get('totalTokens') or 0):,} tokens"
        )
        self.list_widget.setFixedHeight(
            composer_picker_list_height(
                [self.list_widget.item(index).sizeHint().height() for index in range(self.list_widget.count())],
                spacing=4,
                max_height=280,
            )
        )
        self.adjustSize()
        self._request_reposition()

    def _remove_item(self, item_id: str):
        if item_id:
            self.contextItemRemoved.emit(item_id)
        self.close()

    def _request_reposition(self):
        QTimer.singleShot(0, self._reposition_owner)

    def _reposition_owner(self):
        if not self.isVisible():
            return
        reposition = getattr(self.parentWidget(), "_position_composer_context_popup", None)
        if callable(reposition):
            reposition()

    def showEvent(self, event):
        super().showEvent(event)
        if self._application is not None and not self._event_filter_installed:
            QTimer.singleShot(0, self._install_event_filter)
        QTimer.singleShot(0, self.setFocus)

    def _install_event_filter(self):
        if self.isVisible() and self._application is not None and not self._event_filter_installed:
            self._application.installEventFilter(self)
            self._event_filter_installed = True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            global_position = event.globalPosition().toPoint()
            if not self.rect().contains(self.mapFromGlobal(global_position)):
                self.close()
                return False
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self._application is not None and self._event_filter_installed:
            self._application.removeEventFilter(self)
            self._event_filter_installed = False
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
