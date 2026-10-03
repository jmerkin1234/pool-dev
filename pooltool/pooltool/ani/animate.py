#! /usr/bin/env python

import ctypes
import ctypes.util
import gc
import os
import sys
from collections.abc import Generator
from math import atan2, degrees

import simplepbr
from attrs import define
from direct.showbase.ShowBase import ShowBase
from panda3d.core import (
    ClockObject,
    FrameBufferProperties,
    GraphicsWindow,
    TextNode,
    WindowProperties,
)

import pooltool.ani.tasks as tasks
import pooltool.ani.utils as autils
from pooltool.ani.camera import CameraState, cam, camera_states
from pooltool.ani.collision import cue_avoid
from pooltool.ani.constants import menu_text_scale
from pooltool.ani.globals import Global, require_showbase
from pooltool.ani.hud import HUDElement, hud
from pooltool.ani.menu import MenuRegistry
from pooltool.ani.modes import Mode, ModeManager, all_modes
from pooltool.ani.mouse import mouse
from pooltool.ani.scene import PlaybackMode, visual
from pooltool.config import settings
from pooltool.evolution import simulate
from pooltool.evolution.continuous import continuize
from pooltool.layouts import get_rack
from pooltool.objects.cue.datatypes import Cue
from pooltool.objects.table.collection import prebuilt_specs
from pooltool.objects.table.datatypes import Table
from pooltool.ruleset import get_ruleset
from pooltool.ruleset.datatypes import Player, Ruleset
from pooltool.system.datatypes import MultiSystem, System, multisystem
from pooltool.utils import Run, get_total_memory_usage, human_readable_file_size

run = Run()


@define
class ShowBaseConfig:
    window_type: str | None = None
    window_size: tuple[int, int] | None = None
    fb_prop: FrameBufferProperties | None = None
    monitor: bool = False

    @classmethod
    def default(cls):
        return cls(
            window_type="onscreen",
            window_size=None,
            fb_prop=None,
            monitor=False,
        )


@require_showbase
def boop(frames=1):
    """Advance/render a number of frames"""
    for _ in range(frames):
        Global.base.graphicsEngine.renderFrame()


@require_showbase
def window_task(win=None):
    """Routine for managing window activity/resizing

    Determines whether window is active or not. If not, purgatory mode is entered, a
    reduced FPS state.

    Window resizing is left to the desktop window manager. Panda3D automatically
    updates the lens and 2-D aspect nodes when the window size changes, so forcing a
    second size here is unnecessary and can request a display mode that does not exist.
    """
    no_purgatory_modes = {Mode.purgatory, Mode.menu}

    is_window_active = Global.base.win.get_properties().foreground
    if not is_window_active and Global.mode_mgr.mode not in no_purgatory_modes:
        Global.mode_mgr.change_mode(Mode.purgatory)

    return


def _maximize_x11_window(window: GraphicsWindow) -> bool:
    """Ask an X11/EWMH window manager to maximize a Panda3D window."""
    if os.name != "posix" or not os.environ.get("DISPLAY"):
        return False

    x11_name = ctypes.util.find_library("X11")
    if not x11_name:
        return False

    try:
        window_id = window.getWindowHandle().getIntHandle()
        if not window_id:
            return False

        x11 = ctypes.cdll.LoadLibrary(x11_name)

        class XClientMessageData(ctypes.Union):
            _fields_ = [
                ("b", ctypes.c_char * 20),
                ("s", ctypes.c_short * 10),
                ("l", ctypes.c_long * 5),
            ]

        class XClientMessageEvent(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int),
                ("data", XClientMessageData),
            ]

        class XEvent(ctypes.Union):
            _fields_ = [
                ("xclient", XClientMessageEvent),
                ("pad", ctypes.c_long * 24),
            ]

        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.POINTER(XEvent),
        ]
        x11.XSendEvent.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        display = x11.XOpenDisplay(None)
        if not display:
            return False

        try:
            root = x11.XDefaultRootWindow(display)
            wm_state = x11.XInternAtom(display, b"_NET_WM_STATE", False)
            max_horz = x11.XInternAtom(display, b"_NET_WM_STATE_MAXIMIZED_HORZ", False)
            max_vert = x11.XInternAtom(display, b"_NET_WM_STATE_MAXIMIZED_VERT", False)

            event = XEvent()
            event.xclient.type = 33  # ClientMessage
            event.xclient.serial = 0
            event.xclient.send_event = True
            event.xclient.display = display
            event.xclient.window = window_id
            event.xclient.message_type = wm_state
            event.xclient.format = 32
            event.xclient.data.l[0] = 1  # _NET_WM_STATE_ADD
            event.xclient.data.l[1] = max_horz
            event.xclient.data.l[2] = max_vert
            event.xclient.data.l[3] = 1  # normal application
            event.xclient.data.l[4] = 0

            mask = (1 << 20) | (1 << 19)  # SubstructureRedirect/NotifyMask
            sent = x11.XSendEvent(display, root, False, mask, ctypes.byref(event))
            x11.XFlush(display)
            return bool(sent)
        finally:
            x11.XCloseDisplay(display)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _resize_offscreen_window(size: tuple[int, int]):
    """Changes window size when provided the dimensions (x, y) in pixels"""
    Global.base.win.setSize(*[int(dim) for dim in size])


