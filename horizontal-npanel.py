bl_info = {
    "name": "Horizontal N-Panel Tabs",
    "author": "K(AI)mil",
    "version": (1, 2, 1),
    "blender": (4, 0, 0),
    "location": "3D View / Image / UV / Node Editors > N-Panel",
    "description": "Replaces vertical N-Panel tabs with horizontal tabs in 3D, Image/UV, and Node editors",
    "category": "Interface",
}

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
import ast
import ctypes
import json
import math
import os
import time

CONFIG_PATH = os.path.join(bpy.utils.user_resource('CONFIG'), "npanel_horizontal_tabs.json")

SPACE_TYPE_NAMES = (
    "SpaceView3D",
    "SpaceImageEditor",
    "SpaceNodeEditor",
)

SUPPORTED_AREA_TYPES = {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'}

STATE = {
    "spaces": {},
    "area_ui": {},
    "hover_pos": (-1, -1),
    "mouse_down": False,
    "flash_target": None,
    "flash_until": 0.0,
}

AREA_DATA = {}
WINRCT_OFFSETS = None
DRAW_HANDLERS = []
MODAL_WINDOWS = set()


def get_space_config(space_type):
    spaces = STATE["spaces"]
    if space_type not in spaces:
        spaces[space_type] = {"order": [], "pinned": [], "hidden": []}
    return spaces[space_type]


def get_area_ui(area_ptr):
    ui = STATE["area_ui"]
    if area_ptr not in ui:
        ui[area_ptr] = {"scroll": 0, "show_hidden": False}
    return ui[area_ptr]


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "spaces" in data and isinstance(data["spaces"], dict):
                    STATE["spaces"] = data["spaces"]
                elif "order" in data:
                    STATE["spaces"]["VIEW_3D"] = {
                        "order": list(data.get("order", [])),
                        "pinned": list(data.get("pinned", [])),
                        "hidden": list(data.get("hidden", [])),
                    }
        except Exception:
            pass


def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"spaces": STATE["spaces"]}, f, indent=2)
    except Exception:
        pass


def get_uniform_shader():
    try:
        return gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        return gpu.shader.from_builtin('2D_UNIFORM_COLOR')


def set_font_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, int(size), 72)


def find_winrct_offsets(ui_region):
    """Finds and verifies ARegion.winrct and drawrct offsets via live RNA property check."""
    global WINRCT_OFFSETS
    if WINRCT_OFFSETS is not None:
        return WINRCT_OFFSETS

    ptr = ui_region.as_pointer()
    if not ptr:
        return None

    rx, ry = ui_region.x, ui_region.y
    ints = (ctypes.c_int32 * 256).from_address(ptr)

    winrct_idx = None
    for i in range(2, 240):
        if ints[i] == rx and ints[i + 2] == ry:
            orig = ints[i]
            ints[i] = orig + 1
            matched = (ui_region.x == orig + 1)
            ints[i] = orig
            if matched:
                winrct_idx = i
                break

    if winrct_idx is None:
        return None

    offsets = [winrct_idx]
    if (
        ints[winrct_idx + 4] == ints[winrct_idx]
        and ints[winrct_idx + 5] == ints[winrct_idx + 1]
        and ints[winrct_idx + 6] == ints[winrct_idx + 2]
        and ints[winrct_idx + 7] == ints[winrct_idx + 3]
    ):
        offsets.append(winrct_idx + 4)

    WINRCT_OFFSETS = offsets
    return offsets


def set_ui_region_rect(area, ui_region, shift_left_px, touch_right_edge=False):
    if getattr(ui_region, "alignment", 'RIGHT') != 'RIGHT':
        return False

    offsets = find_winrct_offsets(ui_region)
    if not offsets:
        return False

    u_ptr = ui_region.as_pointer()
    if not u_ptr:
        return False

    idx0 = offsets[0]
    u_ints = (ctypes.c_int32 * (max(offsets) + 4)).from_address(u_ptr)

    area_xmax = area.x + area.width - 1
    rct_span = ui_region.width - 1
    shifted_xmax = area_xmax - shift_left_px
    shifted_xmin = shifted_xmax - rct_span
    target_xmax = area_xmax if touch_right_edge else shifted_xmax

    was_stretched = (u_ints[idx0 + 1] - u_ints[idx0]) > (rct_span + 2)

    for idx in offsets:
        u_ints[idx] = shifted_xmin
        u_ints[idx + 1] = target_xmax

    return was_stretched


