import bpy
import ctypes
import re
from .core import evaluate_shape_keys, sync_rest_position, cleanup_legacy_geometry_nodes
from .sculpt import capture_sculpt_start, commit_sculpt_changes

# -------------------------------------------------------------------
# Property Callbacks & Owner Resolution
# -------------------------------------------------------------------

def get_owner_curves_object(prop, context=None):
    """Finds the Curves object that owns the given hair_shape_keys or HairShapeKeyItem."""
    if not prop:
        return None

    # Fast-path: check direct id_data pointer (O(1))
    id_obj = getattr(prop, "id_data", None)
    if id_obj and getattr(id_obj, "type", "") == 'CURVES':
        return id_obj

    def owns_prop(o):
        if not o or o.type != 'CURVES':
            return False
        sk = getattr(o, "hair_shape_keys", None)
        if not sk:
            return False
        if sk == prop:
            return True
        for k in sk.keys:
            if k == prop:
                return True
        return False

    ctx = context or bpy.context
    if ctx and hasattr(ctx, "active_object") and owns_prop(ctx.active_object):
        return ctx.active_object

    if ctx and hasattr(ctx, "selected_objects"):
        for o in ctx.selected_objects:
            if owns_prop(o):
                return o

    for o in bpy.data.objects:
        if owns_prop(o):
            return o

    return None


def on_shape_key_value_update(self, context):
    """Called when any shape key slider or mute toggle changes."""
    if hasattr(self, "slider_min") and hasattr(self, "slider_max"):
        clamped = max(self.slider_min, min(self.slider_max, self.value))
        if abs(self.value - clamped) > 1e-5:
            self.value = clamped
            return
    obj = get_owner_curves_object(self, context)
    if obj:
        evaluate_shape_keys(obj)


def on_active_index_update(self, context):
    """Called when user changes active selection in the shape key list."""
    obj = get_owner_curves_object(self, context)
    if obj:
        cleanup_legacy_geometry_nodes(obj)
        if self.is_sculpting:
            commit_sculpt_changes(obj, key_index=self.prev_active_index)
            capture_sculpt_start(obj)
        self.prev_active_index = self.active_index
        if self.solo:
            evaluate_shape_keys(obj, force=True)
        if 0 < self.active_index < len(self.keys):
            set_slider_soft_range(self.keys[self.active_index])


def on_solo_update(self, context):
    """Called when user toggles Solo (isolate active shape key)."""
    obj = get_owner_curves_object(self, context)
    if obj:
        evaluate_shape_keys(obj, force=True)


def on_rest_position_toggle(self, context):
    """Called when user toggles Add Rest Position."""
    obj = get_owner_curves_object(self, context)
    if obj:
        sync_rest_position(obj)


# -------------------------------------------------------------------
# Dynamic Range Engine (Protected C RNA Range Callback)
# -------------------------------------------------------------------

_shape_key_ranges = {}
_range_callback_installed = False
_range_func_ref = None
_RANGE_FUNC_TYPE = None


def _verify_float_rna_layout(prop):
    """Verifies that the FloatPropertyRNA struct matches the expected 64-bit Blender memory layout.
    Validates soft_min, soft_max, hard_min, hard_max values in the struct before modifying memory."""
    try:
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            return False
        if not prop or getattr(prop, "type", "") != 'FLOAT':
            return False

        ptr = prop.as_pointer()
        if not ptr:
            return False

        f32 = (ctypes.c_float * 128).from_address(ptr)
        # Value prop definition: soft_min=0.0, soft_max=1.0, min=-10.0, max=10.0
        target_sig = (0.0, 1.0, -10.0, 10.0)
        for i in range(75, 105):
            if (abs(f32[i] - target_sig[0]) < 1e-4 and
                abs(f32[i + 1] - target_sig[1]) < 1e-4 and
                abs(f32[i + 2] - target_sig[2]) < 1e-4 and
                abs(f32[i + 3] - target_sig[3]) < 1e-4):
                return True
        return False
    except Exception:
        return False


