# B Hair ShapeKey

**Direct Hair Curves Shape Keys with Keyframing, Drivers, Shape Key**

---

## Overview

**B Hair ShapeKey** brings full,shape key functionality to modern Blender **Hair Curves** (`Curves` datablocks). 

**B Hair ShapeKey** implementing attribute-based shape key integrated directly into **Properties > Data Properties > Shape Keys**.

Whether you are creating dynamic character hairstyles, facial hair animation, cloth/hair collision morphs, or complex armature-driven hair rigs, **B Hair ShapeKey** provides an intuitive, and real-time workflow.

---

## All Features

### 1. Direct Attribute Deformation
- **Pure NumPy Vectorized Performance**: Hair point deformations evaluate in real-time during viewport playback and rendering.
- **Zero Geometry Nodes Overhead**: Operates directly on native curve point attributes (`position`, `sk_basis`, `sk_delta_*`) without modifier bloat or tree evaluation delays.
- **Session Cache**: Dynamic evaluation cache prevents redundant recalculations when values are static.

### 2. Interactive Sculpt Curves Workflow
- **One-Click Grooming**: Click **Groom '[Key Name]'** to enter Sculpt Curves mode immediately on the active key.
- **Native Brush Support**: Sculpt freely with all native Blender hair brushes (Comb, Snake Hook, Pinch, Smooth, Grow/Shrink, etc.).
- **Automatic & Manual Delta Baking**: Click **Save & Finish Grooming** or simply switch back to Object Mode—sculpt displacement is automatically extracted and baked into shape key deltas.

### 3. Keyframing & Rigging Driver Integration
- **Timeline Keyframing**: Keyframe `Value` sliders directly with `I` or the animate button on any frame.
- **Full Driver Support**: Right-click the `Value` slider and choose **Add Driver** to drive hair shapes from character Armature bones, facial bones, action constraints, or custom properties.
- **Viewport Dynamic Sync**: Depsgraph handlers automatically update driven hair shape keys during interactive 3D viewport posing.

### 4. Smart Basis Management & 2nd Key Promotion
- **Non-Destructive Basis Removal**: Removing the `Basis` key automatically promotes the next shape key (e.g. `Key 1`) to become the new `Basis`.
- **Coordinate Re-Projection**: All subsequent shape keys (`Key 2`, `Key 3`, etc.) have their deformation deltas automatically re-projected so hair poses remain identical without distortion or data loss.

### 5. Strand Length Conservation (`Maintain Curve Length`)
- **Anti-Chord Shrinking**: Prevents unnatural hair shrinking when strands bend sharply between keyed poses.
- **Selective Preservation**: Hair shape keys with `Maintain Curve Length` enabled conserve rest strand length, while shape keys with deliberate length scaling (e.g. growing hair) are preserved.

### 6. Bilateral Symmetry Mirroring (`Flip X, Y, Z`)
- **Bilateral KDTree Strand Pairing**: Intelligently pairs corresponding strands across symmetry planes using 3D root proximity.
- **Multi-Axis Support**:
  - `Flip X`: Mirrors Left to Right / Right to Left.
  - `Flip Y`: Mirrors Front to Back / Back to Front.
  - `Flip Z`: Mirrors Top to Bottom / Bottom to Top.
- **Reciprocal Mutual Guard**: Prevents strand collisions or double-mapping on asymmetrical hair cards.

### 7. Relative & Absolute (Non-Relative) Modes
- **Relative Mode**:
  - Independent `Value` sliders for blending multiple hair shapes simultaneously.
  - Per-key customizable `Range Min` and `Max` (supporting negative values, overshooting, or clamp limits).
  - DAG-compliant `Relative To` reference dropdown to build sequential or corrective shapes.
- **Absolute Mode**:
  - Seamless shape transitions driven by continuous **Evaluation Time** (`eval_time`).
  - Useful for morphing hair through predefined animation sequences.

### 8. Solo & Mute Workflow
- **Solo Mode (`SOLO_ON` / `SOLO_OFF`)**: Isolates the active shape key's deformation instantly to inspect shapes without adjusting other sliders.
- **Per-Key Mute**: Toggle visibility of individual shape keys on and off without deleting animation keys.
- **Lock Protection**: Lock shape keys against accidental sculpting or slider modification.