def draw_rounded_tab(shader, x, y, w, h, radius, fill_color, outline_color=None, flat_left=True):
    r = max(1.0, min(radius, w * 0.5, h * 0.5))
    segs = 5
    verts = []

    for i in range(segs + 1):
        ang = -math.pi * 0.5 + (math.pi * 0.5) * (i / segs)
        verts.append((x + w - r + math.cos(ang) * r, y + r + math.sin(ang) * r))

    for i in range(segs + 1):
        ang = 0.0 + (math.pi * 0.5) * (i / segs)
        verts.append((x + w - r + math.cos(ang) * r, y + h - r + math.sin(ang) * r))

    if flat_left:
        verts.append((x, y + h))
        verts.append((x, y))
    else:
        for i in range(segs + 1):
            ang = math.pi * 0.5 + (math.pi * 0.5) * (i / segs)
            verts.append((x + r + math.cos(ang) * r, y + h - r + math.sin(ang) * r))
        for i in range(segs + 1):
            ang = math.pi + (math.pi * 0.5) * (i / segs)
            verts.append((x + r + math.cos(ang) * r, y + r + math.sin(ang) * r))

    cx, cy = x + w * 0.5, y + h * 0.5
    fan_verts = [(cx, cy)] + verts + [verts[0]]
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": fan_verts})
    shader.bind()
    shader.uniform_float("color", fill_color)
    batch.draw(shader)

    if outline_color is not None:
        line_verts = [verts[-1]] + verts if flat_left else (verts + [verts[0]])
        line_batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": line_verts})
        shader.bind()
        shader.uniform_float("color", outline_color)
        line_batch.draw(shader)