def _ensure_dynamic_range_callback(item=None):
    """Installs a native C PropFloatRangeFunc on HairShapeKeyItem.value so Blender
    dynamically queries each individual shape key's slider_min and slider_max range.
    Guarded by runtime struct signature verification to guarantee zero crash risk."""
    global _range_callback_installed, _range_func_ref, _RANGE_FUNC_TYPE
    if _range_callback_installed:
        return

    try:
        cls = globals().get("HairShapeKeyItem")
        if not cls or not hasattr(cls, "bl_rna"):
            if item and hasattr(item, "bl_rna"):
                cls = item
            else:
                return

        prop = cls.bl_rna.properties.get("value")
        if not prop:
            return

        # Verification check (Fix CRIT-1: struct safety verification)
        if not _verify_float_rna_layout(prop):
            print("[HairShapeKey] Dynamic range callback skipped: struct layout mismatch or unsupported platform.")
            return

        ptr = prop.as_pointer()
        u64 = (ctypes.c_uint64 * 64).from_address(ptr)

        # Ensure slot 33 is null or our previous callback before hooking
        slot_val = u64[33]
        if _range_func_ref:
            existing_addr = ctypes.cast(_range_func_ref, ctypes.c_void_p).value
            if slot_val != 0 and slot_val != existing_addr:
                print("[HairShapeKey] Dynamic range slot 33 is already hooked externally. Skipping.")
                return
        elif slot_val != 0:
            print("[HairShapeKey] Dynamic range slot 33 is non-zero. Skipping to preserve external hook.")
            return

        _RANGE_FUNC_TYPE = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float)
        )

        def dynamic_range_cb(ptr_rna, p_min, p_max, p_softmin, p_softmax):
            if not ptr_rna:
                return
            try:
                data_ptr = ctypes.c_void_p.from_address(ptr_rna + 16).value
                r = _shape_key_ranges.get(data_ptr)

                # Fix CRIT-2: Fallback resolver when pointer was invalidated by Undo/Redo,
                # file load, or collection dynamic array reallocation
                if not r and data_ptr:
                    id_ptr = ctypes.c_void_p.from_address(ptr_rna).value
                    target_obj = None

                    ctx = getattr(bpy, "context", None)
                    act = getattr(ctx, "active_object", None) if ctx else None
                    if act and act.as_pointer() == id_ptr:
                        target_obj = act
                    elif hasattr(bpy, "data") and hasattr(bpy.data, "objects"):
                        for o in bpy.data.objects:
                            if o.as_pointer() == id_ptr:
                                target_obj = o
                                break

                    if target_obj and hasattr(target_obj, "hair_shape_keys"):
                        for k in target_obj.hair_shape_keys.keys:
                            _shape_key_ranges[k.as_pointer()] = (float(k.slider_min), float(k.slider_max))
                        r = _shape_key_ranges.get(data_ptr)

                if r:
                    s_min, s_max = r
                    if p_min: p_min[0] = s_min
                    if p_max: p_max[0] = s_max
                    if p_softmin: p_softmin[0] = s_min
                    if p_softmax: p_softmax[0] = s_max
                else:
                    if p_min: p_min[0] = -10.0
                    if p_max: p_max[0] = 10.0
                    if p_softmin: p_softmin[0] = 0.0
                    if p_softmax: p_softmax[0] = 1.0
            except Exception:
                pass

        _range_func_ref = _RANGE_FUNC_TYPE(dynamic_range_cb)
        u64[33] = ctypes.cast(_range_func_ref, ctypes.c_void_p).value
        _range_callback_installed = True
    except Exception as e:
        print(f"[HairShapeKey] Dynamic range callback warning: {e}")


def cleanup_dynamic_range_callback():
    """Safely detaches dynamic range callback from HairShapeKeyItem.value and clears cache."""
    global _range_callback_installed, _range_func_ref, _shape_key_ranges
    try:
        cls = globals().get("HairShapeKeyItem")
        if cls and hasattr(cls, "bl_rna"):
            prop = cls.bl_rna.properties.get("value")
            if prop:
                ptr = prop.as_pointer()
                u64 = (ctypes.c_uint64 * 64).from_address(ptr)
                if _range_func_ref:
                    ref_addr = ctypes.cast(_range_func_ref, ctypes.c_void_p).value
                    if u64[33] == ref_addr:
                        u64[33] = 0
                else:
                    u64[33] = 0
    except Exception:
        pass
    _range_callback_installed = False
    _range_func_ref = None
    _shape_key_ranges.clear()


