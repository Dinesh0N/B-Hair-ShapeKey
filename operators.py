import bpy
import numpy as np
from mathutils.kdtree import KDTree
from .core import (
    cleanup_legacy_geometry_nodes,
    sync_rest_position,
    get_curve_offsets,
    evaluate_shape_keys,
)
from .sculpt import capture_sculpt_start, commit_sculpt_changes
from .properties import _shape_key_ranges, get_unique_shape_key_name

# -------------------------------------------------------------------
# Symmetry & Bilateral Mirroring Engine
# -------------------------------------------------------------------

def flip_shape_key_deltas(curves, delta, basis_pos, offsets, axis='X', use_topology=False):
    """
    Mirrors shape key displacement deltas across the chosen local axis ('X', 'Y', or 'Z').
    Pairs bilateral curve strands across the symmetry plane so movements mirror to the opposite strands.
    Guarded with mutual pairing check to prevent asymmetric double-pairing.
    """
    num_curves = len(offsets) - 1
    num_points = len(curves.points)
    ax_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(axis.upper(), 0)

    if num_curves <= 0 or len(delta) != num_points * 3:
        out = delta.copy()
        out[ax_idx::3] = -out[ax_idx::3]
        return out

    roots = np.empty((num_curves, 3), dtype=np.float32)
    counts = np.diff(offsets)

    for c in range(num_curves):
        s = offsets[c] * 3
        roots[c] = basis_pos[s : s + 3]

    kd = KDTree(num_curves)
    for c in range(num_curves):
        kd.insert(roots[c], c)
    kd.balance()

    new_delta = np.empty_like(delta)
    paired = np.full(num_curves, -1, dtype=np.int32)

    tol = 0.005 if use_topology else 0.05

    for c in range(num_curves):
        if paired[c] != -1:
            continue

        target_root = list(roots[c])
        target_root[ax_idx] = -target_root[ax_idx]
        _, nearest_idx, dist = kd.find(tuple(target_root))

        coord_val = abs(roots[c][ax_idx])
        # Check if match is valid: within tolerance, matching point count, and target not already claimed
        if (nearest_idx is not None and
            counts[c] == counts[nearest_idx] and
            (paired[nearest_idx] == -1 or paired[nearest_idx] == c)):
            max_dist = max(tol, coord_val * 0.15 + 0.005)
            if dist <= max_dist:
                paired[c] = nearest_idx
                paired[nearest_idx] = c
            else:
                paired[c] = c
        else:
            paired[c] = c

    # Apply mirrored deltas
    for c in range(num_curves):
        p = paired[c]
        s_src, e_src = offsets[c] * 3, offsets[c + 1] * 3
        s_dst, e_dst = offsets[p] * 3, offsets[p + 1] * 3

        d_src = delta[s_src:e_src].copy()
        # Invert displacement along the chosen axis
        d_src[ax_idx::3] = -d_src[ax_idx::3]

        new_delta[s_dst:e_dst] = d_src

    return new_delta


# -------------------------------------------------------------------
# Core Shape Key Operators
# -------------------------------------------------------------------

