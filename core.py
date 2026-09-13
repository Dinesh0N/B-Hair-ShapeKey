import bpy
import numpy as np

# -------------------------------------------------------------------
# Direct Evaluation Engine (Pure Attribute & NumPy Based)
# -------------------------------------------------------------------

def cleanup_legacy_geometry_nodes(obj):
    """Removes legacy Geometry Nodes modifier and unused node groups if present."""
    if not obj:
        return
    mod = obj.modifiers.get("Hair_Shape_Keys_GN")
    if mod:
        obj.modifiers.remove(mod)

    # Clean up orphan legacy node tree
    group_name = "Hair_Curves_ShapeKeys_52"
    node_group = bpy.data.node_groups.get(group_name)
    if node_group and node_group.users == 0:
        bpy.data.node_groups.remove(node_group)


def sync_rest_position(obj):
    """Creates or updates the rest_position attribute on curve points matching Basis."""
    if not obj or obj.type != 'CURVES':
        return
    curves = obj.data
    sk_data = getattr(obj, "hair_shape_keys", None)
    if not sk_data:
        return

    if sk_data.add_rest_position:
        basis_attr = curves.attributes.get("sk_basis")
        if basis_attr:
            rest_attr = curves.attributes.get("rest_position")
            if not rest_attr:
                rest_attr = curves.attributes.new(name="rest_position", type='FLOAT_VECTOR', domain='POINT')
            num_points = len(curves.points)
            pos = np.empty(num_points * 3, dtype=np.float32)
            basis_attr.data.foreach_get('vector', pos)
            rest_attr.data.foreach_set('vector', pos)
    else:
        rest_attr = curves.attributes.get("rest_position")
        if rest_attr:
            curves.attributes.remove(rest_attr)


def get_curve_offsets(curves):
    """Returns numpy array of curve point offsets [start_0, start_1, ..., total_points]."""
    num_curves = len(curves.curves)
    if num_curves == 0:
        return np.array([0, len(curves.points)], dtype=np.int32)

    if hasattr(curves, "curve_offset_data"):
        offsets = np.empty(num_curves + 1, dtype=np.int32)
        curves.curve_offset_data.foreach_get('value', offsets)
        return offsets
    else:
        offsets = np.empty(num_curves + 1, dtype=np.int32)
        curr = 0
        offsets[0] = 0
        for i, c in enumerate(curves.curves):
            curr += c.points_length
            offsets[i + 1] = curr
        return offsets