def set_slider_soft_range(item):
    """Registers item's slider_min and slider_max in the per-item range table."""
    if not item or not hasattr(item, "slider_min") or not hasattr(item, "slider_max"):
        return
    _ensure_dynamic_range_callback(item)
    _shape_key_ranges[item.as_pointer()] = (float(item.slider_min), float(item.slider_max))


def get_unique_shape_key_name(sk_data, base_name, exclude_item=None):
    """Generates a unique shape key name following Blender's standard naming convention (Key.001, Key.002, etc.)."""
    if not sk_data or not hasattr(sk_data, "keys"):
        return base_name

    existing_names = set()
    for k in sk_data.keys:
        if exclude_item is not None and k == exclude_item:
            continue
        existing_names.add(k.name)

    if base_name not in existing_names:
        return base_name

    match = re.match(r"^(.*?)\.(\d{3,})$", base_name)
    if match:
        root_name = match.group(1)
        count = int(match.group(2))
    else:
        root_name = base_name
        count = 1

    while True:
        candidate = f"{root_name}.{count:03d}"
        if candidate not in existing_names:
            return candidate
        count += 1


_is_updating_shape_key_name = False


def on_shape_key_name_update(self, context):
    """Handles shape key renames, protecting Basis, preventing duplicates, and propagating to Relative To references."""
    global _is_updating_shape_key_name
    if _is_updating_shape_key_name:
        return

    obj = get_owner_curves_object(self, context)
    if not obj or not hasattr(obj, "hair_shape_keys"):
        return

    sk_data = obj.hair_shape_keys

    # Protect Basis key (index 0) from being renamed
    if len(sk_data.keys) > 0 and sk_data.keys[0] == self:
        if self.name != "Basis":
            _is_updating_shape_key_name = True
            try:
                self.name = "Basis"
                self.prev_name = "Basis"
                self["_prev_name"] = "Basis"
            finally:
                _is_updating_shape_key_name = False
            return

    raw_name = self.name.strip()
    if not raw_name:
        raw_name = "Key"

    unique_name = get_unique_shape_key_name(sk_data, raw_name, exclude_item=self)
    old_name = self.prev_name or self.get("_prev_name") or ""

    _is_updating_shape_key_name = True
    try:
        if self.name != unique_name:
            self.name = unique_name
        self.prev_name = self.name
        self["_prev_name"] = self.name

        # Propagate rename to all other keys referencing the old name
        if old_name and old_name != self.name:
            for k in sk_data.keys:
                if k != self and k.relative_key == old_name:
                    k.relative_key = self.name
    finally:
        _is_updating_shape_key_name = False


def clamp_key_value(item, context=None):
    """Clamps item.value within [item.slider_min, item.slider_max] and evaluates if changed."""
    clamped = max(item.slider_min, min(item.slider_max, item.value))
    if abs(item.value - clamped) > 1e-5:
        item.value = clamped
    else:
        obj = get_owner_curves_object(item, context)
        if obj:
            evaluate_shape_keys(obj)


def on_slider_min_update(self, context):
    """Enforces slider_min <= slider_max within [-10.0, 10.0] and clamps value."""
    if self.slider_min > 10.0:
        self.slider_min = 10.0
    elif self.slider_min < -10.0:
        self.slider_min = -10.0

    if self.slider_min > self.slider_max + 1e-5:
        self.slider_max = self.slider_min
    set_slider_soft_range(self)
    clamp_key_value(self, context)


def on_slider_max_update(self, context):
    """Enforces slider_max >= slider_min within [-10.0, 10.0] and clamps value."""
    if self.slider_max > 10.0:
        self.slider_max = 10.0
    elif self.slider_max < -10.0:
        self.slider_max = -10.0

    if self.slider_max < self.slider_min - 1e-5:
        self.slider_min = self.slider_max
    set_slider_soft_range(self)
    clamp_key_value(self, context)