class HAIR_OT_AddShapeKey(bpy.types.Operator):
    bl_idname = "hair_sk.add_key"
    bl_label = "Add Shape Key"
    bl_description = "Add Basis or new hair shape key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'CURVES'

    def execute(self, context):
        obj = context.active_object
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        curves = obj.data
        sk_data = obj.hair_shape_keys
        cleanup_legacy_geometry_nodes(obj)

        num_points = len(curves.points)
        if num_points == 0:
            self.report({'WARNING'}, "Hair Curves object has no points.")
            return {'CANCELLED'}

        raw_pos = np.empty(num_points * 3, dtype=np.float32)
        curves.attributes['position'].data.foreach_get('vector', raw_pos)

        # First key is always Basis
        if len(sk_data.keys) == 0:
            attr = curves.attributes.get("sk_basis")
            if not attr:
                attr = curves.attributes.new(name="sk_basis", type='FLOAT_VECTOR', domain='POINT')
            attr.data.foreach_set('vector', raw_pos)

            basis_item = sk_data.keys.add()
            basis_item.name = "Basis"
            basis_item.prev_name = "Basis"
            basis_item["_prev_name"] = "Basis"
            basis_item.attr_name = "sk_basis"
            sk_data.active_index = 0
            sk_data.prev_active_index = 0

            sync_rest_position(obj)
            evaluate_shape_keys(obj, force=True)
            curves.update_tag()
            obj.update_tag(refresh={'DATA'})
            self.report({'INFO'}, "Created Basis key.")
            return {'FINISHED'}

        # Subsequent keys are Delta keys
        idx = len(sk_data.keys)
        name = get_unique_shape_key_name(sk_data, f"Key {idx}")
        attr_name = f"sk_delta_{idx}"

        # Ensure unique attribute name
        while curves.attributes.get(attr_name):
            idx += 1
            attr_name = f"sk_delta_{idx}"

        attr = curves.attributes.new(name=attr_name, type='FLOAT_VECTOR', domain='POINT')
        zero_delta = np.zeros(num_points * 3, dtype=np.float32)
        attr.data.foreach_set('vector', zero_delta)

        item = sk_data.keys.add()
        item.name = name
        item.prev_name = name
        item["_prev_name"] = name
        item.attr_name = attr_name
        item.value = 1.0
        sk_data.active_index = len(sk_data.keys) - 1
        sk_data.prev_active_index = sk_data.active_index

        evaluate_shape_keys(obj, force=True)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        self.report({'INFO'}, f"Created {name}. Groom hair in Sculpt Curves mode.")
        return {'FINISHED'}


