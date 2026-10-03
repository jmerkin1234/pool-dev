#! /usr/bin/env python


import pooltool.ani.tasks as tasks
from pooltool.ani.action import Action
from pooltool.ani.camera import cam
from pooltool.ani.collision import cue_avoid
from pooltool.ani.constants import rotate_downtime
from pooltool.ani.globals import Global
from pooltool.ani.hud import hud
from pooltool.ani.modes.datatypes import BaseMode, Mode
from pooltool.ani.mouse import MouseMode, mouse
from pooltool.ani.scene import PlaybackMode, visual
from pooltool.system.datatypes import multisystem


class ShotMode(BaseMode):
    name = Mode.shot
    keymap = {
        Action.move: False,
        Action.restart_ani: False,
        Action.quit: False,
        Action.zoom: False,
        Action.rewind: False,
        Action.fast_forward: False,
        Action.fine_control: False,
        Action.cam_save: False,
        Action.cam_load: False,
        Action.show_help: False,
        Action.close_scene: False,
        Action.introspect: False,
        Action.next_shot: False,
        Action.prev_shot: False,
        Action.parallel: False,
    }

    # The analysis viewer has playback controls; the game plays each shot once.
    view_only = False

    def enter(self, build_animations=False, playback_mode: PlaybackMode | None = None):
        """Enter method for Shot

        Parameters
        ==========
        build_animations : bool, False
            If True, the shot animations are built with visual.build_shot_animation.
        """
        mouse.mode(MouseMode.RELATIVE)

        if build_animations:
            visual.build_shot_animation(animate_stroke=self.view_only)
            visual.animate(PlaybackMode.SINGLE)
            if self.view_only:
                visual.advance_to_end_of_stroke()

        if playback_mode is not None:
            visual.animate(playback_mode)

        self._update_hud()

        if self.view_only:
            self.register_keymap_event("escape", Action.close_scene, True)
            self.register_keymap_event("escape-up", Action.close_scene, False)
        else:
            self.register_keymap_event("escape", Action.quit, True)

        self.register_keymap_event("mouse1", Action.zoom, True)
        self.register_keymap_event("mouse1-up", Action.zoom, False)
        self.register_keymap_event("v", Action.move, True)
        self.register_keymap_event("v-up", Action.move, False)
        self.register_keymap_event("1", Action.cam_save, True)
        self.register_keymap_event("2", Action.cam_load, True)
        self.register_keymap_event("h", Action.show_help, True)
        self.register_keymap_event("i", Action.introspect, True)
        self.register_keymap_event("i-up", Action.introspect, False)
        # Playback tools belong only to the separate analysis viewer.
        if self.view_only:
            tasks.register_event("space", visual.toggle_pause)
            tasks.register_event("arrow_up", visual.speed_up)
            tasks.register_event("arrow_down", visual.slow_down)
            self.register_keymap_event("r", Action.restart_ani, True)
            self.register_keymap_event("r-up", Action.restart_ani, False)
            self.register_keymap_event("arrow_left", Action.rewind, True)
            self.register_keymap_event("arrow_left-up", Action.rewind, False)
            self.register_keymap_event("arrow_right", Action.fast_forward, True)
            self.register_keymap_event("arrow_right-up", Action.fast_forward, False)
            self.register_keymap_event("f", Action.fine_control, True)
            self.register_keymap_event("f-up", Action.fine_control, False)
            self.register_keymap_event("n-up", Action.next_shot, True)
            self.register_keymap_event("p-up", Action.prev_shot, True)
            self.register_keymap_event("enter-up", Action.parallel, True)
            tasks.add(self.shot_animation_task, "shot_animation_task")

        tasks.add(self.shot_view_task, "shot_view_task")
        tasks.add(self.shared_task, "shared_task")

    def exit(self, key="soft"):
        """Exit shot mode.

        Args:
            key:
                'advance' applies the shot result and prepares the next turn.
                'soft' preserves the animation for settings or camera changes.
        """
        assert key in {"advance", "soft"}

        # Always ensure parallel mode is exited, regardless of view_only status
        # This ensures clean exit even if view_only state changed
        if visual.is_parallel_mode:
            visual.exit_parallel_mode()

        if key == "advance":
            # New shot means new system. Depending how ShotMode was entered, it may
            # already exist in the multisystem. If the most recently added system is
            # unsimulated, it is considered the new system. Otherwise, it needs to be
            # created, using most recent system as template.
            new_system_exists = multisystem[-1].simulated

            if new_system_exists:
                # The shot is processed and advanced now, because (1) the shot animation
                # has ended or the user has requested to take the next shot and (2) it
                # hasn't been processed yet, since if it had, the latest system would be
                # unsimulated.
                Global.game.process_and_advance(multisystem[-1])

                if multisystem.active_index != multisystem.max_index:
                    # The currently rendered shot isn't the most recent shot, and it's
                    # the most recent shot we want to advance from. So we render it as
                    # an intermediate step.
                    visual.switch_rendered_system(multisystem_idx=-1)

                    # self.quats is a vestige held in BallRender. We need to calculate
                    # it so we know the final orientation of each ball.
                    for ball_render in visual.balls.values():
                        ball_render.set_quats(ball_render._ball.history_cts)

                new_system = multisystem.active.copy()

                for ball, old_render in zip(
                    new_system.balls.values(), visual.balls.values()
                ):
                    # Set the initial ball orientations of the new shot to match the
                    # final ball orientations of the old shot
                    ball.initial_orientation = old_render.get_final_orientation()

                new_system.reset_history()
                multisystem.append(new_system)

            # Switch shots
            visual.switch_rendered_system(multisystem_idx=-1)
            self._update_hud()

            cue_avoid.init_collisions()
            multisystem.active.cue.reset_state()
            visual.cue.set_render_state_as_object_state()

        tasks.remove("shot_view_task")
        tasks.remove("shot_animation_task")
        tasks.remove("shared_task")

    def shot_view_task(self, task):
        if self.keymap[Action.close_scene]:
            cam.store_state("last_scene", overwrite=True)
            Global.base.messenger.send("close-scene")
            Global.mode_mgr.end_mode()
            Global.base.messenger.send("stop")

        elif not self.view_only and visual.animation_finished:
            # Finish the turn before choosing the next mode. Changing modes from
            # exit() would recursively exit ShotMode and lose the game-over screen.
            Global.mode_mgr.end_mode(key="advance")
            next_mode = Mode.game_over if Global.game.shot_info.game_over else Mode.aim
            Global.mode_mgr.start_mode(next_mode)
            return task.done

        elif self.keymap[Action.zoom]:
            cam.zoom_via_mouse()

        elif self.keymap[Action.move]:
            cam.move_fixation_via_mouse()

        elif task.time > rotate_downtime:
            # Only rotate the camera if some time has passed since the mode was entered,
            # otherwise the shot followthrough jarringly rotates the camera
            cam.rotate_via_mouse()

        else:
            # We didn't do anything this frame, but touch the mouse so any future mouse
            # movements don't experience a big jump
            mouse.touch()

        return task.cont

    def shot_animation_task(self, task):
        if not self.view_only:
            return task.done
        if self.keymap[Action.restart_ani]:
            visual.animate(PlaybackMode.LOOP)
            visual.restart_animation()

        elif self.keymap[Action.rewind]:
            dt = 0.008 if self.keymap[Action.fine_control] else 0.03
            if visual.paused:
                visual.offset_time(-dt)

        elif self.keymap[Action.fast_forward]:
            dt = 0.008 if self.keymap[Action.fine_control] else 0.03
            if visual.paused:
                visual.offset_time(dt)

        elif self.keymap[Action.parallel] and self.view_only:
            self.keymap[Action.parallel] = False
            if visual.is_parallel_mode:
                visual.exit_parallel_mode()
            else:
                visual.setup_parallel_mode()

        elif self.keymap[Action.prev_shot]:
            self.keymap[Action.prev_shot] = False
            shot_index = self._find_previous_valid_shot()
            visual.switch_to_shot(shot_index)
            self._update_hud()
            cue_avoid.init_collisions()

        elif self.keymap[Action.next_shot]:
            self.keymap[Action.next_shot] = False
            shot_index = self._find_next_valid_shot()
            visual.switch_to_shot(shot_index)
            self._update_hud()
            cue_avoid.init_collisions()

        return task.cont

    def _update_hud(self) -> None:
        """Update HUD with current system's cue and cue ball"""
        system_cue = multisystem.active.cue
        hud.update_cue(system_cue, multisystem.active.balls[system_cue.cue_ball_id])

    def _find_previous_valid_shot(self) -> int:
        """Find the previous valid shot, wrapping around if needed."""
        shot_index = multisystem.active_index - 1

        # Search backwards through shots, wrapping to end if needed
        for _ in range(len(multisystem)):
            if shot_index < 0:
                shot_index = len(multisystem) - 1

            if self._is_valid_shot(shot_index):
                return shot_index

            shot_index -= 1

        # Fallback to current shot if no valid shot found
        return multisystem.active_index

    def _find_next_valid_shot(self) -> int:
        """Find the next valid shot, wrapping around if needed."""
        shot_index = multisystem.active_index + 1

        # Search forwards through shots, wrapping to start if needed
        for _ in range(len(multisystem)):
            if shot_index >= len(multisystem):
                shot_index = 0

            if self._is_valid_shot(shot_index):
                return shot_index

            shot_index += 1

        # Fallback to current shot if no valid shot found
        return multisystem.active_index

    def _is_valid_shot(self, shot_index: int) -> bool:
        """Check if a shot index represents a valid shot to navigate to."""
        return multisystem[shot_index].simulated or shot_index != len(multisystem) - 1