def _init_simplepbr() -> simplepbr.Pipeline:
    return simplepbr.init(
        enable_shadows=settings.graphics.shadows,
        max_lights=settings.graphics.max_lights,
    )


class Interface(ShowBase):
    def __init__(self, config: ShowBaseConfig):
        self.showbase_config = config
        super().__init__(self, windowType=self.showbase_config.window_type)

        self.openMainWindow(
            fbprops=self.showbase_config.fb_prop, size=self.showbase_config.window_size
        )

        if isinstance(self.win, GraphicsWindow):
            properties = WindowProperties()
            properties.setFullscreen(False)
            properties.setUndecorated(False)
            self.win.requestProperties(properties)
            tasks.add_later(0.2, self._maximize_window_task, "maximize_window")

        # Background doesn't apply if ran after simplepbr.init(). See
        # https://discourse.panda3d.org/t/cant-change-base-background-after-simplepbr-init/28945
        Global.base.setBackgroundColor(0.04, 0.04, 0.04)

        self.simplepbr_pipeline = _init_simplepbr()

        if isinstance(self.win, GraphicsWindow):
            mouse.init()

        cam.init()

        if not settings.graphics.shader:
            Global.render.set_shader_off()

        Global.clock.setMode(ClockObject.MLimited)
        Global.clock.setFrameRate(settings.graphics.fps)

        Global.register_mode_mgr(ModeManager(all_modes))
        assert Global.mode_mgr is not None
        Global.mode_mgr.init_modes()

        self.frame = 0
        tasks.add(self.increment_frame, "increment_frame")

        if self.showbase_config.monitor:
            tasks.add(self.monitor, "monitor")

        self._listen_constant_events()

    def _maximize_window_task(self, task):
        if isinstance(self.win, GraphicsWindow):
            _maximize_x11_window(self.win)
        return task.done

    def _listen_constant_events(self):
        """Listen for events that are mode independent"""
        tasks.register_event("window-event", window_task)
        tasks.register_event("close-scene", self.close_scene)
        tasks.register_event("toggle-help", hud.toggle_help)

    def close_scene(self):
        # Ensure parallel mode is exited
        if visual.is_parallel_mode:
            visual.exit_parallel_mode()

        cam.set_2d_locked(False)
        scene_node = Global.render.find("scene")
        Global.resume_available = False

        if not scene_node.isEmpty():
            visual.teardown()

        if not scene_node.isEmpty():
            scene_node.removeNode()

        hud.destroy()

        cam.fixation = None
        cam.fixation_object = None
        cam.fixated = False

        if hasattr(self, "scene_active"):
            self.scene_active = False

        gc.collect()

    def create_scene(self):
        """Create a scene from multisystem"""
        Global.render.attachNewNode("scene")

        visual.attach_system(multisystem.active)
        visual.buildup()

        R = max([ball.params.R for ball in multisystem.active.balls.values()])
        cam.fixate(
            pos=(multisystem.active.table.w / 2, multisystem.active.table.l / 2, R),
            node=visual.table.get_node("table"),
        )

    def monitor(self, task):
        if Global.mode_mgr.mode == Mode.purgatory or Global.mode_mgr.mode is None:
            return task.cont

        keymap = Global.mode_mgr.get_keymap()

        debug_data = {
            "Mode": Global.mode_mgr.mode,
            "Last": Global.mode_mgr.last_mode,
            "Tasks": [task.name for task in Global.task_mgr.getAllTasks()],
            "Memory": human_readable_file_size(get_total_memory_usage()),
            "Actions": [k for k in keymap if keymap[k]],
        }

        run.table(debug_data, title=f"Frame {self.frame}")

        return task.cont

    def increment_frame(self, task):
        self.frame += 1
        return task.cont

    def finalizeExit(self):
        """Override ShowBase.finalizeExit to potentially prevent sys.exit call

        See:
        https://docs.panda3d.org/1.10/python/reference/direct.showbase.ShowBase#direct.showbase.ShowBase.ShowBase.finalizeExit
        """
        self.stop()

    def stop(self):
        """Called when window exited. Subclasses can avoid by overwriting this method"""
        sys.exit()