class HAIR_OT_RemoveShapeKey(bpy.types.Operator):
    bl_idname = "hair_sk.remove_key"
    bl_label = "Remove Shape Key"
    bl_description = "Remove active shape key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object
            and context.active_object.type == 'CURVES'
            and len(context.active_object.hair_shape_keys.keys) > 0
        )

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index

        if idx >= len(sk_data.keys):
            return {'CANCELLED'}

        if sk_data.is_sculpting:
            commit_sculpt_changes(obj, key_index=sk_data.prev_active_index)

        # Case 1: Removing Basis (index 0)
        if idx == 0:
            if len(sk_data.keys) == 1:
                # If only Basis exists, remove shape keys completely
                _shape_key_ranges.clear()
                basis_attr = curves.attributes.get("sk_basis")
                if basis_attr:
                    num_points = len(curves.points)
                    if len(basis_attr.data) == num_points:
                        basis_pos = np.empty(num_points * 3, dtype=np.float32)
                        basis_attr.data.foreach_get('vector', basis_pos)
                        pos_attr = curves.attributes.get('position')
                        if pos_attr and len(pos_attr.data) == num_points:
                            pos_attr.data.foreach_set('vector', basis_pos)
                    curves.attributes.remove(basis_attr)

                for item in list(sk_data.keys):
                    attr = curves.attributes.get(item.attr_name)
                    if attr:
                        curves.attributes.remove(attr)

                rest_attr = curves.attributes.get("rest_position")
                if rest_attr:
                    curves.attributes.remove(rest_attr)

                cleanup_legacy_geometry_nodes(obj)
                sk_data.keys.clear()
                sk_data.active_index = 0
                sk_data.prev_active_index = 0
                if obj.mode == 'SCULPT_CURVES':
                    capture_sculpt_start(obj)
                curves.update_tag()
                obj.update_tag(refresh={'DATA'})
                context.view_layer.update()
                self.report({'INFO'}, "Removed Basis and restored original curve position.")
                return {'FINISHED'}

            # If other keys exist, promote the 2nd key (index 1) to become the new Basis
            num_points = len(curves.points)
            basis_attr = curves.attributes.get("sk_basis")
            pos_attr = curves.attributes.get("position")
            if not basis_attr and pos_attr and len(pos_attr.data) == num_points:
                basis_attr = curves.attributes.new(name="sk_basis", type='FLOAT_VECTOR', domain='POINT')
                pos = np.empty(num_points * 3, dtype=np.float32)
                pos_attr.data.foreach_get('vector', pos)
                basis_attr.data.foreach_set('vector', pos)

            second_key = sk_data.keys[1]
            second_attr = curves.attributes.get(second_key.attr_name)

            if not basis_attr or len(basis_attr.data) != num_points:
                return {'CANCELLED'}

            basis_pos = np.empty(num_points * 3, dtype=np.float32)
            basis_attr.data.foreach_get('vector', basis_pos)

            if second_attr and len(second_attr.data) == num_points:
                second_delta = np.empty(num_points * 3, dtype=np.float32)
                second_attr.data.foreach_get('vector', second_delta)
            else:
                second_delta = np.zeros(num_points * 3, dtype=np.float32)

            # New Basis position is old Basis + 2nd key delta
            new_basis = basis_pos + second_delta
            basis_attr.data.foreach_set('vector', new_basis)

            # Re-project all other keys: delta_new = delta_old - second_delta
            for other_key in sk_data.keys[2:]:
                other_attr = curves.attributes.get(other_key.attr_name)
                if other_attr and len(other_attr.data) == num_points:
                    other_delta = np.empty(num_points * 3, dtype=np.float32)
                    other_attr.data.foreach_get('vector', other_delta)
                    other_attr.data.foreach_set('vector', other_delta - second_delta)

            # Clean up the 2nd key (now promoted to Basis)
            promoted_name = second_key.name
            _shape_key_ranges.pop(second_key.as_pointer(), None)
            if second_attr:
                curves.attributes.remove(second_attr)
            sk_data.keys.remove(1)
            sk_data.active_index = 0
            sk_data.prev_active_index = 0

            # Update any remaining keys referencing the promoted key to Basis
            for k in sk_data.keys:
                if k.relative_key == promoted_name:
                    k.relative_key = "Basis"

            sync_rest_position(obj)
            evaluate_shape_keys(obj, force=True)
            if obj.mode == 'SCULPT_CURVES':
                capture_sculpt_start(obj)
            curves.update_tag()
            obj.update_tag(refresh={'DATA'})
            context.view_layer.update()
            self.report({'INFO'}, f"Removed original Basis. '{promoted_name}' is now Basis.")
            return {'FINISHED'}

        item = sk_data.keys[idx]
        removed_name = item.name
        _shape_key_ranges.pop(item.as_pointer(), None)
        attr = curves.attributes.get(item.attr_name)
        if attr:
            curves.attributes.remove(attr)

        sk_data.keys.remove(idx)
        sk_data.active_index = max(0, idx - 1)
        sk_data.prev_active_index = sk_data.active_index

        # Clean up any remaining shape keys referencing the deleted key as relative_key
        basis_name = sk_data.keys[0].name if len(sk_data.keys) > 0 else "Basis"
        for k in sk_data.keys:
            if k.relative_key == removed_name:
                k.relative_key = basis_name

        evaluate_shape_keys(obj, force=True)
        if obj.mode == 'SCULPT_CURVES':
            capture_sculpt_start(obj)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()
        return {'FINISHED'}


class HAIR_OT_MoveShapeKey(bpy.types.Operator):
    bl_idname = "hair_sk.move_key"
    bl_label = "Move Shape Key"
    bl_description = "Move active shape key up or down in the list"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        items=[('UP', "Up", ""), ('DOWN', "Down", "")],
        default='UP'
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj
            and obj.type == 'CURVES'
            and len(obj.hair_shape_keys.keys) > 2
            and obj.hair_shape_keys.active_index > 0
        )

    def execute(self, context):
        sk_data = context.active_object.hair_shape_keys
        idx = sk_data.active_index

        if self.direction == 'UP' and idx > 1:
            sk_data.keys.move(idx, idx - 1)
            sk_data.active_index = idx - 1
        elif self.direction == 'DOWN' and idx < len(sk_data.keys) - 1:
            sk_data.keys.move(idx, idx + 1)
            sk_data.active_index = idx + 1

        sk_data.prev_active_index = sk_data.active_index
        return {'FINISHED'}


