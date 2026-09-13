bl_info = {
    "name": "B Hair ShapeKey",
    "author": "Dinesh007",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "Properties > Data Properties > Shape Keys",
    "description": "Direct Hair Curves Shape Keys with Drivers, Keyframing",
    "category": "Curves",
}

if "bpy" in locals():
    import importlib
    core = importlib.reload(core)
    sculpt = importlib.reload(sculpt)
    properties = importlib.reload(properties)
    operators = importlib.reload(operators)
    ui = importlib.reload(ui)
    handlers = importlib.reload(handlers)
else:
    from . import core, sculpt, properties, operators, ui, handlers

import bpy

from .core import _eval_state_cache
from .properties import (
    PROPERTY_CLASSES,
    HairShapeKeyData,
    _ensure_dynamic_range_callback,
    cleanup_dynamic_range_callback,
)
from .operators import OPERATOR_CLASSES
from .ui import UI_CLASSES
from .handlers import register_handlers, unregister_handlers


classes = (
    *PROPERTY_CLASSES,
    *OPERATOR_CLASSES,
    *UI_CLASSES,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    bpy.types.Object.hair_shape_keys = bpy.props.PointerProperty(type=HairShapeKeyData)
    _ensure_dynamic_range_callback()
    register_handlers()


def unregister():
    _eval_state_cache.clear()
    cleanup_dynamic_range_callback()
    unregister_handlers()

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    if hasattr(bpy.types.Object, "hair_shape_keys"):
        del bpy.types.Object.hair_shape_keys


if __name__ == "__main__":
    register()
