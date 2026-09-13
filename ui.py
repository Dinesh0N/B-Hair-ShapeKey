"""
UI components for Hair Shape Keys: Menus, UIList, and Data Panel.
"""

import bpy
from .properties import set_slider_soft_range


class HAIR_MT_FlipMenu(bpy.types.Menu):
    bl_label = "Flip"
    bl_idname = "HAIR_MT_flip_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator("hair_sk.flip_key", text="Flip X (Left / Right)").axis = 'X'
        layout.operator("hair_sk.flip_key", text="Flip Y (Front / Back)").axis = 'Y'
        layout.operator("hair_sk.flip_key", text="Flip Z (Top / Bottom)").axis = 'Z'


class HAIR_MT_SpecialsMenu(bpy.types.Menu):
    bl_label = "Shape Key Specials"
    bl_idname = "HAIR_MT_specials_menu"

    def draw(self, context):
        layout = self.layout

        layout.operator("hair_sk.new_combined", icon='ADD', text="New Combined")
        layout.operator("hair_sk.duplicate_key", icon='DUPLICATE', text="Duplicate")
        layout.separator()
        layout.menu("HAIR_MT_flip_menu", icon='ARROW_LEFTRIGHT', text="Flip")
        layout.separator()
        layout.operator("hair_sk.lock_all", icon='LOCKED', text="Lock All").action = 'LOCK'
        layout.operator("hair_sk.lock_all", icon='UNLOCKED', text="Unlock All").action = 'UNLOCK'
        layout.separator()
        layout.operator("hair_sk.make_basis", text="Make Basis")
        layout.operator("hair_sk.apply_to_base", text="Apply to Basis")
        layout.separator()
        layout.operator("hair_sk.apply_all", text="Apply All")
        layout.operator("hair_sk.clear_keys", text="Clear All Values")
        layout.separator()
        layout.operator("hair_sk.delete_all", icon='X', text="Delete All")


class HAIR_UL_ShapeKeysList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if index == 0:
            # Basis Shape Key (Protected from accidental editing; cannot be muted as it is the reference)
            row = layout.row(align=True)
            row.label(text="", icon='PARTICLEMODE')
            row.label(text=item.name)
            row.label(text="")  # Empty column matching slider of other keys
            row.label(text="", icon='BLANK1')  # Placeholder matching mute column
            row.prop(item, "lock", text="", icon='LOCKED' if item.lock else 'UNLOCKED', emboss=False)
        else:
            # Non-Basis Shape Keys
            row = layout.row(align=True)
            row.label(text="", icon='PARTICLEMODE')
            row.prop(item, "name", text="", emboss=False)

            if hasattr(data, "relative") and not data.relative:
                # Non-relative / absolute mode shows target keyframe evaluation time
                sub = row.row(align=True)
                sub.alignment = 'RIGHT'
                sub.label(text=f"{index * 10.0:.1f}")
            else:
                set_slider_soft_range(item)
                sub = row.row(align=True)
                sub.enabled = not item.lock
                sub.prop(item, "value", text="", slider=True, emboss=True)

            row.prop(item, "mute", text="", icon='CHECKBOX_DEHLT' if item.mute else 'CHECKBOX_HLT', emboss=False)
            row.prop(item, "lock", text="", icon='LOCKED' if item.lock else 'UNLOCKED', emboss=False)


class HAIR_PT_DataShapeKeysPanel(bpy.types.Panel):
    bl_label = "Shape Keys"
    bl_idname = "HAIR_PT_curves_shape_keys_direct"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'CURVES'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        sk_data = obj.hair_shape_keys

        # Main List
        row = layout.row()
        row.template_list(
            "HAIR_UL_ShapeKeysList", "",
            sk_data, "keys",
            sk_data, "active_index",
            rows=4
        )

        col = row.column(align=True)
        col.operator("hair_sk.add_key", icon='ADD', text="")
        col.operator("hair_sk.remove_key", icon='REMOVE', text="")
        col.separator()
        col.menu("HAIR_MT_specials_menu", icon='DOWNARROW_HLT', text="")

        if len(sk_data.keys) > 2 and sk_data.active_index > 0:
            col.separator()
            sub = col.column(align=True)
            sub.operator("hair_sk.move_key", icon='TRIA_UP', text="").direction = 'UP'
            sub.operator("hair_sk.move_key", icon='TRIA_DOWN', text="").direction = 'DOWN'

        # Rest Position Toggle (Matches Mesh Shape Keys)
        layout.prop(sk_data, "add_rest_position", text="Add Rest Position")

        # Relative row with Solo toggle
        if len(sk_data.keys) > 0:
            split = layout.split(factor=0.4)
            left_row = split.row()
            left_row.prop(sk_data, "relative", text="Relative")

            right_row = split.row(align=True)
            right_row.alignment = 'RIGHT'
            right_row.prop(
                sk_data,
                "solo",
                text="",
                icon='SOLO_ON' if sk_data.solo else 'SOLO_OFF'
            )

        # Active Key Detailed Sub-properties
        if len(sk_data.keys) > 1:
            if not sk_data.relative:
                # Non-Relative / Absolute Mode: Evaluation Time
                col = layout.column(align=True)
                col.use_property_split = True
                col.prop(sk_data, "eval_time", text="Evaluation Time", slider=True)
                if sk_data.active_index > 0:
                    active_key = sk_data.keys[sk_data.active_index]
                    col.prop(active_key, "maintain_length", text="Maintain Curve Length")
            elif sk_data.active_index > 0:
                # Relative Mode Properties
                active_key = sk_data.keys[sk_data.active_index]
                set_slider_soft_range(active_key)
                col = layout.column(align=True)
                col.use_property_split = True
                col.enabled = not active_key.lock
                col.prop(active_key, "value", text="Value", slider=True)
                col.prop(active_key, "slider_min", text="Range Min")
                col.prop(active_key, "slider_max", text="Max")

                col.prop(active_key, "relative_key", text="Relative To")
                col.prop(active_key, "maintain_length", text="Maintain Curve Length")

        # Grooming / Sculpt Workflow
        if len(sk_data.keys) > 0:
            box = layout.box()
            if context.mode == 'SCULPT_CURVES' or sk_data.is_sculpting:
                box.alert = True
                box.operator("hair_sk.bake_sculpt", text="Save & Finish Grooming", icon='FILE_TICK')
            else:
                idx = sk_data.active_index
                key_name = sk_data.keys[idx].name if idx < len(sk_data.keys) else "Active Key"
                sub = box.row()
                if idx < len(sk_data.keys) and sk_data.keys[idx].lock:
                    sub.enabled = False
                    sub.operator("hair_sk.enter_sculpt", text=f"Locked: '{key_name}'", icon='LOCKED')
                else:
                    sub.operator("hair_sk.enter_sculpt", text=f"Groom '{key_name}'", icon='SCULPTMODE_HLT')


UI_CLASSES = (
    HAIR_MT_FlipMenu,
    HAIR_MT_SpecialsMenu,
    HAIR_UL_ShapeKeysList,
    HAIR_PT_DataShapeKeysPanel,
)