class HAIR_OT_ClearShapeKeys(bpy.types.Operator):
    bl_idname = "hair_sk.clear_keys"
    bl_label = "Clear All Values"
    bl_description = "Resets all shape key values to 0.0"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 1

    def execute(self, context):
        obj = context.active_object
        sk_data = obj.hair_shape_keys
        for key in sk_data.keys[1:]:
            if not key.lock:
                if key.slider_min <= 0.0 <= key.slider_max:
                    key.value = 0.0
                else:
                    key.value = max(key.slider_min, min(key.slider_max, 0.0))
        evaluate_shape_keys(obj, force=True)
        return {'FINISHED'}


class HAIR_OT_NewCombined(bpy.types.Operator):
    bl_idname = "hair_sk.new_combined"
    bl_label = "New Combined"
    bl_description = "Creates a new shape key from the current blended hair shape"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        num_points = len(curves.points)

        basis_attr = curves.attributes.get("sk_basis")
        if not basis_attr:
            return {'CANCELLED'}

        basis_pos = np.empty(num_points * 3, dtype=np.float32)
        basis_attr.data.foreach_get('vector', basis_pos)

        current_pos = np.empty(num_points * 3, dtype=np.float32)
        curves.attributes['position'].data.foreach_get('vector', current_pos)

        delta = current_pos - basis_pos

        idx = len(sk_data.keys)
        name = get_unique_shape_key_name(sk_data, f"Combined_{idx}")
        attr_name = f"sk_delta_{idx}"
        while curves.attributes.get(attr_name):
            idx += 1
            attr_name = f"sk_delta_{idx}"

        attr = curves.attributes.new(name=attr_name, type='FLOAT_VECTOR', domain='POINT')
        attr.data.foreach_set('vector', delta)

        item = sk_data.keys.add()
        item.name = name
        item.prev_name = name
        item["_prev_name"] = name
        item.attr_name = attr_name
        item.value = 0.0
        sk_data.active_index = len(sk_data.keys) - 1
        sk_data.prev_active_index = sk_data.active_index

        self.report({'INFO'}, f"Created '{name}' from current mix")
        return {'FINISHED'}


class HAIR_OT_DuplicateKey(bpy.types.Operator):
    bl_idname = "hair_sk.duplicate_key"
    bl_label = "Duplicate"
    bl_description = "Duplicates the active shape key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 1 and obj.hair_shape_keys.active_index > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index
        src_item = sk_data.keys[idx]

        src_attr = curves.attributes.get(src_item.attr_name)
        if not src_attr:
            return {'CANCELLED'}

        num_points = len(curves.points)
        delta = np.empty(num_points * 3, dtype=np.float32)
        src_attr.data.foreach_get('vector', delta)

        new_idx = len(sk_data.keys)
        name = get_unique_shape_key_name(sk_data, f"{src_item.name}.copy")
        attr_name = f"sk_delta_{new_idx}"
        while curves.attributes.get(attr_name):
            new_idx += 1
            attr_name = f"sk_delta_{new_idx}"

        attr = curves.attributes.new(name=attr_name, type='FLOAT_VECTOR', domain='POINT')
        attr.data.foreach_set('vector', delta)

        new_item = sk_data.keys.add()
        new_item.name = name
        new_item.prev_name = name
        new_item["_prev_name"] = name
        new_item.attr_name = attr_name
        new_item.slider_min = src_item.slider_min
        new_item.slider_max = src_item.slider_max
        new_item.value = src_item.value
        new_item.maintain_length = src_item.maintain_length
        new_item.relative_key = src_item.relative_key
        sk_data.active_index = len(sk_data.keys) - 1
        sk_data.prev_active_index = sk_data.active_index

        self.report({'INFO'}, f"Duplicated '{src_item.name}' as '{name}'")
        return {'FINISHED'}