FBF_FBP = FrameBufferProperties()
FBF_FBP.setRgbColor(True)
FBF_FBP.setRgbaBits(8, 8, 8, 0)
FBF_FBP.setDepthBits(24)

DEFAULT_FBF_CONFIG = ShowBaseConfig(
    window_type="offscreen",
    monitor=False,
    fb_prop=FBF_FBP,
)


class FrameStepper(Interface):
    """Step through a shot frame-by-frame"""

    def __init__(self, config: ShowBaseConfig = DEFAULT_FBF_CONFIG):
        Interface.__init__(self, config=config)

        # Aim to render 10000 FPS so the clock doesn't sleep between frames
        Global.clock.setMode(ClockObject.MLimited)
        Global.clock.setFrameRate(10000)

    def _iterator(
        self,
        system: System,
        size: tuple[int, int] = (int(1.6 * 720), 720),
        fps: float = 30.0,
    ) -> Generator:
        continuize(system, dt=1 / fps, inplace=True)

        multisystem.reset()
        multisystem.append(system)

        _resize_offscreen_window(size)

        self.create_scene()

        # We don't want the cue in this
        visual.cue.hide_nodes()

        # Or the camera fixation point object
        if cam.fixation_object is not None:
            cam.fixation_object.removeNode()

        # Set quaternions for each ball
        for ball in visual.balls.values():
            ball.set_quats(ball._ball.history_cts)

        frames = int(system.events[-1].time * fps) + 1

        yield frames

        for frame in range(frames):
            for ball in visual.balls.values():
                ball.set_render_state_from_history(ball._ball.history_cts, frame)
                ball._ball.state = ball._ball.history_cts[frame]

            Global.task_mgr.step()

            yield frame

    def iterator(self, *args, **kwargs) -> tuple[Generator, int]:
        """Iterate through each frame

        Args:
            shot:
                The shot you would like to iterate through. It should already by
                simulated. It is OK if you have continuized the shot (you can check with
                shot.continuized), but the continuization will be overwritten to match
                the `fps` chosen in this method.
            size:
                The number of pixels in x and y. If x:y != 1.6, the aspect ratio will
                look distorted.
            fps:
                This is the rate (in frames per second) that the shot is iterated
                through.

        Returns:
            iterator:
                This is an iterator. `next(iterator)` will advance the rendered objects
                to the next frame.
            frames:
                This is the length of the iterator (by the time you receive it). Useful
                for things like `for frame in frames: next(iterator)`.
        """
        iterator = self._iterator(*args, **kwargs)
        frames = next(iterator)
        return iterator, frames