def enforce_curve_length(cur_pos, basis_pos, offsets, key_deltas, key_values, maintain_flags):
    """
    Enforces rest curve length along each hair strand from root to tip.
    Eliminates linear shape key chord shrinking while fully preserving smooth bend curvature.
    Vectorized with NumPy for real-time interactive playback.
    """
    out = cur_pos.reshape(-1, 3).copy()
    num_curves = len(offsets) - 1
    if num_curves <= 0 or len(out) == 0:
        return out.ravel()

    basis_pts = basis_pos.reshape(-1, 3)
    diffs = np.diff(offsets)

    # Fast path: uniform points per curve (standard for groomed hair)
    if len(diffs) > 0 and np.all(diffs == diffs[0]):
        k = int(diffs[0])
        if k > 1:
            out_r = out.reshape(num_curves, k, 3)
            bas_r = basis_pts.reshape(num_curves, k, 3)

            # Rest segment lengths of Basis
            bas_diff = np.diff(bas_r, axis=1)
            l_basis = np.linalg.norm(bas_diff, axis=2, keepdims=True)

            # Desired segment lengths: preserve intended shape key lengths while preventing runaway compounding stretch.
            # Shape keys with maintain_length=True strictly maintain Basis rest length (eliminating chord shrinking).
            # Only shape keys with maintain_length=False (deliberate length scaling) contribute length changes.
            if all(maintain_flags):
                l_desired = l_basis
            else:
                delta_l_sum = np.zeros_like(l_basis)
                weight_len_sum = np.zeros_like(l_basis)

                for d_flat, v, m in zip(key_deltas, key_values, maintain_flags):
                    if not m and v != 0.0:
                        d_r = d_flat.reshape(num_curves, k, 3)
                        key_pts = bas_r + d_r
                        l_key = np.linalg.norm(np.diff(key_pts, axis=1), axis=2, keepdims=True)
                        delta_l = l_key - l_basis

                        has_len_change = np.abs(delta_l) > 1e-5
                        delta_l_sum += v * delta_l
                        weight_len_sum += np.where(has_len_change, abs(v), 0.0)

                norm_factor = np.where(weight_len_sum > 1.0, weight_len_sum, 1.0)
                l_desired = l_basis + (delta_l_sum / norm_factor)
                l_desired = np.maximum(l_desired, 1e-6)

            # Directions from the smooth linearly blended curve
            cur_diff = np.diff(out_r, axis=1)
            cur_norm = np.linalg.norm(cur_diff, axis=2, keepdims=True)

            bas_norm = np.where(l_basis > 1e-6, l_basis, 1.0)
            bas_unit = np.where(l_basis > 1e-6, bas_diff / bas_norm, 0.0)
            unit_dirs = np.where(cur_norm > 1e-6, cur_diff / np.where(cur_norm > 1e-6, cur_norm, 1.0), bas_unit)

            out_r[:, 1:, :] = out_r[:, :1, :] + np.cumsum(unit_dirs * l_desired, axis=1)

            return out_r.reshape(-1, 3).ravel()

    # General path: variable point counts per curve (fully vectorized 1D segmented algorithm)
    N = len(out)
    diffs = np.diff(offsets)
    is_tip = np.zeros(N, dtype=bool)
    is_tip[offsets[1:] - 1] = True

    seg_start = np.where(~is_tip)[0]
    seg_end = seg_start + 1

    if len(seg_start) == 0:
        return out.ravel()

    b_diff = basis_pts[seg_end] - basis_pts[seg_start]
    l_basis = np.linalg.norm(b_diff, axis=1, keepdims=True)

    if all(maintain_flags):
        l_desired = l_basis
    else:
        delta_l_sum = np.zeros_like(l_basis)
        weight_len_sum = np.zeros_like(l_basis)

        for d_flat, v, m in zip(key_deltas, key_values, maintain_flags):
            if not m and v != 0.0:
                d_pts = d_flat.reshape(-1, 3)
                key_pts = basis_pts + d_pts
                key_diff = key_pts[seg_end] - key_pts[seg_start]
                l_key = np.linalg.norm(key_diff, axis=1, keepdims=True)
                delta_l = l_key - l_basis
                has_len_change = np.abs(delta_l) > 1e-5
                delta_l_sum += v * delta_l
                weight_len_sum += np.where(has_len_change, abs(v), 0.0)

        norm_factor = np.where(weight_len_sum > 1.0, weight_len_sum, 1.0)
        l_desired = l_basis + (delta_l_sum / norm_factor)
        l_desired = np.maximum(l_desired, 1e-6)

    cur_diff = out[seg_end] - out[seg_start]
    cur_norm = np.linalg.norm(cur_diff, axis=1, keepdims=True)

    bas_norm = np.where(l_basis > 1e-6, l_basis, 1.0)
    bas_unit = np.where(l_basis > 1e-6, b_diff / bas_norm, 0.0)
    unit_dirs = np.where(cur_norm > 1e-6, cur_diff / np.where(cur_norm > 1e-6, cur_norm, 1.0), bas_unit)

    step_vectors = unit_dirs * l_desired

    accum = np.zeros((N, 3), dtype=np.float32)
    accum[seg_end] = step_vectors

    curve_totals = np.add.reduceat(accum, offsets[:-1], axis=0)
    if len(offsets) > 2:
        accum[offsets[1:-1]] -= curve_totals[:-1]

    cum_offsets = np.cumsum(accum, axis=0)

    curve_indices = np.repeat(np.arange(num_curves), diffs)
    root_positions = out[offsets[:-1]][curve_indices]
    out = root_positions + cum_offsets

    return out.ravel()


_eval_state_cache = {}