class HAIR_OT_FlipKey(bpy.types.Operator):
    bl_idname = "hair_sk.flip_key"
    bl_label = "Flip"
    bl_description = "Mirrors active shape key across the local X, Y, or Z axis"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        name="Axis",
        description="Symmetry axis to mirror across",
        items=[
            ('X', "X", "Mirror across local X axis (Left / Right)"),
            ('Y', "Y", "Mirror across local Y axis (Front / Back)"),
            ('Z', "Z", "Mirror across local Z axis (Top / Bottom)"),
        ],
        default='X'
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "axis", expand=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 1 and obj.hair_shape_keys.active_index > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index
        item = sk_data.keys[idx]

        basis_attr = curves.attributes.get("sk_basis")
        delta_attr = curves.attributes.get(item.attr_name)
        if not basis_attr or not delta_attr:
            return {'CANCELLED'}

        num_points = len(curves.points)
        if len(basis_attr.data) != num_points or len(delta_attr.data) != num_points:
            return {'CANCELLED'}

        basis_pos = np.empty(num_points * 3, dtype=np.float32)
        basis_attr.data.foreach_get('vector', basis_pos)

        delta = np.empty(num_points * 3, dtype=np.float32)
        delta_attr.data.foreach_get('vector', delta)

        offsets = get_curve_offsets(curves)
        flipped_delta = flip_shape_key_deltas(curves, delta, basis_pos, offsets, axis=self.axis)

        delta_attr.data.foreach_set('vector', flipped_delta)
        evaluate_shape_keys(obj, force=True)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        self.report({'INFO'}, f"Flipped '{item.name}' across {self.axis} axis")
        return {'FINISHED'}


