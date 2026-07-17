"""Compatibility exports for legacy dialog imports.

The live implementations now live with their owning features. Keeping this small
shim prevents external integrations from importing duplicate dialog classes.
"""

from graphlink_canvas.graphlink_canvas_dialogs import ColorPickerDialog
from graphlink_widgets.pins import NavigationPinEditor as PinEditDialog

__all__ = ["ColorPickerDialog", "PinEditDialog"]