class ShotViewer(Interface):
    """An interface for viewing shots from within python.

    Important:
        For instructions on how to use the interactive interface, see :doc:`The
        Interface </getting_started/interface>`.

    Important:
        Only one instance of this class can be created per python process. You'll
        receive a runtime error if you try. Instead, create one instance and use it for
        the lifetime of your python process.

    For usage, see :meth:`show`.
    """

    def __init__(self, config=ShowBaseConfig.default()):
        Interface.__init__(self, config=config)
        self._create_title("")

        # Set ShotMode to view only. This prevents giving cue stick control to the user
        # and dictates that esc key closes scene rather than going to a menu
        Global.mode_mgr.modes[Mode.shot].view_only = True

        self._stop()

    def show(
        self,
        shot_or_shots: System | MultiSystem,
        title: str = "Press <esc> to continue program execution",
        camera_state: CameraState | None = None,
    ):
        """Opens the interactive interface for one or more shots.

        Important:
            For instructions on how to use the interactive interface, see :doc:`The
            Interface </getting_started/interface>`.

        Args:
            shot_or_shots:
                The shot or collection of shots to visualize. This can be a single
                :class:`pooltool.system.System` object or a
                :class:`pooltool.system.MultiSystem` object containing
                multiple systems.

                Note:
                    If a multisystem is passed, the systems can be scrolled through by
                    pressing *n* (next) and *p* (previous). When using ``pt.show()``,
                    press *Enter* to toggle parallel visualization mode where all systems
                    play simultaneously with reduced opacity except the active one. In
                    parallel mode, use *n* and *p* to change which system has full opacity.
                    Note that parallel visualization is only available in ``pt.show()``
                    and not when playing the game through ``run-pooltool``.
            title:
                The title to display in the visualization. Defaults to an empty string.
            camera_state:
                The initial camera state that the visualization is rendered with.

        Example:

            This example visualizes a single shot.

            >>> import pooltool as pt
            >>> system = pt.System.example()

            Make sure the shot is simulated, otherwise it will make for a boring
            visualization:

            >>> pt.simulate(system, inplace=True)

            Create a :class:`ShotViewer` object:

            >>> gui = pt.ShotViewer()

            Now visualize the shot:

            >>> gui.show(system)

            (Press *escape* to exit the interface and continue script execution)
        """
        self._start()

        multisystem.reset()
        if isinstance(shot_or_shots, System):
            multisystem.append(shot_or_shots)
        else:
            for shot in shot_or_shots:
                multisystem.append(shot)

        self.create_scene()

        if camera_state is None:
            cam.load_saved_state("last_scene", ok_if_not_exists=True)
        else:
            cam.load_state(camera_state)

        self._create_title(title)
        self.title_node.show()

        if settings.graphics.hud:
            hud.init(hide=[HUDElement.help_text])

        params = dict(
            build_animations=True,
            playback_mode=PlaybackMode.LOOP,
        )
        Global.mode_mgr.update_event_baseline()
        Global.mode_mgr.change_mode(Mode.shot, enter_kwargs=params)
        Global.task_mgr.run()

    def _listen_constant_events(self):
        """Listen for events that are mode independent"""
        Interface._listen_constant_events(self)
        tasks.register_event("stop", self._stop)

    def _create_title(self, title):
        self.title_node = autils.CustomOnscreenText(
            text=title,
            pos=(-1.55, -0.93),
            scale=menu_text_scale * 0.7,
            fg=(1, 1, 1, 1),
            align=TextNode.ALeft,
            parent=Global.aspect2d,
        )
        self.title_node.hide()

    def _start(self):
        self.openMainWindow(keepCamera=True)
        self.simplepbr_pipeline = _init_simplepbr()
        mouse.init()

    def _stop(self):
        # Clean up simplepbr resources to prevent memory leak.
        # Each _start() creates a new pipeline; without cleanup, FilterManager
        # buffers and update tasks accumulate in the Panda3D engine.
        # https://github.com/ekiefl/pooltool/issues/219
        self.simplepbr_pipeline._filtermgr.cleanup()
        Global.task_mgr.remove("simplepbr update")
        self.title_node.destroy()
        self.closeWindow(self.win)
        Global.task_mgr.stop()


