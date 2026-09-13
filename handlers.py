"""
Persistent Timeline, Render, Depsgraph, and Load Handlers for Hair Shape Keys.
"""

import bpy
from bpy.app.handlers import persistent

from .core import evaluate_shape_keys, cleanup_legacy_geometry_nodes, _eval_state_cache
from .sculpt import capture_sculpt_start, commit_sculpt_changes
from .properties import _shape_key_ranges


@persistent
def on_frame_change_direct_eval(scene, depsgraph=None):
    """Evaluates animated shape key values across timeline playback."""
    for obj in scene.objects:
        if obj.type == 'CURVES' and hasattr(obj, "hair_shape_keys"):
            if len(obj.hair_shape_keys.keys) > 0:
                evaluate_shape_keys(obj, force=False)


@persistent
def on_render_direct_eval(scene):
    """Guarantees fresh shape key evaluation before rendering still frames (F12) or animations."""
    for obj in scene.objects:
        if obj.type == 'CURVES' and hasattr(obj, "hair_shape_keys"):
            if len(obj.hair_shape_keys.keys) > 0:
                evaluate_shape_keys(obj, force=True)


@persistent
def on_load_post_cleanup(dummy=None):
    """Clears state cache when loading a new blend file to prevent stale pointer collisions
    and pre-populates shape key ranges for curves objects."""
    _eval_state_cache.clear()
    _shape_key_ranges.clear()

    # Reset is_sculpting and pre-warm range cache for loaded curves
    try:
        for obj in bpy.data.objects:
            if obj.type == 'CURVES' and hasattr(obj, "hair_shape_keys"):
                sk_data = obj.hair_shape_keys
                sk_data.is_sculpting = False
                for k in sk_data.keys:
                    _shape_key_ranges[k.as_pointer()] = (float(k.slider_min), float(k.slider_max))
    except Exception:
        pass


_is_handling_depsgraph = False


@persistent
def on_depsgraph_mode_sync(scene, depsgraph):
    """
    1. Detects mode changes between OBJECT and SCULPT_CURVES (auto-bakes sculpt deltas on exit).
    2. Dynamically evaluates shape keys driven by Armature bones / drivers in the 3D viewport.
    """
    global _is_handling_depsgraph
    if _is_handling_depsgraph:
        return

    _is_handling_depsgraph = True
    try:
        # Part 1: Sculpt mode sync for active Curves object
        act_obj = getattr(bpy.context, "active_object", None) if hasattr(bpy, "context") else None
        if act_obj and act_obj.type == 'CURVES' and hasattr(act_obj, "hair_shape_keys"):
            sk_data = act_obj.hair_shape_keys
            if len(sk_data.keys) > 0:
                current_mode = act_obj.mode
                if current_mode == 'SCULPT_CURVES' and not sk_data.is_sculpting:
                    cleanup_legacy_geometry_nodes(act_obj)
                    capture_sculpt_start(act_obj)
                    sk_data.prev_active_index = sk_data.active_index
                    sk_data.is_sculpting = True
                elif current_mode == 'OBJECT' and sk_data.is_sculpting:
                    commit_sculpt_changes(act_obj, key_index=sk_data.prev_active_index)
                    sk_data.is_sculpting = False
                    evaluate_shape_keys(act_obj, force=True)

        # Part 2: Interactive Driver & Rigging Updates in 3D Viewport
        # When an animator poses bones in Pose mode or custom properties drive hair shape keys,
        # Blender evaluates drivers in depsgraph without firing Python RNA update callbacks.
        curves_to_check = set()
        for update in depsgraph.updates:
            id_data = update.id
            if isinstance(id_data, bpy.types.Object):
                orig = getattr(id_data, "original", id_data)
                if orig and orig.type == 'CURVES' and hasattr(orig, "hair_shape_keys"):
                    curves_to_check.add(orig)

        # Also check curves objects with drivers/animation in scene
        for obj in scene.objects:
            if obj.type == 'CURVES' and hasattr(obj, "hair_shape_keys") and len(obj.hair_shape_keys.keys) > 0:
                if obj.animation_data and (obj.animation_data.drivers or obj.animation_data.action):
                    curves_to_check.add(obj)

        for c_obj in curves_to_check:
            evaluate_shape_keys(c_obj, force=False)

    finally:
        _is_handling_depsgraph = False


def _clean_handler(handler_list, name):
    for h in list(handler_list):
        if getattr(h, "__name__", "") == name:
            try:
                handler_list.remove(h)
            except Exception:
                pass


def register_handlers():
    _clean_handler(bpy.app.handlers.frame_change_post, "on_frame_change_direct_eval")
    bpy.app.handlers.frame_change_post.append(on_frame_change_direct_eval)

    _clean_handler(bpy.app.handlers.render_pre, "on_render_direct_eval")
    bpy.app.handlers.render_pre.append(on_render_direct_eval)

    _clean_handler(bpy.app.handlers.render_init, "on_render_direct_eval")
    bpy.app.handlers.render_init.append(on_render_direct_eval)

    _clean_handler(bpy.app.handlers.depsgraph_update_post, "on_depsgraph_mode_sync")
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_mode_sync)

    _clean_handler(bpy.app.handlers.load_post, "on_load_post_cleanup")
    bpy.app.handlers.load_post.append(on_load_post_cleanup)


def unregister_handlers():
    _clean_handler(bpy.app.handlers.load_post, "on_load_post_cleanup")
    _clean_handler(bpy.app.handlers.depsgraph_update_post, "on_depsgraph_mode_sync")
    _clean_handler(bpy.app.handlers.frame_change_post, "on_frame_change_direct_eval")
    _clean_handler(bpy.app.handlers.render_pre, "on_render_direct_eval")
    _clean_handler(bpy.app.handlers.render_init, "on_render_direct_eval")
