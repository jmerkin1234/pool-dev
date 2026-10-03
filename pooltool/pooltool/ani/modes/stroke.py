#! /usr/bin/env python

import pooltool.ani.tasks as tasks
import pooltool.ani.utils as autils
from pooltool.ani.action import Action
from pooltool.ani.camera import cam
from pooltool.ani.constants import (
    backstroke_fraction,
    max_stroke_speed,
    min_stroke_speed,
    stroke_sensitivity,
)
from pooltool.ani.globals import Global
from pooltool.ani.hud import hud
from pooltool.ani.modes.datatypes import BaseMode, Mode
from pooltool.ani.mouse import MouseMode, mouse
from pooltool.ani.scene import visual
from pooltool.system.datatypes import multisystem


class StrokeMode(BaseMode):
    name = Mode.stroke
    keymap = {
        Action.stroke: False,
        Action.exec_shot: False,
        Action.aim: False,
        Action.quit: False,
        Action.show_help: False,
    }

    def __init__(self):
        super().__init__()
        self.call_shot_message = None
        self.firing = False

    def enter(self):
        mouse.mode(MouseMode.RELATIVE)
        if Global.mode_mgr.last_mode in (Mode.aim, Mode.view):
            Global.mode_mgr.mode_stroked_from = Global.mode_mgr.last_mode
            self.keymap[Action.stroke] = True
            self.firing = False
            visual.cue.track_stroke()
        visual.cue.show_nodes(ignore=("cue_cseg",))

        self.register_keymap_event("s", Action.stroke, True)
        self.register_keymap_event("s-up", Action.stroke, False)
        self.register_keymap_event("space", Action.exec_shot, True)
        self.register_keymap_event("space-up", Action.exec_shot, False)
        self.register_keymap_event("a", Action.aim, True)
        self.register_keymap_event("escape", Action.quit, True)
        self.register_keymap_event("h", Action.show_help, True)
        tasks.add(self.stroke_task, "stroke_task")
        tasks.add(self.shared_task, "shared_task")

    def exit(self):
        tasks.remove("stroke_task")
        tasks.remove("shared_task")
        if self.call_shot_message is not None:
            self.call_shot_message.hide()
            self.call_shot_message = None
        cam.store_state(Mode.stroke, overwrite=True)

    def stroke_task(self, task):
        cue_node = visual.cue.get_node("cue_stick")
        if self.firing:
            # Space commits the shot. Advance the cue to contact only once.
            mouse.touch()
            cue_node.setX(
                max(
                    0,
                    cue_node.getX()
                    - multisystem.active.cue.V0 * min(Global.clock.getDt(), 0.05),
                )
            )
            if cue_node.getX() <= 0:
                self.firing = False
                visual.cue.set_object_state_as_render_state(skip_V0=True)
                multisystem.active.strike()
                Global.mode_mgr.change_mode(Mode.calculate)
                return task.done
        elif self.keymap[Action.aim]:
            cue_node.setX(0)
            Global.mode_mgr.change_mode(Global.mode_mgr.mode_stroked_from)
            return task.done
        elif self.keymap[Action.exec_shot]:
            self.keymap[Action.exec_shot] = False
            mouse.touch()
            if not Global.game.shot_constraints.can_shoot():
                if self.call_shot_message is None:
                    self.call_shot_message = autils.TextOverlay(
                        title="Press A to aim, then hold C to call your shot.",
                        frame_color=(0, 0, 0, 0.3),
                        title_pos=(0, 0, 0.6),
                        text_fg=(1, 1, 1, 0.8),
                        text_scale=0.05,
                    )
                    self.call_shot_message.show()
            elif cue_node.getX() > 0:
                self.firing = True
        elif self.keymap[Action.stroke]:
            self.stroke_cue_stick()
        else:
            # Releasing S locks both the draw distance and shot power.
            mouse.touch()
        return task.cont

    def stroke_cue_stick(self):
        max_backstroke = multisystem.active.cue.specs.length * backstroke_fraction
        with mouse:
            dy = mouse.get_dy()
        cue_node = visual.cue.get_node("cue_stick")
        draw = max(0, min(max_backstroke, cue_node.getX() - dy * stroke_sensitivity))
        cue_node.setX(draw)
        cue = multisystem.active.cue
        cue.set_state(
            V0=max(min_stroke_speed, max_stroke_speed * draw / max_backstroke)
        )
        hud.update_cue(cue, multisystem.active.balls[cue.cue_ball_id])