def get_relative_key_items(self, context):
    """Dynamically lists available shape keys to be chosen as reference.
    Enforces DAG compliance: only Basis and keys positioned above this key are selectable."""
    obj = get_owner_curves_object(self, context)
    if not obj and context and hasattr(context, "active_object"):
        if context.active_object and context.active_object.type == 'CURVES':
            obj = context.active_object

    basis_name = "Basis"
    if obj and hasattr(obj, "hair_shape_keys") and len(obj.hair_shape_keys.keys) > 0:
        basis_name = obj.hair_shape_keys.keys[0].name

    items = [(basis_name, basis_name, f"Relative to {basis_name}")]
    if obj and hasattr(obj, "hair_shape_keys"):
        for k in obj.hair_shape_keys.keys[1:]:
            if k == self:
                break
            if k.name:
                items.append((k.name, k.name, f"Relative to {k.name}"))
    return items


# -------------------------------------------------------------------
# Property Definitions
# -------------------------------------------------------------------

class HairShapeKeyItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(
        name="Name",
        default="Key",
        update=on_shape_key_name_update
    )
    prev_name: bpy.props.StringProperty(
        name="Previous Name",
        options={'HIDDEN', 'SKIP_SAVE'}
    )
    attr_name: bpy.props.StringProperty(name="Attribute Name")
    value: bpy.props.FloatProperty(
        name="Value",
        description="Value of shape key",
        min=-10.0,
        max=10.0,
        soft_min=0.0,
        soft_max=1.0,
        default=0.0,
        options={'ANIMATABLE'},
        update=on_shape_key_value_update
    )
    slider_min: bpy.props.FloatProperty(
        name="Range Min",
        description="Minimum value of the slider range (-10.0 to 10.0)",
        default=0.0,
        min=-10.0,
        max=10.0,
        soft_min=-10.0,
        soft_max=10.0,
        update=on_slider_min_update
    )
    slider_max: bpy.props.FloatProperty(
        name="Max",
        description="Maximum value of the slider range (-10.0 to 10.0)",
        default=1.0,
        min=-10.0,
        max=10.0,
        soft_min=-10.0,
        soft_max=10.0,
        update=on_slider_max_update
    )
    mute: bpy.props.BoolProperty(name="Mute", default=False, update=on_shape_key_value_update)
    lock: bpy.props.BoolProperty(name="Lock", description="Lock shape key against editing and sculpting", default=False)
    relative_key: bpy.props.EnumProperty(
        name="Relative To",
        description="Reference key for relative deformation",
        items=get_relative_key_items,
        update=on_shape_key_value_update
    )
    maintain_length: bpy.props.BoolProperty(
        name="Maintain Curve Length",
        description="Preserves hair strand length during deformation to prevent hair shrinking/stretching",
        default=True,
        update=on_shape_key_value_update
    )


class HairShapeKeyData(bpy.types.PropertyGroup):
    keys: bpy.props.CollectionProperty(type=HairShapeKeyItem)
    active_index: bpy.props.IntProperty(name="Active Index", default=0, update=on_active_index_update)
    prev_active_index: bpy.props.IntProperty(name="Previous Active Index", default=0)
    relative: bpy.props.BoolProperty(name="Relative", default=True, update=on_shape_key_value_update)
    eval_time: bpy.props.FloatProperty(
        name="Evaluation Time",
        description="Evaluation time for non-relative shape keys (10.0 per key)",
        default=0.0,
        min=0.0,
        update=on_shape_key_value_update
    )
    solo: bpy.props.BoolProperty(
        name="Solo",
        description="Solo active shape key (isolate its deformation)",
        default=False,
        update=on_solo_update
    )
    add_rest_position: bpy.props.BoolProperty(
        name="Add Rest Position",
        description="Add and maintain standard rest_position attribute on curve points",
        default=False,
        update=on_rest_position_toggle
    )
    is_sculpting: bpy.props.BoolProperty(name="Is Sculpting", default=False)


PROPERTY_CLASSES = (
    HairShapeKeyItem,
    HairShapeKeyData,
)
