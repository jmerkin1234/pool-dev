#! /usr/bin/env python

import numpy as np
from panda3d.core import LineSegs, TextNode, TransparencyAttrib

import pooltool.ani.tasks as tasks
import pooltool.ani.utils as autils
import pooltool.constants as c
from pooltool.ani.action import Action
from pooltool.ani.camera import cam, camera_states
from pooltool.ani.collision import cue_avoid
from pooltool.ani.constants import (
    elevate_sensitivity,
    english_sensitivity,
    fine_aim_step,
    max_elevate,
    max_english,
    max_stroke_speed,
    min_camera,
    min_stroke_speed,
    power_sensitivity,
    rotate_sensitivity_x,
)
from pooltool.ani.globals import Global
from pooltool.ani.hud import hud
from pooltool.ani.modes.datatypes import BaseMode, Mode
from pooltool.ani.mouse import MouseMode, mouse
from pooltool.ani.scene import visual
from pooltool.config import settings
from pooltool.objects.table.collection import prebuilt_specs
from pooltool.physics.utils import tip_contact_offset
from pooltool.ptmath.utils import norm2d
from pooltool.system.datatypes import multisystem


class AimMode(BaseMode):
    name = Mode.aim
    keymap = {
        Action.rotate_cue_left: False,
        Action.rotate_cue_right: False,
        Action.adjust_head: False,
        Action.quit: False,
        Action.stroke: False,
        Action.view: False,
        Action.zoom: False,
        Action.exec_shot: False,
        Action.power: False,
        Action.elevation: False,
        Action.english: False,
        Action.cam_save: False,
        Action.cam_load: False,
        Action.show_help: False,
        Action.pick_ball: False,
        Action.call_shot: False,
        Action.ball_in_hand: False,
        Action.introspect: False,
    }

    def __init__(self):
        super().__init__()

        # In this state, the cue sticks to the cue_avoid.min_theta
        self.magnet_theta = True
        # if cue angle is within this many degrees from cue_avoid.min_theta, it sticks
        # to cue_avoid.min_theta
        self.magnet_threshold = 0.2

        # The game starts in a fixed overhead camera. Pressing V toggles this
        # flag without changing modes, so 3D keeps the original AimMode controls.
        self.camera_is_2d = True
        self.trajectory_guide_node = None
        self.debug_info = None
        self.table_spec_info = None
        self.fine_aim_delta = 0.0

    def enter(self, load_prev_cam=False):
        mouse.mode(MouseMode.RELATIVE)

        if not visual.cue.has_focus:
            ball_id = multisystem.active.cue.cue_ball_id
            visual.cue.init_focus(visual.balls[ball_id])
        else:
            visual.cue.match_ball_position()

        # The game can update the physics cue before AimMode is entered (for example,
        # when setting the opening break). Sync the rendered cue before drawing the
        # guide so its target is based on the direction the player actually sees.
        visual.cue.set_render_state_as_object_state()
        visual.cue.show_nodes(ignore=("cue_cseg",))
        visual.cue.get_node("cue_stick").setX(0)

        if self.camera_is_2d:
            cam.set_2d_locked(True)
            self._lock_2d_camera()
        else:
            cam.set_2d_locked(False)
            # Restore the saved 3D aiming camera, then keep its original
            # fixation behavior centered on the current cue ball.
            cam.load_saved_state("aim_3d_camera", ok_if_not_exists=True)
            self._move_3d_fixation_to_cue_ball()

        self._update_trajectory_guide()
        self._init_debug_info()

        self.register_keymap_event("escape", Action.quit, True)
        self.fine_aim_delta = 0.0
        tasks.register_event("wheel_up", self.queue_fine_aim, [fine_aim_step])
        tasks.register_event("wheel_down", self.queue_fine_aim, [-fine_aim_step])
        self.register_keymap_event("t", Action.adjust_head, True)
        self.register_keymap_event("t-up", Action.adjust_head, False)
        self.register_keymap_event("mouse1", Action.zoom, True)
        self.register_keymap_event("mouse1-up", Action.zoom, False)
        self.register_keymap_event("j", Action.rotate_cue_left, True)
        self.register_keymap_event("j-up", Action.rotate_cue_left, False)
        self.register_keymap_event("k", Action.rotate_cue_right, True)
        self.register_keymap_event("k-up", Action.rotate_cue_right, False)
        self.register_keymap_event("s", Action.stroke, True)
        self.register_keymap_event("v", Action.view, True)
        self.register_keymap_event("1", Action.cam_save, True)
        self.register_keymap_event("2", Action.cam_load, True)
        self.register_keymap_event("h", Action.show_help, True)
        self.register_keymap_event("q", Action.pick_ball, True)
        self.register_keymap_event("c", Action.call_shot, True)
        self.register_keymap_event("g", Action.ball_in_hand, True)
        self.register_keymap_event("b", Action.elevation, True)
        self.register_keymap_event("b-up", Action.elevation, False)
        self.register_keymap_event("e", Action.english, True)
        self.register_keymap_event("e-up", Action.english, False)
        self.register_keymap_event("x", Action.power, True)
        self.register_keymap_event("x-up", Action.power, False)
        self.register_keymap_event("space", Action.exec_shot, True)
        self.register_keymap_event("space-up", Action.exec_shot, False)
        self.register_keymap_event("i", Action.introspect, True)
        self.register_keymap_event("i-up", Action.introspect, False)

        if settings.gameplay.cue_collision:
            tasks.add(cue_avoid.collision_task, "collision_task")

        tasks.add(self.aim_task, "aim_task")
        tasks.add(self.shared_task, "shared_task")

    def exit(self):
        tasks.remove("aim_task")
        tasks.remove("shared_task")

        self._hide_trajectory_guide()
        self._destroy_debug_info()

        if settings.gameplay.cue_collision:
            tasks.remove("collision_task")

        if self.camera_is_2d:
            cam.set_2d_locked(True)
            self._lock_2d_camera()
        else:
            cam.set_2d_locked(False)
            cam.store_state("aim_3d_camera", overwrite=True)

        cam.store_state(Mode.aim, overwrite=True)

    def aim_task(self, task):
        if self.camera_is_2d:
            # Reapply the exact overhead state every frame so zoom, cue elevation,
            # collision avoidance, or any other interaction cannot move it.
            self._lock_2d_camera()

        if self.keymap[Action.view]:
            self.keymap[Action.view] = False
            self.toggle_camera_mode()
        elif self.keymap[Action.stroke]:
            Global.mode_mgr.change_mode(Mode.stroke)
        elif self.keymap[Action.pick_ball]:
            Global.mode_mgr.change_mode(Mode.pick_ball)
        elif self.keymap[Action.call_shot]:
            Global.mode_mgr.change_mode(Mode.call_shot)
        elif self.keymap[Action.ball_in_hand]:
            Global.mode_mgr.change_mode(Mode.ball_in_hand)
        elif self.keymap[Action.zoom]:
            if self.camera_is_2d:
                self._consume_mouse_motion()
            else:
                cam.zoom_via_mouse()
        elif self.keymap[Action.adjust_head]:
            if self.camera_is_2d:
                self._consume_mouse_motion()
            else:
                cam.rotate_via_mouse(theta_only=True)
                self.cue_avoidance()
        elif self.keymap[Action.elevation]:
            self.aim_elevate_cue()
        elif self.keymap[Action.english]:
            self.apply_english()
        elif self.keymap[Action.power]:
            self.aim_apply_power()
        elif self.keymap[Action.exec_shot]:
            self.keymap[Action.exec_shot] = False
            if Global.game.shot_constraints.can_shoot():
                Global.mode_mgr.mode_stroked_from = Mode.aim
                visual.cue.set_object_state_as_render_state(skip_V0=True)
                multisystem.active.strike()
                Global.mode_mgr.change_mode(Mode.calculate)
        else:
            self.rotate()

        if self.camera_is_2d:
            self._lock_2d_camera()

        if Global.mode_mgr.mode == Mode.aim:
            self._update_trajectory_guide()

        return task.cont

    def toggle_camera_mode(self) -> None:
        """Toggle between locked 2D aiming and the original 3D aiming camera."""
        if self.camera_is_2d:
            self.camera_is_2d = False
            cam.set_2d_locked(False)
            cam.load_saved_state("aim_3d_camera", ok_if_not_exists=True)
            self._move_3d_fixation_to_cue_ball()
            self.fix_cue_stick_to_camera()
            self.cue_avoidance()
            return

        cam.store_state("aim_3d_camera", overwrite=True)
        self.camera_is_2d = True
        cam.set_2d_locked(True)
        self._lock_2d_camera()

    @staticmethod
    def _lock_2d_camera() -> None:
        """Restore the same fixed overhead camera without modifying saved 3D state."""
        cam.load_state(camera_states["7_foot_overhead"], force=True)

    @staticmethod
    def _move_3d_fixation_to_cue_ball() -> None:
        cueing_ball_id = multisystem.active.cue.cue_ball_id
        cam.move_fixation(visual.balls[cueing_ball_id].get_node("pos").getPos())

    @staticmethod
    def _consume_mouse_motion() -> None:
        """Clear relative mouse movement while a 2D camera control is disabled."""
        with mouse:
            mouse.get_dx()
            mouse.get_dy()

    def _update_trajectory_guide(self) -> None:
        """Draw cue-ball, ghost-ball, and first-object-ball aim paths."""
        self._ensure_trajectory_guide()
        system = multisystem.active
        cue_ball = system.balls[system.cue.cue_ball_id]

        if cue_ball.state.s == c.pocketed:
            self.trajectory_guide_node.hide()
            return

        cue_xy = np.asarray(cue_ball.state.rvw[0, :2], dtype=float)
        cue_radius = cue_ball.params.R
        phi = np.deg2rad(system.cue.phi)
        direction = np.array([np.cos(phi), np.sin(phi)], dtype=float)

        target, ghost_xy = self._first_target_on_ray(
            cue_xy, direction, cue_radius, system.balls.values(), system.cue.cue_ball_id
        )
        self._update_debug_info(system, target, ghost_xy)
        table = system.table
        cue_end = (
            ghost_xy
            if ghost_xy is not None
            else self._ray_to_table_edge(cue_xy, direction, table.w, table.l)
        )

        guide_z = max(0.001, cue_radius * 0.12)
        drawer = LineSegs()
        drawer.setThickness(3)
        self._draw_line(drawer, cue_xy, cue_end, guide_z, (0.1, 0.9, 1.0, 0.95))

        if target is not None and ghost_xy is not None:
            target_xy = np.asarray(target.state.rvw[0, :2], dtype=float)
            object_direction = target_xy - ghost_xy
            object_distance = np.linalg.norm(object_direction)
            if object_distance > 1e-12:
                object_direction /= object_distance
                object_end = self._ray_to_table_edge(
                    target_xy, object_direction, table.w, table.l
                )
                self._draw_line(
                    drawer,
                    target_xy,
                    object_end,
                    guide_z,
                    (1.0, 0.65, 0.1, 0.95),
                )

            self._draw_circle(drawer, ghost_xy, cue_radius, guide_z)

        self._replace_trajectory_geometry(drawer)
        self.trajectory_guide_node.show()

    @staticmethod
    def _first_target_on_ray(cue_xy, direction, cue_radius, balls, cue_id):
        """Return the first live object ball intersected by the cue's aim ray."""
        closest = None

        for ball in balls:
            if ball.id == cue_id or ball.state.s == c.pocketed:
                continue

            ball_xy = np.asarray(ball.state.rvw[0, :2], dtype=float)
            relative = ball_xy - cue_xy
            along_ray = float(np.dot(relative, direction))
            if along_ray <= 0:
                continue

            perpendicular = relative - along_ray * direction
            radius_sum = cue_radius + ball.params.R
            discriminant = radius_sum**2 - float(np.dot(perpendicular, perpendicular))
            if discriminant < 0:
                continue

            collision_distance = along_ray - np.sqrt(discriminant)
            if collision_distance <= 0:
                continue

            if closest is None or collision_distance < closest[0]:
                closest = (
                    collision_distance,
                    ball,
                    cue_xy + direction * collision_distance,
                )

        if closest is None:
            return None, None
        return closest[1], closest[2]

    @staticmethod
    def _ray_to_table_edge(start, direction, table_w, table_l):
        """Return the forward intersection of a ray with the table rectangle."""
        distances = []
        x, y = start
        dx, dy = direction

        if dx > 1e-12:
            distances.append((table_w - x) / dx)
        elif dx < -1e-12:
            distances.append((0 - x) / dx)

        if dy > 1e-12:
            distances.append((table_l - y) / dy)
        elif dy < -1e-12:
            distances.append((0 - y) / dy)

        positive = [distance for distance in distances if distance > 0]
        distance = min(positive) if positive else 0.0
        return start + direction * distance

    @staticmethod
    def _draw_line(drawer, start, end, z, color):
        drawer.setColor(*color)
        drawer.moveTo(float(start[0]), float(start[1]), z)
        drawer.drawTo(float(end[0]), float(end[1]), z)

    @staticmethod
    def _draw_circle(drawer, center, radius, z):
        drawer.setColor(0.3, 1.0, 0.3, 0.95)
        angles = np.linspace(0, 2 * np.pi, 33)
        first = center + radius * np.array([np.cos(angles[0]), np.sin(angles[0])])
        drawer.moveTo(float(first[0]), float(first[1]), z)
        for angle in angles[1:]:
            point = center + radius * np.array([np.cos(angle), np.sin(angle)])
            drawer.drawTo(float(point[0]), float(point[1]), z)

    def _ensure_trajectory_guide(self):
        if self.trajectory_guide_node is None or self.trajectory_guide_node.isEmpty():
            parent = Global.render.find("scene").find("table")
            self.trajectory_guide_node = parent.attachNewNode("aim_trajectory_guide")
            self.trajectory_guide_node.setTransparency(TransparencyAttrib.MAlpha)
            self.trajectory_guide_node.setShaderAuto()
            self.trajectory_guide_node.setDepthOffset(10)
        return self.trajectory_guide_node

    def _replace_trajectory_geometry(self, drawer):
        old_node = self.trajectory_guide_node
        parent = old_node.getParent()
        old_node.removeNode()
        self.trajectory_guide_node = parent.attachNewNode(drawer.create())
        self.trajectory_guide_node.setName("aim_trajectory_guide")
        self.trajectory_guide_node.setTransparency(TransparencyAttrib.MAlpha)
        self.trajectory_guide_node.setShaderAuto()
        self.trajectory_guide_node.setDepthOffset(10)

    def _hide_trajectory_guide(self):
        if (
            self.trajectory_guide_node is not None
            and not self.trajectory_guide_node.isEmpty()
        ):
            self.trajectory_guide_node.hide()

    def _init_debug_info(self) -> None:
        if not settings.graphics.debug or self.debug_info is not None:
            return

        self.debug_info = autils.CustomOnscreenText(
            text="",
            font_name="LABTSECS",
            pos=(-1.58, 0.84),
            scale=0.038,
            fg=(1.0, 0.95, 0.35, 1.0),
            align=TextNode.ARight,
            parent=Global.aspect2d,
            mayChange=True,
        )
        self.table_spec_info = autils.CustomOnscreenText(
            text="",
            font_name="LABTSECS",
            pos=(-1.58, 0.48),
            scale=0.03,
            fg=(0.8, 0.95, 1.0, 1.0),
            align=TextNode.ARight,
            parent=Global.aspect2d,
            mayChange=False,
        )
        specs = prebuilt_specs(settings.gameplay.table_name)
        self.table_spec_info.setText(
            "TABLE SPECS\n"
            f"L {specs.l:.6f}\n"
            f"W {specs.w:.6f}\n"
            f"CUSH W {specs.cushion_width:.6f}\n"
            f"CUSH H {specs.cushion_height:.6f}\n"
            f"COR W {specs.corner_pocket_width:.6f}\n"
            f"COR ANG {specs.corner_pocket_angle:.6f}\n"
            f"COR DEP {specs.corner_pocket_depth:.6f}\n"
            f"COR RAD {specs.corner_pocket_radius:.6f}\n"
            f"COR JAW {specs.corner_jaw_radius:.6f}\n"
            f"SIDE W {specs.side_pocket_width:.6f}\n"
            f"SIDE ANG {specs.side_pocket_angle:.6f}\n"
            f"SIDE DEP {specs.side_pocket_depth:.6f}\n"
            f"SIDE RAD {specs.side_pocket_radius:.6f}\n"
            f"SIDE JAW {specs.side_jaw_radius:.6f}"
        )

    def set_debug_visualization(self, enabled: bool) -> None:
        """Update the aim overlay immediately when debug mode changes."""
        if enabled:
            self._init_debug_info()
        else:
            self._destroy_debug_info()

    def _update_debug_info(self, system, target, ghost_xy) -> None:
        if self.debug_info is None:
            return

        cue = system.cue
        cue_ball = system.balls[system.cue.cue_ball_id]
        cue_ball_speed = np.linalg.norm(cue_ball.state.rvw[1])
        cue_ball_spin = np.linalg.norm(cue_ball.state.rvw[2])
        target_id = "none" if target is None else target.id
        target_speed = (
            "-" if target is None else f"{np.linalg.norm(target.state.rvw[1]):.3f}"
        )
        camera = "2D" if self.camera_is_2d else "3D"
        self.debug_info.setText(
            "DEBUG\n"
            f"CAM {camera}\n"
            f"CUE {cue.V0:.2f}\n"
            f"BALL {cue_ball_speed:.3f}\n"
            f"SPIN {cue_ball_spin:.2f}\n"
            f"TGT {target_id}\n"
            f"T V {target_speed}"
        )

    def _destroy_debug_info(self) -> None:
        if self.debug_info is not None:
            self.debug_info.removeNode()
            self.debug_info = None
        if self.table_spec_info is not None:
            self.table_spec_info.removeNode()
            self.table_spec_info = None

    def queue_fine_aim(self, degrees: float) -> None:
        self.fine_aim_delta += degrees

    def rotate(self):
        if self.fine_aim_delta:
            delta = self.fine_aim_delta
            self.fine_aim_delta = 0.0
            self._consume_mouse_motion()
            if self.camera_is_2d:
                cue = multisystem.active.cue
                cue.set_state(phi=(cue.phi + delta) % 360)
                visual.cue.set_render_state_as_object_state()
            else:
                cam.rotate(phi=(cam.phi + delta) % 360)
                self.fix_cue_stick_to_camera()
            self.cue_avoidance()
            return

        if self.camera_is_2d:
            self.rotate_cue_only()
            self.cue_avoidance()
            return

        # Original 3D aiming behavior below is unchanged.
        keyboard_rotation = 2
        if self.keymap[Action.rotate_cue_left]:
            cam.rotate(phi=cam.phi + keyboard_rotation)
        elif self.keymap[Action.rotate_cue_right]:
            cam.rotate(phi=cam.phi - keyboard_rotation)
        else:
            cam.rotate_via_mouse()
        self.fix_cue_stick_to_camera()
        self.cue_avoidance()

    def rotate_cue_only(self) -> None:
        """Rotate the cue in 2D while leaving the camera completely unchanged."""
        cue = multisystem.active.cue
        keyboard_rotation = 2

        if self.keymap[Action.rotate_cue_left]:
            phi = cue.phi + keyboard_rotation
        elif self.keymap[Action.rotate_cue_right]:
            phi = cue.phi - keyboard_rotation
        else:
            with mouse:
                phi = cue.phi - rotate_sensitivity_x * mouse.get_dx()

        cue.set_state(phi=phi % 360)
        visual.cue.set_render_state_as_object_state()

    def cue_avoidance(self):
        _, _, theta, *_ = visual.cue.get_render_state()

        if (theta < cue_avoid.min_theta) or self.magnet_theta:
            theta = cue_avoid.min_theta
            system_cue = multisystem.active.cue
            system_cue.set_state(theta=theta)
            system_cue_ball = multisystem.active.balls[system_cue.cue_ball_id]
            visual.cue.set_render_state_as_object_state()
            hud.update_cue(system_cue, system_cue_ball)

        if not self.camera_is_2d and cam.theta < theta + min_camera:
            cam.rotate(theta=theta + min_camera)

    def fix_cue_stick_to_camera(self):
        phi = (cam.fixation.getH() + 180) % 360
        multisystem.active.cue.set_state(phi=phi)
        visual.cue.set_render_state_as_object_state()

    def aim_apply_power(self):
        with mouse:
            dy = mouse.get_dy()

        V0 = multisystem.active.cue.V0 + dy * power_sensitivity
        if V0 < min_stroke_speed:
            V0 = min_stroke_speed
        if V0 > max_stroke_speed:
            V0 = max_stroke_speed

        multisystem.active.cue.set_state(V0=V0)
        self._update_hud()

    def aim_elevate_cue(self):
        cue = visual.cue.get_node("cue_stick_focus")

        with mouse:
            delta_elevation = mouse.get_dy() * elevate_sensitivity

        old_elevation = -cue.getR()
        new_elevation = max(0, min(max_elevate, old_elevation + delta_elevation))

        if cue_avoid.min_theta >= new_elevation - self.magnet_threshold:
            # user set theta to minimum value, resume cushion tracking
            self.magnet_theta = True
            new_elevation = cue_avoid.min_theta
        else:
            # theta has been modified by the user, so no longer tracks the cushion
            self.magnet_theta = False

        cue.setR(-new_elevation)

        if not self.camera_is_2d and cam.theta < (new_elevation + min_camera):
            cam.rotate(theta=new_elevation + min_camera)

        multisystem.active.cue.set_state(theta=new_elevation)
        self._update_hud()

    def apply_english(self):
        with mouse:
            dx, dy = mouse.get_dx(), mouse.get_dy()

        cue = visual.cue.get_node("cue_stick")
        cue_focus = visual.cue.get_node("cue_stick_focus")

        R = visual.cue.follow._ball.params.R

        delta_y, delta_z = dx * english_sensitivity, dy * english_sensitivity

        # y corresponds to side spin, z to top/bottom spin
        new_y = cue.getY() + delta_y
        new_z = cue.getZ() + delta_z

        cue_axis_offset = (
            np.array([-new_y, new_z]) / R
        )  # components normalized to ball radius
        contact_point_offset = tip_contact_offset(
            cue_axis_offset, multisystem.active.cue.specs.tip_radius, R
        )

        norm = norm2d(contact_point_offset)
        if norm > max_english:
            limit_scaling_factor = max_english / norm
            new_y *= limit_scaling_factor
            new_z *= limit_scaling_factor
            cue_axis_offset *= limit_scaling_factor
            contact_point_offset *= limit_scaling_factor

        cue.setY(new_y)
        cue.setZ(new_z)

        # if application of english increases min_theta beyond current elevation,
        # increase elevation
        if (
            self.magnet_theta
            or cue_avoid.min_theta >= -cue_focus.getR() - self.magnet_threshold
        ):
            cue_focus.setR(-cue_avoid.min_theta)

        if not self.camera_is_2d and cam.theta < (
            new_theta := -cue_focus.getR() + min_camera
        ):
            cam.rotate(theta=new_theta)

        multisystem.active.cue.set_state(
            a=contact_point_offset[0],
            b=contact_point_offset[1],
            theta=-cue_focus.getR(),
        )

        self._update_hud()

    def _update_hud(self) -> None:
        """Update HUD with current system's cue and cue ball"""
        system_cue = multisystem.active.cue
        hud.update_cue(system_cue, multisystem.active.balls[system_cue.cue_ball_id])