class HAIR_OT_LockAll(bpy.types.Operator):
    bl_idname = "hair_sk.lock_all"
    bl_label = "Lock All"
    bl_description = "Locks or unlocks all shape keys"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(
        items=[('LOCK', "Lock All", ""), ('UNLOCK', "Unlock All", "")],
        default='LOCK'
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 0

    def execute(self, context):
        obj = context.active_object
        sk_data = obj.hair_shape_keys
        lock_val = (self.action == 'LOCK')
        for key in sk_data.keys:
            key.lock = lock_val
        self.report({'INFO'}, f"{'Locked' if lock_val else 'Unlocked'} all shape keys")
        return {'FINISHED'}


class HAIR_OT_MakeBasis(bpy.types.Operator):
    bl_idname = "hair_sk.make_basis"
    bl_label = "Make Basis"
    bl_description = "Promotes active shape key to be the new Basis and re-projects other keys"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 1 and obj.hair_shape_keys.active_index > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index
        item = sk_data.keys[idx]

        if sk_data.is_sculpting:
            commit_sculpt_changes(obj, key_index=sk_data.prev_active_index)

        basis_attr = curves.attributes.get("sk_basis")
        delta_attr = curves.attributes.get(item.attr_name)
        if not basis_attr or not delta_attr:
            return {'CANCELLED'}

        num_points = len(curves.points)
        basis_pos = np.empty(num_points * 3, dtype=np.float32)
        delta_pos = np.empty(num_points * 3, dtype=np.float32)

        basis_attr.data.foreach_get('vector', basis_pos)
        delta_attr.data.foreach_get('vector', delta_pos)

        # New Basis is Basis + Active Delta
        new_basis = basis_pos + delta_pos
        basis_attr.data.foreach_set('vector', new_basis)

        # All other keys delta_new = delta_old - active_delta
        for other_key in sk_data.keys[1:]:
            if other_key != item:
                other_attr = curves.attributes.get(other_key.attr_name)
                if other_attr:
                    other_buf = np.empty(num_points * 3, dtype=np.float32)
                    other_attr.data.foreach_get('vector', other_buf)
                    other_attr.data.foreach_set('vector', other_buf - delta_pos)

        # Remove the promoted key
        removed_name = item.name
        _shape_key_ranges.pop(item.as_pointer(), None)
        curves.attributes.remove(delta_attr)
        sk_data.keys.remove(idx)
        sk_data.active_index = 0
        sk_data.prev_active_index = 0

        basis_name = sk_data.keys[0].name if len(sk_data.keys) > 0 else "Basis"
        for other_key in sk_data.keys:
            if other_key.relative_key == removed_name:
                other_key.relative_key = basis_name

        sync_rest_position(obj)
        evaluate_shape_keys(obj, force=True)
        if obj.mode == 'SCULPT_CURVES':
            capture_sculpt_start(obj)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()
        self.report({'INFO'}, f"Promoted '{item.name}' to Basis")
        return {'FINISHED'}


class HAIR_OT_DeleteAll(bpy.types.Operator):
    bl_idname = "hair_sk.delete_all"
    bl_label = "Delete All"
    bl_description = "Removes all shape keys and resets curve back to Basis"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys

        _shape_key_ranges.clear()

        # Reset curve position to Basis
        basis_attr = curves.attributes.get("sk_basis")
        if basis_attr:
            num_points = len(curves.points)
            basis_pos = np.empty(num_points * 3, dtype=np.float32)
            basis_attr.data.foreach_get('vector', basis_pos)
            curves.attributes['position'].data.foreach_set('vector', basis_pos)

        # Remove all delta attributes
        for key in list(sk_data.keys):
            attr = curves.attributes.get(key.attr_name)
            if attr:
                curves.attributes.remove(attr)

        if basis_attr:
            curves.attributes.remove(basis_attr)

        rest_attr = curves.attributes.get("rest_position")
        if rest_attr:
            curves.attributes.remove(rest_attr)

        cleanup_legacy_geometry_nodes(obj)
        sk_data.keys.clear()
        sk_data.active_index = 0
        sk_data.prev_active_index = 0
        sk_data.is_sculpting = False

        if obj.mode == 'SCULPT_CURVES':
            capture_sculpt_start(obj)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()
        self.report({'INFO'}, "Deleted all shape keys and restored Basis.")
        return {'FINISHED'}


class HAIR_OT_ApplyToBase(bpy.types.Operator):
    bl_idname = "hair_sk.apply_to_base"
    bl_label = "Apply to Basis"
    bl_description = "Bakes active key's deformation permanently into Basis and removes the key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and obj.hair_shape_keys.active_index > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index
        item = sk_data.keys[idx]

        if sk_data.is_sculpting:
            commit_sculpt_changes(obj, key_index=sk_data.prev_active_index)

        basis_attr = curves.attributes.get("sk_basis")
        delta_attr = curves.attributes.get(item.attr_name)
        if not basis_attr or not delta_attr:
            return {'CANCELLED'}

        num_points = len(curves.points)
        basis_pos = np.empty(num_points * 3, dtype=np.float32)
        delta_pos = np.empty(num_points * 3, dtype=np.float32)

        basis_attr.data.foreach_get('vector', basis_pos)
        delta_attr.data.foreach_get('vector', delta_pos)

        # New basis is basis + delta * value
        new_basis = basis_pos + (delta_pos * item.value)
        basis_attr.data.foreach_set('vector', new_basis)

        removed_name = item.name
        _shape_key_ranges.pop(item.as_pointer(), None)
        curves.attributes.remove(delta_attr)
        sk_data.keys.remove(idx)
        sk_data.active_index = max(0, idx - 1)
        sk_data.prev_active_index = sk_data.active_index

        basis_name = sk_data.keys[0].name if len(sk_data.keys) > 0 else "Basis"
        for other_key in sk_data.keys:
            if other_key.relative_key == removed_name:
                other_key.relative_key = basis_name

        # Other keys remain identical in global deformation
        shift = delta_pos * item.value
        for other_key in sk_data.keys[1:]:
            other_attr = curves.attributes.get(other_key.attr_name)
            if other_attr:
                other_buf = np.empty(num_points * 3, dtype=np.float32)
                other_attr.data.foreach_get('vector', other_buf)
                other_attr.data.foreach_set('vector', other_buf - shift)

        sync_rest_position(obj)
        evaluate_shape_keys(obj, force=True)
        if obj.mode == 'SCULPT_CURVES':
            capture_sculpt_start(obj)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()
        self.report({'INFO'}, f"Baked '{item.name}' into Basis")
        return {'FINISHED'}


class HAIR_OT_ApplyAll(bpy.types.Operator):
    bl_idname = "hair_sk.apply_all"
    bl_label = "Apply All"
    bl_description = "Applies full blended deformation permanently and removes all shape keys"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 0

    def execute(self, context):
        obj = context.active_object
        curves = obj.data
        sk_data = obj.hair_shape_keys

        if sk_data.is_sculpting:
            commit_sculpt_changes(obj, key_index=sk_data.prev_active_index)

        _shape_key_ranges.clear()

        # Fully evaluate current positions
        evaluate_shape_keys(obj, force=True)

        for key in list(sk_data.keys):
            attr = curves.attributes.get(key.attr_name)
            if attr:
                curves.attributes.remove(attr)

        basis_attr = curves.attributes.get("sk_basis")
        if basis_attr:
            curves.attributes.remove(basis_attr)

        cleanup_legacy_geometry_nodes(obj)
        sk_data.keys.clear()
        sk_data.active_index = 0
        sk_data.prev_active_index = 0
        sk_data.is_sculpting = False

        if obj.mode == 'SCULPT_CURVES':
            capture_sculpt_start(obj)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()
        self.report({'INFO'}, "Collapsed and applied all shape keys permanently.")
        return {'FINISHED'}


class HAIR_OT_EnterSculpt(bpy.types.Operator):
    bl_idname = "hair_sk.enter_sculpt"
    bl_label = "Groom Active Key"
    bl_description = "Prepares the curve and switches to Sculpt Curves mode for the active key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES' and len(obj.hair_shape_keys.keys) > 0

    def execute(self, context):
        obj = context.active_object
        sk_data = obj.hair_shape_keys
        idx = sk_data.active_index
        item = sk_data.keys[idx]

        if item.lock:
            self.report({'WARNING'}, f"Cannot groom '{item.name}': Shape Key is locked. Unlock it first.")
            return {'CANCELLED'}

        # Ensure key is active and unmuted so sculpted deformations are visible
        if idx > 0:
            if item.value == 0.0:
                if item.slider_min <= 1.0 <= item.slider_max:
                    item.value = 1.0
                else:
                    item.value = item.slider_max if item.slider_max != 0.0 else item.slider_min
            if item.mute:
                item.mute = False

        cleanup_legacy_geometry_nodes(obj)
        evaluate_shape_keys(obj, force=True)
        capture_sculpt_start(obj)
        sk_data.prev_active_index = idx
        sk_data.is_sculpting = True

        if context.mode != 'SCULPT_CURVES':
            bpy.ops.object.mode_set(mode='SCULPT_CURVES')

        self.report({'INFO'}, f"Grooming active key: {item.name}")
        return {'FINISHED'}


class HAIR_OT_BakeSculpt(bpy.types.Operator):
    bl_idname = "hair_sk.bake_sculpt"
    bl_label = "Save & Finish Grooming"
    bl_description = "Saves groomed brush changes into the active shape key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVES'

    def execute(self, context):
        obj = context.active_object
        sk_data = obj.hair_shape_keys
        commit_sculpt_changes(obj, key_index=sk_data.prev_active_index)
        sk_data.is_sculpting = False
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        evaluate_shape_keys(obj, force=True)
        curves = obj.data
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})

        idx = sk_data.active_index
        item = sk_data.keys[idx]
        self.report({'INFO'}, f"Saved edits to {item.name}")
        return {'FINISHED'}


OPERATOR_CLASSES = (
    HAIR_OT_AddShapeKey,
    HAIR_OT_RemoveShapeKey,
    HAIR_OT_MoveShapeKey,
    HAIR_OT_ClearShapeKeys,
    HAIR_OT_NewCombined,
    HAIR_OT_DuplicateKey,
    HAIR_OT_FlipKey,
    HAIR_OT_LockAll,
    HAIR_OT_MakeBasis,
    HAIR_OT_DeleteAll,
    HAIR_OT_ApplyToBase,
    HAIR_OT_ApplyAll,
    HAIR_OT_EnterSculpt,
    HAIR_OT_BakeSculpt,
)