class Game(Interface):
    """This class runs the pooltool application"""

    def __init__(self, config=ShowBaseConfig.default()):
        Interface.__init__(self, config=config)

        # This task chain allows simulations to be run in parallel to the game processes
        Global.task_mgr.setupTaskChain("simulation", numThreads=1)

        tasks.register_event("enter-game", self._enter_game)
        tasks.register_event("new-game", self._start_new_game)
        tasks.register_event("resume-game", self._resume_game)
        tasks.register_event("pause-game", self._pause_game)
        tasks.register_event("practice-game", self._start_new_game, [True])
        tasks.register_event("f3", self.toggle_debug)

        Global.mode_mgr.update_event_baseline()
        Global.mode_mgr.change_mode(Mode.menu)

        self.scene_active = False
        self._suspended_mode = None
        self._suspended_last_mode = None
        self._resume_animation = False

    def _enter_game(self):
        """Close the menu, setup the visualization, and start the game"""
        Global.resume_available = False
        if not Global.game or not len(multisystem):
            self._create_system()
            self._aim_cue_at_rack()

        MenuRegistry.hide_all()
        self.create_scene()
        self.scene_active = True

        # Prepare the original 3D AimMode camera on the cue ball, save it
        # separately, then start from the exact fixed 2D overhead state.
        cueing_ball_id = multisystem.active.cue.cue_ball_id
        cam.move_fixation(visual.balls[cueing_ball_id].get_node("pos").getPos())
        cam.store_state("aim_3d_camera", overwrite=True)
        cam.load_state(camera_states["7_foot_overhead"])
        Global.mode_mgr.modes[Mode.aim].camera_is_2d = True
        visual.cue.hide_nodes()
        cue_avoid.init_collisions()

        if settings.graphics.hud:
            hud.init()

        code_comp_menu = autils.TextOverlay(
            title="Preparing table...",
            frame_color=(0, 0, 0, 0.4),
            title_pos=(0, 0, 0),
        )
        code_comp_menu.show()
        boop(2)
        simulate(System.example(), inplace=True)
        code_comp_menu.hide()

        Global.mode_mgr.change_mode(Mode.aim)

    def _pause_game(self):
        """Open settings while retaining the live table, cue and shot progress."""
        if not self.scene_active or Global.mode_mgr.mode == Mode.menu:
            return
        if Global.mode_mgr.mode == Mode.game_over:
            Global.mode_mgr.end_mode()
            self.close_scene()
            Global.mode_mgr.start_mode(Mode.menu)
            return
        self._suspended_mode = Global.mode_mgr.mode
        self._suspended_last_mode = Global.mode_mgr.last_mode
        self._resume_animation = (
            self._suspended_mode == Mode.shot
            and not visual.paused
            and not visual.animation_finished
        )
        if self._resume_animation:
            visual.pause_animation()
        Global.resume_available = True
        hud.hide()
        Global.mode_mgr.change_mode(Mode.menu, enter_kwargs=dict(menu_name="settings"))
        Global.render.find("scene").hide()

    def _resume_game(self):
        """Restore the suspended mode without rebuilding or replaying the shot."""
        if not Global.resume_available or self._suspended_mode is None:
            return
        mode = self._suspended_mode
        Global.mode_mgr.end_mode()
        Global.resume_available = False
        self._suspended_mode = None
        Global.render.find("scene").show()
        hud.show()
        kwargs = dict(resuming=True) if mode == Mode.calculate else {}
        Global.mode_mgr.start_mode(mode, **kwargs)
        Global.mode_mgr.last_mode = self._suspended_last_mode
        if self._resume_animation:
            visual.resume_animation()
        self._resume_animation = False

    def _start_new_game(self, practice=False):
        """Discard the saved table state and start a fresh rack."""
        Global.mode_mgr.end_mode()
        tasks.remove("run_simulation")
        self.close_scene()
        self._suspended_mode = None
        Global.resume_available = False
        multisystem.reset()
        Global.game = None
        self._create_system(enforce_rules=not practice)
        self._aim_cue_at_rack()
        self._enter_game()

    def toggle_debug(self):
        """Toggle debug visualizations without leaving the current game."""
        if Global.mode_mgr.mode == Mode.menu:
            return

        enabled = not settings.graphics.debug
        with settings.write() as current:
            current.graphics.debug = enabled

        scene_node = Global.render.find("scene")
        if scene_node.isEmpty():
            return

        visual.table.set_debug_visualization(enabled)
        aim_mode = Global.mode_mgr.modes[Mode.aim]
        aim_mode.set_debug_visualization(enabled)

    def _aim_cue_at_rack(self) -> None:
        """Aim the opening cue at the closest object ball in the rack."""
        system = multisystem.active
        cue_ball = system.balls[system.cue.cue_ball_id]
        cue_x, cue_y = cue_ball.state.rvw[0][:2]

        object_balls = [
            ball
            for ball_id, ball in system.balls.items()
            if ball_id != system.cue.cue_ball_id
        ]
        if not object_balls:
            return

        head_ball = min(
            object_balls,
            key=lambda ball: (
                (ball.state.rvw[0][0] - cue_x) ** 2
                + (ball.state.rvw[0][1] - cue_y) ** 2
            ),
        )
        target_x, target_y = head_ball.state.rvw[0][:2]
        phi = degrees(atan2(target_y - cue_y, target_x - cue_x)) % 360
        system.cue.set_state(phi=phi)

    def _create_system(self, enforce_rules=None):
        """Create the multisystem and game objects"""
        game_type = settings.gameplay.game_type
        if enforce_rules is None:
            enforce_rules = settings.gameplay.enforce_rules
        game = get_ruleset(game_type, enforce_rules=enforce_rules)()
        game.players = (
            [Player("Player 1"), Player("Player 2")]
            if enforce_rules
            else [Player("Practice")]
        )

        table = Table.from_table_specs(prebuilt_specs(settings.gameplay.table_name))
        balls = get_rack(
            game_type=game_type,
            table=table,
            ball_params=None,
            ballset=None,
            spacing_factor=1e-3,
        )
        cue = Cue.from_game_type(game_type)
        cue.cue_ball_id = game.shot_constraints.cueball(balls)
        shot = System(table=table, balls=balls, cue=cue)

        self.attach_system(shot)
        self.attach_ruleset(game)

    def attach_system(self, system: System) -> None:
        multisystem.reset()
        multisystem.append(system)

    def attach_ruleset(self, ruleset: Ruleset) -> None:
        Global.game = ruleset

    def start(self):
        Global.task_mgr.run()


__all__ = [
    "Game",
    "ShotViewer",
    "FrameStepper",
]