def evaluate_shape_keys(obj, force=False):
    """
    Directly computes shape key deformations and writes positions to hair curve points.
    Supports Relative (with Relative To) and Non-Relative (Evaluation Time) modes.
    Vectorized with NumPy for real-time performance.
    Caches evaluated state to prevent duplicate/redundant computations.
    """
    if not obj or obj.type != 'CURVES':
        return

    curves = obj.data
    if not curves or not hasattr(curves, "points") or len(curves.points) == 0:
        return

    sk_data = getattr(obj, "hair_shape_keys", None)
    if not sk_data or len(sk_data.keys) == 0:
        return

    # If actively sculpting with a brush, do not overwrite in the middle of a stroke
    if obj.mode == 'SCULPT_CURVES' and sk_data.is_sculpting:
        return

    num_points = len(curves.points)

    # State cache lookup: avoid duplicate evaluations
    # Use session_uid (monotonic unique ID per datablock) to avoid stale pointer collisions
    obj_id = getattr(obj, "session_uid", obj.as_pointer())
    curves_id = getattr(curves, "session_uid", curves.as_pointer())
    cache_key = (obj_id, curves_id)
    eval_state = (
        sk_data.solo,
        sk_data.active_index,
        sk_data.relative,
        round(float(sk_data.eval_time), 4),
        tuple((k.name, round(float(k.value), 5), k.mute, k.maintain_length, k.relative_key) for k in sk_data.keys),
        num_points
    )
    if not force and _eval_state_cache.get(cache_key) == eval_state:
        return

    basis_attr = curves.attributes.get("sk_basis")
    if not basis_attr:
        return

    if len(basis_attr.data) != num_points:
        return

    basis_pos = np.empty(num_points * 3, dtype=np.float32)
    basis_attr.data.foreach_get('vector', basis_pos)

    key_deltas = []
    key_values = []
    maintain_flags = []

    # Solo Mode: Show ONLY the active shape key, scaled by its value (or 100% if non-relative)
    if sk_data.solo:
        active_idx = sk_data.active_index
        if active_idx == 0 or active_idx >= len(sk_data.keys):
            target_pos = basis_pos.copy()
        else:
            active_key = sk_data.keys[active_idx]
            if active_key.mute:
                target_pos = basis_pos.copy()
            else:
                val = active_key.value if sk_data.relative else 1.0
                if abs(val) < 1e-6:
                    target_pos = basis_pos.copy()
                else:
                    delta_attr = curves.attributes.get(active_key.attr_name)
                    if delta_attr and len(delta_attr.data) == num_points:
                        delta_pos = np.empty(num_points * 3, dtype=np.float32)
                        delta_attr.data.foreach_get('vector', delta_pos)

                        basis_name = sk_data.keys[0].name if len(sk_data.keys) > 0 else "Basis"
                        # Relative To: If relative to another key, evaluate delta from that reference key
                        if sk_data.relative and active_key.relative_key and active_key.relative_key != basis_name and active_key.relative_key != "Basis":
                            ref_item = next((k for k in sk_data.keys if k.name == active_key.relative_key and k != active_key), None)
                            if ref_item:
                                ref_attr = curves.attributes.get(ref_item.attr_name)
                                if ref_attr and len(ref_attr.data) == num_points:
                                    ref_pos = np.empty(num_points * 3, dtype=np.float32)
                                    ref_attr.data.foreach_get('vector', ref_pos)
                                    delta_pos = delta_pos - ref_pos

                        target_pos = basis_pos + delta_pos * val
                        key_deltas.append(delta_pos)
                        key_values.append(val)
                        maintain_flags.append(active_key.maintain_length)
                    else:
                        target_pos = basis_pos.copy()
    elif sk_data.relative:
        # Relative Blending: Basis + Sum(key.value * key.delta)
        target_pos = basis_pos.copy()
        basis_name = sk_data.keys[0].name if len(sk_data.keys) > 0 else "Basis"
        for key in sk_data.keys[1:]:
            if key.mute or key.value == 0.0:
                continue
            delta_attr = curves.attributes.get(key.attr_name)
            if delta_attr and len(delta_attr.data) == num_points:
                delta_pos = np.empty(num_points * 3, dtype=np.float32)
                delta_attr.data.foreach_get('vector', delta_pos)

                # Relative To: If relative to another key, evaluate delta from that reference key
                if key.relative_key and key.relative_key != basis_name and key.relative_key != "Basis":
                    ref_item = next((k for k in sk_data.keys if k.name == key.relative_key and k != key), None)
                    if ref_item:
                        ref_attr = curves.attributes.get(ref_item.attr_name)
                        if ref_attr and len(ref_attr.data) == num_points:
                            ref_pos = np.empty(num_points * 3, dtype=np.float32)
                            ref_attr.data.foreach_get('vector', ref_pos)
                            delta_pos = delta_pos - ref_pos

                target_pos += delta_pos * key.value
                key_deltas.append(delta_pos)
                key_values.append(key.value)
                maintain_flags.append(key.maintain_length)
    else:
        # Non-Relative / Absolute Mode: Sequential morph driven by eval_time (10.0 per key)
        num_keys = len(sk_data.keys)
        if num_keys <= 1:
            target_pos = basis_pos.copy()
        else:
            max_t = (num_keys - 1) * 10.0
            t = max(0.0, min(float(sk_data.eval_time), max_t))
            k = min(int(t // 10.0), num_keys - 2)
            frac = (t - (k * 10.0)) / 10.0

            def get_key_pos(idx):
                if idx == 0:
                    return basis_pos
                item = sk_data.keys[idx]
                attr = curves.attributes.get(item.attr_name)
                if attr and len(attr.data) == num_points:
                    d = np.empty(num_points * 3, dtype=np.float32)
                    attr.data.foreach_get('vector', d)
                    return basis_pos + d
                return basis_pos

            pos_a = get_key_pos(k)
            # Skip muted destination key if applicable
            next_item = sk_data.keys[k + 1] if (k + 1) < num_keys else None
            if next_item and next_item.mute:
                pos_b = pos_a
            else:
                pos_b = get_key_pos(k + 1)
            target_pos = pos_a * (1.0 - frac) + pos_b * frac

            eff_delta = target_pos - basis_pos
            key_deltas.append(eff_delta)
            key_values.append(1.0)
            maintain_flags.append(sk_data.keys[k + 1].maintain_length)

    # Maintain curve length along strands without kinking or distortion
    should_maintain = any(m and v != 0.0 for m, v in zip(maintain_flags, key_values))
    if should_maintain:
        offsets = get_curve_offsets(curves)
        target_pos = enforce_curve_length(target_pos, basis_pos, offsets, key_deltas, key_values, maintain_flags)

    # Direct write to curve position attribute
    pos_attr = curves.attributes.get('position')
    if pos_attr and len(pos_attr.data) == num_points:
        pos_attr.data.foreach_set('vector', target_pos)
        curves.update_tag()
        obj.update_tag(refresh={'DATA'})
        if len(_eval_state_cache) > 200:
            _eval_state_cache.clear()
        _eval_state_cache[cache_key] = eval_state
