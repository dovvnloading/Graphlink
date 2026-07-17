"""Unified, accessible Composer shell for graph conversations."""

from __future__ import annotations

import os

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from graphlink_config import get_current_palette
from .text_inputs import ChatInputTextEdit


class ComposerWidget(QFrame):
    """The single interaction surface for composing a graph request.

    The widget deliberately keeps the existing ``ChatInputTextEdit`` contract
    so the window can migrate incrementally, while making context, routing,
    request state, and recovery affordances visible in one place.
    """

    sendRequested = Signal()
    textChanged = Signal(str)
    attachRequested = Signal()
    filesDropped = Signal(list)
    textDropped = Signal(str)
    attachmentRemoved = Signal(str)
    largePasteDetected = Signal(str)
    composerHeightChanged = Signal(int)
    contextReviewRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composerShell")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._context_anchor = None
        self._request_active = False
        self._request_message = ""
        self._attachments = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        header.setSpacing(8)
        self.context_label = QLabel("New graph request", self)
        self.context_label.setObjectName("composerContextLabel")
        self.context_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.context_label.setAccessibleName("Graph context")
        header.addWidget(self.context_label)

        self.context_review_button = QPushButton("Review context", self)
        self.context_review_button.setObjectName("composerSecondaryButton")
        self.context_review_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_review_button.setAccessibleName("Review graph context")
        self.context_review_button.setToolTip("Review which graph context will be sent")
        self.context_review_button.clicked.connect(self.contextReviewRequested.emit)
        header.addWidget(self.context_review_button)
        root.addLayout(header)

        self.message_input = ChatInputTextEdit(self)
        self.message_input.setPlaceholderText("Ask about this graph…")
        self.message_input.sendRequested.connect(self.sendRequested.emit)
        self.message_input.largePasteDetected.connect(self.largePasteDetected.emit)
        self.message_input.filesDropped.connect(self.filesDropped.emit)
        self.message_input.textDropped.connect(self.textDropped.emit)
        self.message_input.attachmentRemoved.connect(self.attachmentRemoved.emit)
        self.message_input.editor.textChanged.connect(lambda: self.textChanged.emit(self.text()))
        self.message_input.composerHeightChanged.connect(self._sync_height)
        self.message_input.setAccessibleName("Message composer")
        root.addWidget(self.message_input)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.attach_file_btn = QPushButton(self)
        self.attach_file_btn.setObjectName("composerAttachButton")
        self.attach_file_btn.setIcon(qta.icon("fa5s.paperclip", color="#D5D5D5"))
        self.attach_file_btn.setFixedHeight(34)
        self.attach_file_btn.setMinimumWidth(34)
        self.attach_file_btn.setAccessibleName("Attach context")
        self.attach_file_btn.setToolTip("Attach images, audio, or readable files")
        self.attach_file_btn.clicked.connect(self.attachRequested.emit)
        action_row.addWidget(self.attach_file_btn)

        self.context_summary = QLabel("No attachments", self)
        self.context_summary.setObjectName("composerMetaLabel")
        self.context_summary.setAccessibleName("Attachment summary")
        action_row.addWidget(self.context_summary)

        action_row.addStretch(1)
        self.provider_status = QLabel("Active provider route", self)
        self.provider_status.setObjectName("composerMetaLabel")
        self.provider_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.provider_status.setAccessibleName("Model route")
        action_row.addWidget(self.provider_status)

        self.send_button = QPushButton(self)
        self.send_button.setObjectName("composerSendButton")
        self.send_button.setIcon(qta.icon("fa5s.paper-plane", color="#FFFFFF"))
        self.send_button.setFixedSize(42, 34)
        self.send_button.setAccessibleName("Send message")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.sendRequested.emit)
        action_row.addWidget(self.send_button)
        root.addLayout(action_row)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("composerStatusLabel")
        self.status_label.setVisible(False)
        self.status_label.setAccessibleName("Request status")
        root.addWidget(self.status_label)

        self._apply_styles()
        self._sync_height()

    # Compatibility surface used by the existing ChatWindow and actions mixin.
    def text(self):
        return self.message_input.text()

    def setText(self, text):
        self.message_input.setText(text)

    def clear(self):
        self.message_input.clear()

    def insertPlainText(self, text):
        self.message_input.insertPlainText(text)

    def setPlaceholderText(self, text):
        self.message_input.setPlaceholderText(text)

    def setFocus(self):
        self.message_input.setFocus()

    def focusWidget(self):
        return self.message_input.focusWidget()

    def on_theme_changed(self):
        self.message_input.on_theme_changed()
        self._apply_styles()

    def set_context_items(self, items):
        self._attachments = list(items or [])
        self.message_input.set_context_items(self._attachments)
        self._update_context_summary()

    def set_context_anchor(self, node):
        self._context_anchor = node
        if node is None:
            label = "New graph request"
        else:
            label = getattr(node, "title", None) or getattr(node, "text", None) or type(node).__name__
            label = " ".join(str(label).split())
            if len(label) > 46:
                label = f"{label[:43]}…"
            label = f"Responding to {label}"
        self.context_label.setText(label)
        self.context_review_button.setEnabled(node is not None or bool(self._attachments))

    def set_provider_status(self, text, tooltip=""):
        self.provider_status.setText(str(text or "Active provider route"))
        self.provider_status.setToolTip(str(tooltip or text or ""))

    def set_request_state(self, active=False, cancel_pending=False, message=""):
        self._request_active = bool(active)
        self._request_message = str(message or "")
        self.send_button.setEnabled(not cancel_pending)
        self.attach_file_btn.setEnabled(not active)
        self.message_input.setEnabled(not active)
        self.status_label.setVisible(bool(active or message))
        self.status_label.setText(self._request_message)
        self.status_label.setProperty("requestActive", bool(active))
        self._apply_styles()

    def set_editor_enabled(self, enabled):
        self.message_input.setEnabled(bool(enabled))

    def setEnabled(self, enabled):
        # Preserve QWidget semantics for callers that intentionally disable the
        # whole shell, while request lifecycle code should use set_editor_enabled.
        super().setEnabled(enabled)

    def _update_context_summary(self):
        count = len(self._attachments)
        if not count:
            self.context_summary.setText("No attachments")
        elif count == 1:
            item = self._attachments[0]
            self.context_summary.setText(f"1 attachment · {item.get('name', 'file')}")
        else:
            self.context_summary.setText(f"{count} attachments ready")
        self.context_review_button.setEnabled(self._context_anchor is not None or count > 0)

    def _sync_height(self, *_):
        self.adjustSize()
        self.updateGeometry()
        self.composerHeightChanged.emit(self.sizeHint().height())

    def _apply_styles(self):
        palette = get_current_palette()
        selection = palette.SELECTION.name()
        self.setStyleSheet(f"""
            QFrame#composerShell {{
                background-color: rgba(34, 34, 34, 245);
                border: 1px solid rgba(143, 143, 143, 0.34);
                border-radius: 14px;
            }}
            QLabel#composerContextLabel {{
                color: #F4F4F4;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#composerMetaLabel {{
                color: #A4A4A4;
                font-size: 11px;
            }}
            QLabel#composerStatusLabel {{
                color: #CFCFCF;
                font-size: 11px;
                padding: 2px 4px;
            }}
            QPushButton#composerSecondaryButton, QPushButton#composerAttachButton {{
                color: #D5D5D5;
                background: transparent;
                border: 1px solid rgba(162, 162, 162, 0.34);
                border-radius: 8px;
                padding: 5px 9px;
            }}
            QPushButton#composerSecondaryButton:hover, QPushButton#composerAttachButton:hover {{
                color: #FFFFFF;
                border-color: {selection};
                background: rgba(255, 255, 255, 0.06);
            }}
            QPushButton#composerSendButton {{
                color: #FFFFFF;
                background: {selection};
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 9px;
                padding: 5px;
            }}
            QPushButton#composerSendButton:hover {{
                background: {QColor(palette.SELECTION).lighter(115).name()};
            }}
            QPushButton#composerSendButton:disabled {{
                background: rgba(109, 109, 109, 0.55);
            }}
        """)
