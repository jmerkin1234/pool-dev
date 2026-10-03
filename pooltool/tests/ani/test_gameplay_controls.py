"""Regression coverage for game input and settings transitions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from panda3d.core import NodePath

from pooltool.ani.action import Action
from pooltool.ani.animate import Game
from pooltool.ani.constants import fine_aim_step, max_stroke_speed
from pooltool.ani.menu.menus.settings import SettingsMenu
from pooltool.ani.modes.aim import AimMode
from pooltool.ani.modes.datatypes import Mode, ModeManager
from pooltool.ani.modes.shot import ShotMode
from pooltool.ani.modes.stroke import StrokeMode
from pooltool.objects.cue.datatypes import Cue

TASK = SimpleNamespace(cont="cont", done="done", time=1)


class Mouse:
    dx = 0.0
    dy = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.touch()

    def touch(self):
        self.dx = self.dy = 0.0

    def get_dx(self):
        return self.dx

    def get_dy(self):
        return self.dy


@pytest.fixture
def stroke(monkeypatch):
    import pooltool.ani.modes.stroke as module

    cue = Cue(V0=1)
    node = NodePath("cue_stick")
    scene = Mock()
    scene.cue.get_node.return_value = node
    active = SimpleNamespace(cue=cue, balls={cue.cue_ball_id: object()}, strike=Mock())
    global_state = SimpleNamespace(
        mode_mgr=Mock(mode_stroked_from=Mode.aim),
        game=Mock(),
        clock=Mock(),
    )
    global_state.game.shot_constraints.can_shoot.return_value = True
    global_state.clock.getDt.return_value = 1 / 60
    pointer = Mouse()
    monkeypatch.setattr(module, "mouse", pointer)
    monkeypatch.setattr(module, "visual", scene)
    monkeypatch.setattr(module, "multisystem", SimpleNamespace(active=active))
    monkeypatch.setattr(module, "Global", global_state)
    monkeypatch.setattr(module, "hud", Mock())
    mode = StrokeMode()
    mode.reset_action_states()
    return SimpleNamespace(
        mode=mode,
        cue=cue,
        node=node,
        active=active,
        global_state=global_state,
        mouse=pointer,
    )


def test_releasing_s_holds_draw_and_power_until_space(stroke):
    mode = stroke.mode
    mode.keymap[Action.stroke] = True
    stroke.mouse.dy = -0.5
    mode.stroke_task(TASK)
    draw, power = stroke.node.getX(), stroke.cue.V0
    assert draw > 0 and power > 0
    mode.keymap[Action.stroke] = False
    for _ in range(30):
        stroke.mouse.dy = 1
        mode.stroke_task(TASK)
    assert stroke.node.getX() == draw
    assert stroke.cue.V0 == power
    stroke.active.strike.assert_not_called()

    mode.keymap[Action.exec_shot] = True
    mode.stroke_task(TASK)
    for _ in range(100):
        if mode.stroke_task(TASK) == TASK.done:
            break
    stroke.active.strike.assert_called_once_with()
    stroke.global_state.mode_mgr.change_mode.assert_called_once_with(Mode.calculate)
    assert stroke.node.getX() == 0
    assert stroke.cue.V0 == power


def test_mouse_forward_never_fires_and_draw_power_is_capped(stroke):
    stroke.mode.keymap[Action.stroke] = True
    stroke.mouse.dy = -100
    stroke.mode.stroke_task(TASK)
    assert stroke.cue.V0 == max_stroke_speed
    stroke.mouse.dy = 100
    stroke.mode.stroke_task(TASK)
    assert stroke.node.getX() == 0
    stroke.active.strike.assert_not_called()


def test_space_cannot_fire_uncalled_shot(stroke, monkeypatch):
    monkeypatch.setattr("pooltool.ani.modes.stroke.autils.TextOverlay", Mock())
    stroke.node.setX(0.2)
    stroke.global_state.game.shot_constraints.can_shoot.return_value = False
    stroke.mode.keymap[Action.exec_shot] = True
    stroke.mode.stroke_task(TASK)
    assert not stroke.mode.firing
    assert stroke.node.getX() == pytest.approx(0.2)
    stroke.active.strike.assert_not_called()


@pytest.mark.parametrize("overhead", [True, False])
def test_wheel_accumulates_fine_aim_without_mouse_drift(monkeypatch, overhead):
    import pooltool.ani.modes.aim as module

    cue = Cue(phi=359.9)
    monkeypatch.setattr(
        module, "multisystem", SimpleNamespace(active=SimpleNamespace(cue=cue))
    )
    scene = Mock()
    camera = Mock(phi=179.9)
    monkeypatch.setattr(module, "visual", scene)
    monkeypatch.setattr(module, "cam", camera)
    mode = AimMode()
    mode.reset_action_states()
    mode.camera_is_2d = overhead
    mode._consume_mouse_motion = Mock()
    mode.cue_avoidance = Mock()
    mode.fix_cue_stick_to_camera = Mock()
    mode.queue_fine_aim(fine_aim_step)
    mode.queue_fine_aim(fine_aim_step)
    mode.rotate()
    if overhead:
        assert cue.phi == pytest.approx(0.1)
    else:
        camera.rotate.assert_called_once_with(phi=pytest.approx(180.1))
    mode._consume_mouse_motion.assert_called_once()
    camera.rotate_via_mouse.assert_not_called()
    assert mode.fine_aim_delta == 0


def test_game_registers_no_playback_controls(monkeypatch):
    import pooltool.ani.modes.shot as module

    events = {}
    monkeypatch.setattr(module, "mouse", Mock())
    task_api = Mock()
    task_api.register_event.side_effect = lambda key, *args: events.setdefault(
        key, args
    )
    monkeypatch.setattr(module, "tasks", task_api)
    monkeypatch.setattr("pooltool.ani.modes.datatypes.tasks", task_api)
    mode = ShotMode()
    mode.view_only = False
    mode._update_hud = Mock()
    mode.enter()
    assert "escape" in events
    assert not (
        {
            "space",
            "r",
            "r-up",
            "z",
            "p-up",
            "n-up",
            "a",
            "arrow_left",
            "arrow_right",
            "arrow_up",
            "arrow_down",
        }
        & events.keys()
    )
    assert "shot_animation_task" not in [c.args[1] for c in task_api.add.call_args_list]


def test_game_over_transition_exits_shot_only_once(monkeypatch):
    import pooltool.ani.modes.shot as module

    mode = ShotMode()
    mode.reset_action_states()
    mode.exit = Mock()
    over = Mock()
    manager = ModeManager({})
    manager.modes = {Mode.shot: mode, Mode.game_over: over}
    manager.mode = Mode.shot
    manager.remove_mode_events = Mock()
    monkeypatch.setattr(module, "visual", SimpleNamespace(animation_finished=True))
    monkeypatch.setattr(
        module,
        "Global",
        SimpleNamespace(
            mode_mgr=manager,
            game=SimpleNamespace(shot_info=SimpleNamespace(game_over=True)),
        ),
    )
    assert mode.shot_view_task(TASK) == TASK.done
    assert manager.mode == Mode.game_over
    mode.exit.assert_called_once_with(key="advance")
    over.enter.assert_called_once_with()


def test_settings_does_not_create_resume_without_a_game(monkeypatch):
    import pooltool.ani.menu.menus.settings as module

    monkeypatch.setattr(module.BaseMenu, "__init__", lambda self: None)
    monkeypatch.setattr(module, "Global", SimpleNamespace(resume_available=False))
    monkeypatch.setattr(module, "MenuTitle", Mock())
    monkeypatch.setattr(module, "MenuHeader", Mock())
    monkeypatch.setattr(module, "MenuBackButton", Mock())
    button = Mock()
    monkeypatch.setattr(module, "MenuButton", button)
    monkeypatch.setattr(module, "create_elements_from_dataclass", lambda _: [])
    menu = SettingsMenu()
    menu.add_back_button = menu.add_title = menu.add_header = menu.add_button = Mock()
    menu.populate()
    button.create.assert_not_called()
    assert menu.resume_button is None


@pytest.mark.parametrize("mode", [Mode.aim, Mode.stroke, Mode.calculate, Mode.shot])
def test_pause_resume_keeps_scene_and_current_mode(monkeypatch, mode):
    import pooltool.ani.animate as module

    manager = Mock(mode=mode, last_mode=Mode.aim)
    scene = Mock(paused=False, animation_finished=False)
    global_state = SimpleNamespace(
        mode_mgr=manager, render=Mock(), resume_available=False
    )
    monkeypatch.setattr(module, "Global", global_state)
    monkeypatch.setattr(module, "visual", scene)
    monkeypatch.setattr(module, "hud", Mock())
    game = SimpleNamespace(scene_active=True, _suspended_mode=None)
    Game._pause_game(game)
    assert global_state.resume_available
    scene.teardown.assert_not_called()
    manager.change_mode.assert_called_once_with(
        Mode.menu, enter_kwargs={"menu_name": "settings"}
    )
    Game._resume_game(game)
    assert not global_state.resume_available
    manager.start_mode.assert_called_once_with(
        mode, **({"resuming": True} if mode == Mode.calculate else {})
    )
    scene.build_shot_animation.assert_not_called()
    scene.animate.assert_not_called()
    if mode == Mode.shot:
        scene.pause_animation.assert_called_once()
        scene.resume_animation.assert_called_once()