def draw_rect(shader, x, y, w, h, color):
    verts = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=((0, 1, 2), (0, 2, 3)))
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_triangle(shader, x1, y1, x2, y2, x3, y3, color):
    batch = batch_for_shader(shader, 'TRIS', {"pos": ((x1, y1), (x2, y2), (x3, y3))})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_cross(shader, cx, cy, r, color, scale=1.0):
    gpu.state.line_width_set(max(2.0, 2.3 * scale))
    verts = ((cx - r, cy - r), (cx + r, cy + r), (cx - r, cy + r), (cx + r, cy - r))
    batch = batch_for_shader(shader, 'LINES', {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def draw_pushpin_icon(shader, cx, cy, scale, is_pinned, is_hovered=False):
    s = 4.8 * scale
    angle = -0.45 if is_pinned else -0.65
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    def rot(px, py):
        return (cx + (px * cos_a - py * sin_a) * s, cy + (px * sin_a + py * cos_a) * s)

    if is_pinned:
        head_col = (1.0, 0.78, 0.18, 1.0) if not is_hovered else (1.0, 0.88, 0.38, 1.0)
        needle_col = (0.92, 0.95, 1.0, 1.0)
    else:
        head_col = (0.85, 0.85, 0.85, 0.95) if is_hovered else (0.62, 0.62, 0.62, 0.85)
        needle_col = (0.72, 0.72, 0.72, 0.90)

    n_start = rot(0.0, -0.10)
    n_end = rot(0.0, -1.22)

    gpu.state.line_width_set(max(2.5, 2.8 * scale))
    shadow_batch = batch_for_shader(shader, 'LINES', {"pos": (n_start, n_end)})
    shader.bind()
    shader.uniform_float("color", (0.05, 0.05, 0.05, 0.75))
    shadow_batch.draw(shader)

    gpu.state.line_width_set(max(1.6, 1.9 * scale))
    needle_batch = batch_for_shader(shader, 'LINES', {"pos": (n_start, n_end)})
    shader.bind()
    shader.uniform_float("color", needle_col)
    needle_batch.draw(shader)
    gpu.state.line_width_set(1.0)

    body_local = (
        (-0.68, 1.05), (0.68, 1.05), (0.68, 0.68), (-0.68, 0.68),
        (-0.26, 0.68), (0.26, 0.68), (0.34, 0.12), (-0.34, 0.12),
        (-0.92, 0.12), (0.92, 0.12), (0.92, -0.20), (-0.92, -0.20),
    )
    verts = [rot(px, py) for px, py in body_local]
    indices = (
        (0, 1, 2), (0, 2, 3),
        (4, 5, 6), (4, 6, 7),
        (8, 9, 10), (8, 10, 11),
    )

    batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", head_col)
    batch.draw(shader)

    outline_loop = [
        verts[0], verts[1], verts[2], verts[5],
        verts[6], verts[9], verts[10], verts[11],
        verts[8], verts[7], verts[4], verts[3], verts[0],
    ]
    out_batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": outline_loop})
    shader.bind()
    shader.uniform_float("color", (0.08, 0.08, 0.08, 0.65))
    out_batch.draw(shader)


def is_button_active_flash(area_ptr, action, cat):
    if STATE["flash_target"] == (area_ptr, action, cat) and time.monotonic() < STATE["flash_until"]:
        return True
    return False


def draw_icon_button_bg(shader, x, y, w, h, radius, is_hov, is_press, kind="DEFAULT"):
    pad_x = 1
    pad_y = 2
    bx, by, bw, bh = x + pad_x, y + pad_y, w - pad_x * 2, h - pad_y * 2

    if is_press:
        if kind == "DANGER":
            bg = (0.95, 0.18, 0.18, 1.0)
            outline = (1.0, 0.55, 0.55, 1.0)
        elif kind == "PIN":
            bg = (0.85, 0.62, 0.10, 1.0)
            outline = (1.0, 0.88, 0.40, 1.0)
        else:
            bg = (0.28, 0.52, 0.85, 1.0)
            outline = (0.55, 0.78, 1.0, 1.0)
    elif is_hov:
        if kind == "DANGER":
            bg = (0.78, 0.20, 0.20, 0.96)
            outline = (0.98, 0.40, 0.40, 1.0)
        elif kind == "PIN":
            bg = (0.42, 0.36, 0.18, 0.95)
            outline = (0.85, 0.70, 0.25, 0.95)
        else:
            bg = (0.32, 0.32, 0.32, 0.95)
            outline = (0.52, 0.52, 0.52, 0.95)
    else:
        if kind == "DANGER":
            bg = (0.28, 0.13, 0.13, 0.75)
            outline = None
        else:
            bg = (0.08, 0.08, 0.08, 0.55)
            outline = None

    draw_rounded_tab(shader, bx, by, bw, bh, radius, bg, outline, flat_left=False)


def get_native_gutter_rect(context, ui_region):
    scale = context.preferences.system.ui_scale
    gutter_w = int(round(27 * scale))
    if getattr(ui_region, "alignment", 'RIGHT') == 'LEFT':
        return 0, gutter_w
    return max(0, ui_region.width - gutter_w), gutter_w


def get_native_categories(ui_region, space_type):
    cats = []
    try:
        ui_region.active_panel_category = "__query_tabs__"
    except TypeError as e:
        msg = str(e)
        idx = msg.find("(")
        if idx != -1:
            try:
                parsed = ast.literal_eval(msg[idx:])
                if isinstance(parsed, str) and parsed:
                    cats = [parsed]
                elif isinstance(parsed, (tuple, list)):
                    cats = [c for c in parsed if isinstance(c, str) and c]
            except Exception:
                pass
    except Exception:
        pass

    if not cats:
        for name in dir(bpy.types):
            cls = getattr(bpy.types, name, None)
            if isinstance(cls, type) and issubclass(cls, bpy.types.Panel):
                if getattr(cls, "bl_space_type", "") == space_type and getattr(cls, "bl_region_type", "") == 'UI':
                    if getattr(cls, "is_registered", False) and not getattr(cls, "bl_parent_id", None):
                        cat = getattr(cls, "bl_category", "Misc")
                        if cat not in cats:
                            cats.append(cat)
    return cats


def sync_and_sort_categories(space_type, raw_cats):
    cfg = get_space_config(space_type)
    for c in raw_cats:
        if c not in cfg["order"]:
            cfg["order"].append(c)

    ordered = [c for c in cfg["order"] if c in raw_cats]
    pinned = [c for c in ordered if c in cfg["pinned"] and c not in cfg["hidden"]]
    unpinned = [c for c in ordered if c not in cfg["pinned"] and c not in cfg["hidden"]]
    hidden = [c for c in ordered if c in cfg["hidden"]]
    return pinned + unpinned, hidden


def compute_bar_width(context, raw_cats):
    scale = context.preferences.system.ui_scale
    font_id = 0
    set_font_size(font_id, int(11 * scale))
    max_text_w = max([blf.dimensions(font_id, c)[0] for c in raw_cats] + [int(60 * scale)])
    return int(min(185 * scale, max(120 * scale, max_text_w + 30 * scale)))


def truncate_text(font_id, text, max_width):
    if blf.dimensions(font_id, text)[0] <= max_width:
        return text
    while len(text) > 1 and blf.dimensions(font_id, text + "..")[0] > max_width:
        text = text[:-1]
    return text + ".."


def get_shift_params(context, area):
    space = context.space_data
    if not area or area.type not in SUPPORTED_AREA_TYPES or not space or not getattr(space, "show_region_ui", True):
        return None, None, 0, 0, 0, 0

    ui_region = next(
        (r for r in area.regions if r.type == 'UI' and getattr(r, "alignment", 'RIGHT') == 'RIGHT' and r.width > 1),
        None,
    )
    win_region = next((r for r in area.regions if r.type == 'WINDOW' and r.width > 1), None)
    if not win_region or not ui_region or ui_region.height <= 1:
        return None, None, 0, 0, 0, 0

    raw_cats = get_native_categories(ui_region, area.type)
    if not raw_cats:
        return None, None, 0, 0, 0, 0

    bar_w = compute_bar_width(context, raw_cats)
    gx, gw = get_native_gutter_rect(context, ui_region)
    shift_px = max(0, bar_w - gw)
    return win_region, ui_region, bar_w, gx, gw, shift_px


def draw_callback_pre_win():
    context = bpy.context
    area = context.area
    win_region, ui_region, _, _, _, shift_px = get_shift_params(context, area)
    if win_region and ui_region and context.region == win_region:
        set_ui_region_rect(area, ui_region, shift_px, touch_right_edge=True)


def draw_callback_keep_shifted():
    context = bpy.context
    area = context.area
    win_region, ui_region, _, _, _, shift_px = get_shift_params(context, area)
    if win_region and ui_region:
        set_ui_region_rect(area, ui_region, shift_px, touch_right_edge=False)


def draw_callback_ui():
    context = bpy.context
    area = context.area
    win_region, ui_region, _, gx, gw, shift_px = get_shift_params(context, area)
    if not win_region or not ui_region:
        return

    was_stretched = set_ui_region_rect(area, ui_region, shift_px, touch_right_edge=False)
    if was_stretched:
        ui_region.tag_redraw()

    shader = get_uniform_shader()
    verts = ((gx, 0), (gx + gw, 0), (gx + gw, ui_region.height), (gx, ui_region.height))
    batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=((0, 1, 2), (0, 2, 3)))

    gpu.state.blend_set('NONE')
    shader.bind()
    if getattr(ui_region, "overlap", True):
        shader.uniform_float("color", (0.0, 0.0, 0.0, 0.0))
    else:
        shader.uniform_float("color", (0.16, 0.16, 0.16, 1.0))
    batch.draw(shader)


def draw_callback_window():
    if not bpy.app.timers.is_registered(ensure_modal_timer):
        bpy.app.timers.register(ensure_modal_timer, first_interval=0.05)

    context = bpy.context
    area = context.area
    if not area:
        return

    area_ptr = area.as_pointer()
    win_region, ui_region, bar_w, gx, gw, shift_px = get_shift_params(context, area)
    if not win_region or not ui_region:
        AREA_DATA.pop(area_ptr, None)
        return

    if context.region != win_region:
        return

    set_ui_region_rect(area, ui_region, shift_px, touch_right_edge=False)

    space_type = area.type
    cfg = get_space_config(space_type)
    area_ui = get_area_ui(area_ptr)

    raw_cats = get_native_categories(ui_region, space_type)
    visible_cats, hidden_cats = sync_and_sort_categories(space_type, raw_cats)
    active_cat = getattr(ui_region, "active_panel_category", "")

    area_right = area.x + area.width
    dx = max(0, area_right - (win_region.x + win_region.width))
    dy = ui_region.y - win_region.y

    vx, vy, vw, vh = gpu.state.viewport_get()
    sx, sy, sw, sh = gpu.state.scissor_get()
    if dx != 0 or dy != 0 or win_region.height != ui_region.height:
        gpu.state.viewport_set(vx + dx, vy + dy, vw, vh)
        gpu.state.scissor_set(sx + dx, ui_region.y, sw, ui_region.height)

    origin_x = win_region.x + dx
    origin_y = ui_region.y

    scale = context.preferences.system.ui_scale
    font_id = 0
    set_font_size(font_id, int(11 * scale))

    row_h = int(22 * scale)
    gap = int(3 * scale)
    radius = 4.0 * scale
    btn_w = int(19 * scale)
    btn_rad = 3.0 * scale

    left_x = min((ui_region.x + gx) - origin_x - 1, (area_right - origin_x) - bar_w - 1)
    right_x = left_x + bar_w
    top_y = ui_region.height - int(10 * scale)
    bottom_limit = int(12 * scale)

    shader = get_uniform_shader()

    if not getattr(ui_region, "overlap", True) or dx > 0:
        gpu.state.blend_set('NONE')
        draw_rect(shader, left_x, 0, bar_w + 4, ui_region.height, (0.16, 0.16, 0.16, 1.0))

    items = [("TAB", c) for c in visible_cats]
    if hidden_cats:
        items.append(("HIDDEN_HEADER", f"Hidden ({len(hidden_cats)})"))
        if area_ui["show_hidden"]:
            for hc in hidden_cats:
                items.append(("HIDDEN_ITEM", hc))

    max_rows = max(3, int((top_y - bottom_limit) // (row_h + gap)))
    max_scroll = max(0, len(items) - max_rows)
    area_ui["scroll"] = max(0, min(area_ui["scroll"], max_scroll))

    visible_slice = items[area_ui["scroll"] : area_ui["scroll"] + max_rows]

    mx_local = STATE["hover_pos"][0] - origin_x
    my_local = STATE["hover_pos"][1] - origin_y

    gpu.state.blend_set('ALPHA')

    col_active_bg = (0.24, 0.34, 0.50, 1.0)
    col_active_outline = (0.36, 0.56, 0.85, 1.0)
    col_accent_bar = (0.35, 0.65, 1.0, 1.0)
    col_inactive_bg = (0.11, 0.11, 0.11, 0.95)
    col_hover_bg = (0.18, 0.18, 0.18, 0.98)
    col_outline = (0.24, 0.24, 0.24, 0.95)

    cur_y = top_y
    hitboxes = []

    for item_type, cat in visible_slice:
        cur_y -= (row_h + gap)
        is_hovered = (left_x <= mx_local <= right_x and cur_y <= my_local <= cur_y + row_h)

        if item_type == "TAB":
            is_active = (cat == active_cat)
            is_pinned = (cat in cfg["pinned"])

            tab_w = bar_w if (is_active or is_hovered) else (bar_w - int(3 * scale))
            pin_x = left_x + int(3 * scale)
            up_x = left_x + tab_w - btn_w * 3 - int(2 * scale)
            dn_x = left_x + tab_w - btn_w * 2 - int(2 * scale)
            hx = left_x + tab_w - btn_w - int(2 * scale)
            label_x = pin_x + btn_w + int(2 * scale)
            label_w = max(10, up_x - label_x)

            hov_pin = is_hovered and (pin_x <= mx_local < pin_x + btn_w)
            hov_sel = is_hovered and (label_x <= mx_local < label_x + label_w)
            hov_up = is_hovered and (up_x <= mx_local < up_x + btn_w)
            hov_dn = is_hovered and (dn_x <= mx_local < dn_x + btn_w)
            hov_hide = is_hovered and (hx <= mx_local <= hx + btn_w)

            press_pin = (hov_pin and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "PIN", cat)
            press_sel = (hov_sel and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "SELECT", cat)
            press_up = (hov_up and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "UP", cat)
            press_dn = (hov_dn and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "DOWN", cat)
            press_hide = (hov_hide and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "HIDE", cat)

            if press_sel:
                bg = (0.18, 0.28, 0.44, 1.0)
            elif is_active:
                bg = col_active_bg
            elif is_hovered:
                bg = col_hover_bg
            else:
                bg = col_inactive_bg

            outline = col_active_outline if (is_active or press_sel) else col_outline
            draw_rounded_tab(shader, left_x, cur_y, tab_w, row_h, radius, bg, outline, flat_left=True)

            if is_active:
                accent_w = max(2, int(3 * scale))
                draw_rect(shader, left_x, cur_y + 1, accent_w, row_h - 2, col_accent_bar)

            pin_cx = pin_x + btn_w * 0.5
            pin_cy = cur_y + row_h * 0.5 - (1.0 * scale if press_pin else 0.0)

            if is_hovered:
                draw_icon_button_bg(shader, pin_x, cur_y, btn_w, row_h, btn_rad, hov_pin, press_pin, kind="PIN")
                draw_pushpin_icon(shader, pin_cx, pin_cy, scale, is_pinned or press_pin, is_hovered=hov_pin)
                hitboxes.append((pin_x, cur_y, btn_w, row_h, "PIN", cat))

                lbl_y_off = int(5 * scale) if press_sel else int(6 * scale)
                blf.position(font_id, label_x + int(2 * scale), cur_y + lbl_y_off, 0)
                blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
                blf.draw(font_id, truncate_text(font_id, cat, label_w - int(4 * scale)))
                hitboxes.append((label_x, cur_y, label_w, row_h, "SELECT", cat))

                draw_icon_button_bg(shader, up_x, cur_y, btn_w, row_h, btn_rad, hov_up, press_up)
                cx = up_x + btn_w * 0.5
                cy = cur_y + row_h * 0.5 - (1.0 * scale if press_up else 0.0)
                tr_w, tr_h = 4.4 * scale, 3.8 * scale
                up_col = (1.0, 1.0, 1.0, 1.0) if (hov_up or press_up) else (0.85, 0.85, 0.85, 0.95)
                draw_triangle(shader, cx, cy + tr_h, cx - tr_w, cy - tr_h * 0.85, cx + tr_w, cy - tr_h * 0.85, up_col)
                hitboxes.append((up_x, cur_y, btn_w, row_h, "UP", cat))

                draw_icon_button_bg(shader, dn_x, cur_y, btn_w, row_h, btn_rad, hov_dn, press_dn)
                cx = dn_x + btn_w * 0.5
                cy = cur_y + row_h * 0.5 - (1.0 * scale if press_dn else 0.0)
                dn_col = (1.0, 1.0, 1.0, 1.0) if (hov_dn or press_dn) else (0.85, 0.85, 0.85, 0.95)
                draw_triangle(shader, cx, cy - tr_h, cx - tr_w, cy + tr_h * 0.85, cx + tr_w, cy + tr_h * 0.85, dn_col)
                hitboxes.append((dn_x, cur_y, btn_w, row_h, "DOWN", cat))

                draw_icon_button_bg(shader, hx, cur_y, btn_w, row_h, btn_rad, hov_hide, press_hide, kind="DANGER")
                cx = hx + btn_w * 0.5
                cy = cur_y + row_h * 0.5 - (1.0 * scale if press_hide else 0.0)
                x_col = (1.0, 1.0, 1.0, 1.0) if (hov_hide or press_hide) else (1.0, 0.35, 0.35, 1.0)
                draw_cross(shader, cx, cy, 3.8 * scale, x_col, scale)
                hitboxes.append((hx, cur_y, btn_w, row_h, "HIDE", cat))
            else:
                if is_pinned:
                    draw_pushpin_icon(shader, pin_cx, pin_cy, scale, is_pinned=True, is_hovered=False)
                    text_x = label_x + int(2 * scale)
                else:
                    text_x = left_x + int(9 * scale)

                blf.position(font_id, text_x, cur_y + int(6 * scale), 0)
                if is_active:
                    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
                else:
                    blf.color(font_id, 0.72, 0.72, 0.72, 1.0)
                blf.draw(font_id, truncate_text(font_id, cat, (left_x + tab_w) - text_x - int(6 * scale)))
                hitboxes.append((left_x, cur_y, tab_w, row_h, "SELECT", cat))

        elif item_type == "HIDDEN_HEADER":
            tab_w = bar_w - int(4 * scale)
            ra_w = int(btn_w * 1.7)
            ra_x = left_x + tab_w - ra_w - int(2 * scale)
            hov_ra = is_hovered and (ra_x <= mx_local <= ra_x + ra_w)
            hov_hdr = is_hovered and not hov_ra
            press_ra = (hov_ra and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "RESTORE_ALL", "")
            press_hdr = (hov_hdr and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "TOGGLE_HIDDEN", "")

            hdr_bg = (0.16, 0.16, 0.16, 0.98) if hov_hdr else (0.09, 0.09, 0.09, 0.94)
            draw_rounded_tab(shader, left_x, cur_y, tab_w, row_h, radius, hdr_bg, col_outline, True)

            blf.position(font_id, left_x + int(8 * scale), cur_y + int((5 if press_hdr else 6) * scale), 0)
            blf.color(font_id, 0.85, 0.85, 0.85, 1.0) if hov_hdr else blf.color(font_id, 0.65, 0.65, 0.65, 0.95)
            prefix = "▼ " if area_ui["show_hidden"] else "▶ "
            blf.draw(font_id, prefix + cat)
            hitboxes.append((left_x, cur_y, tab_w - ra_w, row_h, "TOGGLE_HIDDEN", ""))

            draw_icon_button_bg(shader, ra_x, cur_y, ra_w, row_h, btn_rad, hov_ra, press_ra)
            blf.position(font_id, ra_x + int(6 * scale), cur_y + int((5 if press_ra else 6) * scale), 0)
            blf.color(font_id, 0.55, 0.95, 0.55, 1.0)
            blf.draw(font_id, "All")
            hitboxes.append((ra_x, cur_y, ra_w, row_h, "RESTORE_ALL", ""))

        elif item_type == "HIDDEN_ITEM":
            tab_w = bar_w - int(8 * scale)
            press_res = (is_hovered and STATE["mouse_down"]) or is_button_active_flash(area_ptr, "RESTORE", cat)
            bg = (0.20, 0.32, 0.20, 0.98) if press_res else (col_hover_bg if is_hovered else (0.09, 0.09, 0.09, 0.88))
            draw_rounded_tab(shader, left_x, cur_y, tab_w, row_h, radius, bg, col_outline, True)

            y_off = int((5 if press_res else 6) * scale)
            blf.position(font_id, left_x + int(8 * scale), cur_y + y_off, 0)
            blf.color(font_id, 0.88, 0.88, 0.88, 1.0) if is_hovered else blf.color(font_id, 0.60, 0.60, 0.60, 0.95)
            blf.draw(font_id, truncate_text(font_id, cat, tab_w - int(24 * scale)))

            plus_x = left_x + tab_w - btn_w - int(2 * scale)
            draw_icon_button_bg(shader, plus_x, cur_y, btn_w, row_h, btn_rad, is_hovered, press_res)
            blf.position(font_id, plus_x + int(5 * scale), cur_y + y_off, 0)
            blf.color(font_id, 0.55, 0.98, 0.55, 1.0)
            blf.draw(font_id, "+")
            hitboxes.append((left_x, cur_y, tab_w, row_h, "RESTORE", cat))

    gpu.state.blend_set('NONE')
    if dx != 0 or dy != 0 or win_region.height != ui_region.height:
        gpu.state.viewport_set(vx, vy, vw, vh)
        gpu.state.scissor_set(sx, sy, sw, sh)

    panel_h = len(visible_slice) * (row_h + gap)
    screen_bounds = (origin_x + left_x, origin_y + top_y - panel_h, origin_x + right_x, origin_y + top_y)
    gutter_bounds = (origin_x + left_x, ui_region.y, area_right, ui_region.y + ui_region.height)
    screen_hits = [
        (origin_x + x, origin_y + y, origin_x + x + w, origin_y + y + h, act, val)
        for (x, y, w, h, act, val) in hitboxes
    ]

    AREA_DATA[area_ptr] = {
        "bounds": screen_bounds,
        "gutter": gutter_bounds,
        "hits": screen_hits,
        "shift_px": shift_px,
    }


def move_category(space_type, cat, direction, visible_cats):
    cfg = get_space_config(space_type)
    is_pinned = cat in cfg["pinned"]
    group = [c for c in visible_cats if (c in cfg["pinned"]) == is_pinned]
    if cat not in group:
        return
    idx = group.index(cat)
    target_idx = idx + direction
    if 0 <= target_idx < len(group):
        other = group[target_idx]
        i1, i2 = cfg["order"].index(cat), cfg["order"].index(other)
        cfg["order"][i1], cfg["order"][i2] = cfg["order"][i2], cfg["order"][i1]
        save_config()


def clear_flash_timer():
    if time.monotonic() >= STATE["flash_until"]:
        STATE["flash_target"] = None
        wm = bpy.context.window_manager
        if wm:
            for win in wm.windows:
                if win.screen:
                    for area in win.screen.areas:
                        if area.as_pointer() in AREA_DATA:
                            area.tag_redraw()
        return None
    return 0.05


def handle_bar_click(area, data, mx, my):
    ui_region = next(
        (r for r in area.regions if r.type == 'UI' and getattr(r, "alignment", 'RIGHT') == 'RIGHT' and r.width > 1),
        None,
    )
    if not ui_region:
        return False

    area_ptr = area.as_pointer()
    space_type = area.type
    cfg = get_space_config(space_type)
    area_ui = get_area_ui(area_ptr)

    raw_cats = get_native_categories(ui_region, space_type)
    visible_cats, _ = sync_and_sort_categories(space_type, raw_cats)

    for x1, y1, x2, y2, action, cat in data["hits"]:
        if x1 <= mx <= x2 and y1 <= my <= y2:
            STATE["flash_target"] = (area_ptr, action, cat)
            STATE["flash_until"] = time.monotonic() + 0.14
            if not bpy.app.timers.is_registered(clear_flash_timer):
                bpy.app.timers.register(clear_flash_timer, first_interval=0.15)

            if action == "SELECT":
                try:
                    ui_region.active_panel_category = cat
                except Exception:
                    pass
            elif action == "PIN":
                if cat in cfg["pinned"]:
                    cfg["pinned"].remove(cat)
                else:
                    cfg["pinned"].append(cat)
                save_config()
            elif action == "UP":
                move_category(space_type, cat, -1, visible_cats)
            elif action == "DOWN":
                move_category(space_type, cat, 1, visible_cats)
            elif action == "HIDE":
                if cat not in cfg["hidden"]:
                    cfg["hidden"].append(cat)
                save_config()
                new_vis, _ = sync_and_sort_categories(space_type, raw_cats)
                if new_vis and getattr(ui_region, "active_panel_category", "") == cat:
                    try:
                        ui_region.active_panel_category = new_vis[0]
                    except Exception:
                        pass
            elif action == "TOGGLE_HIDDEN":
                area_ui["show_hidden"] = not area_ui["show_hidden"]
            elif action == "RESTORE":
                if cat in cfg["hidden"]:
                    cfg["hidden"].remove(cat)
                save_config()
                try:
                    ui_region.active_panel_category = cat
                except Exception:
                    pass
            elif action == "RESTORE_ALL":
                cfg["hidden"].clear()
                save_config()

            area.tag_redraw()
            ui_region.tag_redraw()
            return True
    return False


class WM_OT_npanel_tabs_modal(bpy.types.Operator):
    bl_idname = "wm.npanel_tabs_modal"
    bl_label = "N-Panel Horizontal Tabs Modal"
    bl_options = {'INTERNAL'}

    def modal(self, context, event):
        if not context.screen:
            return {'PASS_THROUGH'}

        mx, my = event.mouse_x, event.mouse_y

        for area in context.screen.areas:
            if area.type not in SUPPORTED_AREA_TYPES:
                continue
            data = AREA_DATA.get(area.as_pointer())
            if data:
                ui_reg = next(
                    (
                        r for r in area.regions
                        if r.type == 'UI' and getattr(r, "alignment", 'RIGHT') == 'RIGHT' and r.width > 1
                    ),
                    None,
                )
                if ui_reg:
                    set_ui_region_rect(area, ui_reg, data["shift_px"], touch_right_edge=False)

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if STATE["mouse_down"]:
                STATE["mouse_down"] = False
                for area in context.screen.areas:
                    if area.as_pointer() in AREA_DATA:
                        area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            old_hx, old_hy = STATE["hover_pos"]
            STATE["hover_pos"] = (mx, my)
            for area in context.screen.areas:
                if area.type not in SUPPORTED_AREA_TYPES:
                    continue
                data = AREA_DATA.get(area.as_pointer())
                if data:
                    bx1, by1, bx2, by2 = data["bounds"]
                    if (bx1 <= mx <= bx2 and by1 <= my <= by2) or (bx1 <= old_hx <= bx2 and by1 <= old_hy <= by2):
                        area.tag_redraw()
            return {'PASS_THROUGH'}

        if event.type not in {'LEFTMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        for area in context.screen.areas:
            if area.type not in SUPPORTED_AREA_TYPES:
                continue
            area_ptr = area.as_pointer()
            data = AREA_DATA.get(area_ptr)
            if not data:
                continue

            bx1, by1, bx2, by2 = data["bounds"]
            gx1, gy1, gx2, gy2 = data["gutter"]

            if bx1 <= mx <= bx2 and by1 <= my <= by2:
                area_ui = get_area_ui(area_ptr)
                if event.type == 'WHEELUPMOUSE' and event.value == 'PRESS':
                    area_ui["scroll"] = max(0, area_ui["scroll"] - 1)
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if event.type == 'WHEELDOWNMOUSE' and event.value == 'PRESS':
                    area_ui["scroll"] += 1
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if event.type == 'LEFTMOUSE':
                    if event.value == 'PRESS':
                        STATE["mouse_down"] = True
                        handle_bar_click(area, data, mx, my)
                        area.tag_redraw()
                    return {'RUNNING_MODAL'}

            if gx1 <= mx <= gx2 and gy1 <= my <= gy2:
                if event.type == 'LEFTMOUSE':
                    return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        if context.window:
            MODAL_WINDOWS.add(context.window.as_pointer())
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def ensure_modal_timer():
    wm = bpy.context.window_manager
    if not wm or not wm.windows:
        return 0.2

    active_ptrs = {w.as_pointer() for w in wm.windows}
    MODAL_WINDOWS.intersection_update(active_ptrs)

    for win in wm.windows:
        win_ptr = win.as_pointer()
        if win_ptr not in MODAL_WINDOWS and win.screen:
            try:
                with bpy.context.temp_override(window=win, screen=win.screen):
                    bpy.ops.wm.npanel_tabs_modal('INVOKE_DEFAULT')
            except Exception:
                return 0.2
    return None


@bpy.app.handlers.persistent
def on_load_post(_dummy):
    MODAL_WINDOWS.clear()
    if not bpy.app.timers.is_registered(ensure_modal_timer):
        bpy.app.timers.register(ensure_modal_timer, first_interval=0.1)


def add_space_handler(space_cls, callback, region_type, draw_type):
    try:
        handle = space_cls.draw_handler_add(callback, (), region_type, draw_type)
        DRAW_HANDLERS.append((space_cls, handle, region_type))
    except Exception:
        pass


def register():
    load_config()
    bpy.utils.register_class(WM_OT_npanel_tabs_modal)

    for cls_name in SPACE_TYPE_NAMES:
        space_cls = getattr(bpy.types, cls_name, None)
        if space_cls is None:
            continue
        add_space_handler(space_cls, draw_callback_pre_win, 'WINDOW', 'PRE_VIEW')
        add_space_handler(space_cls, draw_callback_window, 'WINDOW', 'POST_PIXEL')
        add_space_handler(space_cls, draw_callback_ui, 'UI', 'POST_PIXEL')
        add_space_handler(space_cls, draw_callback_keep_shifted, 'HEADER', 'POST_PIXEL')
        add_space_handler(space_cls, draw_callback_keep_shifted, 'TOOL_HEADER', 'POST_PIXEL')

    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)
    bpy.app.timers.register(ensure_modal_timer, first_interval=0.1)


def unregister():
    MODAL_WINDOWS.clear()
    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)

    for space_cls, handle, region_type in DRAW_HANDLERS:
        try:
            space_cls.draw_handler_remove(handle, region_type)
        except Exception:
            pass
    DRAW_HANDLERS.clear()

    wm = bpy.context.window_manager
    if wm:
        for win in wm.windows:
            if win.screen:
                for area in win.screen.areas:
                    if area.type in SUPPORTED_AREA_TYPES:
                        for reg in area.regions:
                            if reg.type == 'UI' and reg.width > 1:
                                set_ui_region_rect(area, reg, 0, touch_right_edge=False)
                        area.tag_redraw()

    bpy.utils.unregister_class(WM_OT_npanel_tabs_modal)


if __name__ == "__main__":
    register()