import numpy as np
from .core import sync_rest_position

# -------------------------------------------------------------------
# Sculpt Mode Integration & Brush Stroke Capture
# -------------------------------------------------------------------

def capture_sculpt_start(obj):
    """Records curve points before sculpt brushing begins."""
    if not obj or obj.type != 'CURVES':
        return
    curves = obj.data
    num_points = len(curves.points)
    if num_points == 0:
        return

    pos_attr = curves.attributes.get('position')
    if not pos_attr or len(pos_attr.data) != num_points:
        return

    start_attr = curves.attributes.get("sk_sculpt_start")
    if start_attr and len(start_attr.data) != num_points:
        curves.attributes.remove(start_attr)
        start_attr = None

    if not start_attr:
        start_attr = curves.attributes.new(name="sk_sculpt_start", type='FLOAT_VECTOR', domain='POINT')

    pos = np.empty(num_points * 3, dtype=np.float32)
    pos_attr.data.foreach_get('vector', pos)
    start_attr.data.foreach_set('vector', pos)


def commit_sculpt_changes(obj, key_index=None):
    """Computes brush stroke delta and applies it to the active (or specified) shape key."""
    if not obj or obj.type != 'CURVES':
        return
    curves = obj.data
    sk_data = getattr(obj, "hair_shape_keys", None)
    if not sk_data or len(sk_data.keys) == 0:
        return

    num_points = len(curves.points)
    if num_points == 0:
        return

    start_attr = curves.attributes.get("sk_sculpt_start")
    if not start_attr:
        return

    # Guard: check point count mismatch to avoid crash if topology changed during sculpt
    if len(start_attr.data) != num_points:
        curves.attributes.remove(start_attr)
        return

    idx = sk_data.active_index if key_index is None else key_index
    if idx < 0 or idx >= len(sk_data.keys):
        curves.attributes.remove(start_attr)
        return

    item = sk_data.keys[idx]

    # If key is locked, do not modify it
    if item.lock:
        curves.attributes.remove(start_attr)
        return

    pos_attr = curves.attributes.get('position')
    if not pos_attr or len(pos_attr.data) != num_points:
        curves.attributes.remove(start_attr)
        return

    start_pos = np.empty(num_points * 3, dtype=np.float32)
    start_attr.data.foreach_get('vector', start_pos)

    current_pos = np.empty(num_points * 3, dtype=np.float32)
    pos_attr.data.foreach_get('vector', current_pos)

    # Brush stroke displacement
    delta_brush = current_pos - start_pos

    if idx == 0:
        # Sculpted Basis
        basis_attr = curves.attributes.get("sk_basis")
        if not basis_attr:
            basis_attr = curves.attributes.new(name="sk_basis", type='FLOAT_VECTOR', domain='POINT')
            basis_pos = start_pos
        else:
            if len(basis_attr.data) != num_points:
                curves.attributes.remove(start_attr)
                return
            basis_pos = np.empty(num_points * 3, dtype=np.float32)
            basis_attr.data.foreach_get('vector', basis_pos)
        basis_attr.data.foreach_set('vector', basis_pos + delta_brush)
        sync_rest_position(obj)
    else:
        # Sculpted Delta Key
        # If sculpting with relative value < 1.0, scale delta_brush by 1/value so the
        # evaluated deformation matches the brush stroke 1:1.
        val = item.value if sk_data.relative else 1.0
        if abs(val) > 1e-4 and abs(val - 1.0) > 1e-4:
            delta_brush = delta_brush / val

        delta_attr = curves.attributes.get(item.attr_name)
        if not delta_attr:
            delta_attr = curves.attributes.new(name=item.attr_name, type='FLOAT_VECTOR', domain='POINT')
            delta_pos = np.zeros(num_points * 3, dtype=np.float32)
        else:
            if len(delta_attr.data) != num_points:
                curves.attributes.remove(start_attr)
                return
            delta_pos = np.empty(num_points * 3, dtype=np.float32)
            delta_attr.data.foreach_get('vector', delta_pos)

        delta_attr.data.foreach_set('vector', delta_pos + delta_brush)

    # Remove temporary sculpt buffer
    curves.attributes.remove(start_attr)