### 9. Specials Menu Operations
Access comprehensive shape key utilities from the dropdown menu (down arrow icon beside list):
- **New Combined**: Bakes the currently evaluated mixed shape into a fresh, new shape key.
- **Duplicate**: Clones the active shape key with all its point deltas and settings.
- **Flip Submenu**: Quick access to Flip X, Flip Y, and Flip Z.
- **Lock All / Unlock All**: Batch locks or unlocks all keys in the list.
- **Make Basis**: Promotes any selected shape key to become the new reference Basis shape.
- **Apply to Basis**: Merges active shape key displacement permanently into the rest shape.
- **Apply All**: Permanently applies all current deformations to the base hair geometry and resets shape keys.
- **Clear All Values**: Resets all relative shape key values to 0.0.
- **Delete All**: Cleans up all shape keys and restores original rest positions.

### 10. Downstream Geometry Nodes & Shading Compatibility
- **Add Rest Position**: Toggle to automatically generate and maintain a standard `rest_position` attribute on curve points, ensuring compatibility with downstream hair shading and Geometry Nodes deformation modifiers.

---

## Usage Guide

### Getting Started

1. **Select Hair Curves**: In the 3D Viewport, select any modern Hair Curves object (created via `Add > Curves > Empty Hair` or groomed hair).
2. **Locate Panel**: Navigate to the **Properties Editor > Data Properties tab (Curves icon) > Shape Keys**.

```
Properties
└── Data Properties [Curves Icon]
    └── Shape Keys
        ├── [UI List: Basis, Key 1, Key 2...]
        ├── [+] [-] [v Specials] [^] [v]
        ├── Add Rest Position
        ├── Relative [x] | Solo [Icon]
        └── Value Sliders & Range Settings
```

---

### Step-by-Step Workflows

#### Creating Your First Hair Shape Key
1. In the **Shape Keys** panel, click the **`+` (Add)** button.
   - The first key added automatically becomes **`Basis`** (the rest reference shape).
2. Click **`+`** again to add a new shape key (e.g., **`Key 1`**).

#### Sculpting a Hair Shape
1. Select **`Key 1`** in the list.
2. Click **Groom 'Key 1'** (or switch to **Sculpt Curves** mode from the 3D Viewport mode dropdown).
3. Use the **Comb**, **Snake Hook**, or **Pinch** brushes to groom the hair into the desired shape.
4. When finished, click **Save & Finish Grooming** in the panel (or switch back to **Object Mode**).
5. Move the **Value** slider to blend between the **Basis** shape and your sculpted hair shape.

#### Keyframing & Timeline Animation
1. Move the timeline playhead to frame 1.
2. Set `Value` to `0.0`, right-click the slider, and choose **Insert Keyframe** (or press `I` hovering over the slider).
3. Move to frame 20, change `Value` to `1.0`, and insert a keyframe.
4. Play back the timeline (`Spacebar`) to see real-time hair shape interpolation.

#### Driving Hair with Bones (Facial Rigging / Wind / Gravity)
1. Right-click the **Value** slider on your hair shape key.
2. Select **Add Driver**.
3. In the Driver Popover, set:
   - **Type**: `Averaged Value` or `Scripted Expression`.
   - **Prop**: Select your character **Armature**.
   - **Bone**: Select the controlling bone (e.g. `hair_front_L` or `jaw_master`).
   - **Type**: `X/Y/Z Location` or `Rotation`.
4. Rotate or pose the bone in Pose Mode—the hair curves deform interactively.

#### Mirroring Hair Shapes (`Flip X`)
1. Groom a shape on one side (e.g., left hair sweep).
2. In the specials menu (down arrow icon), select **Duplicate** to make a copy.
3. With the copy selected, open the specials menu and select **Flip > Flip X (Left / Right)**.
4. The hair deltas are mirrored across the local X symmetry plane.

---

## Technical Specifications

- **Target Blender Versions**: Blender 5.2+ (and 4.2+ LTS)
- **Supported Datablocks**: Hair Curves (`CURVES`)
- **License**: GNU General Public License v3 or later (`SPDX:GPL-3.0-or-later`)
- **Author**: Dinesh007
- **Extension ID**: `b_hair_shapekey`
