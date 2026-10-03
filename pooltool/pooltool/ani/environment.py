#! /usr/bin/env python

from typing import Any, Dict, Iterable, Optional, Tuple, Union

from panda3d.core import (
    AmbientLight,
    DirectionalLight,
    NodePath,
    PerspectiveLens,
    PointLight,
    Spotlight,
)

from pooltool.ani.constants import model_dir
from pooltool.ani.globals import Global
from pooltool.config import settings
from pooltool.utils import panda_path


class Environment:
    """
    Manages the 3D environment for the simulation, including room, floor, and lighting.
    """
    def __init__(self) -> None:
        self.room: Optional[NodePath] = None
        self.floor: Optional[NodePath] = None
        self.room_loaded: bool = False
        self.floor_loaded: bool = False
        self.lights_loaded: bool = False

        self.shadow: bool = True

        self.slights: Dict[Union[int, str], NodePath] = {}
        self.plights: Dict[int, NodePath] = {}
        self.dlights: Dict[int, NodePath] = {}
        self.alnp: Optional[NodePath] = None

        shader = settings.graphics.shader
        lights = settings.graphics.lights

        self.slight_str: float = 4.0
        self.slight_color: Tuple[float, float, float, float] = (0.8, 0.8, 0.6, 1.0)

        self.plight_str: float = 4.0
        self.plight_color: Tuple[float, float, float, float] = (0.8, 0.8, 0.6, 1.0)

        self.dlight_str: float = 1.0 if (lights and not shader) else 3.0
        self.dlight_color: Tuple[float, float, float, float] = (0.8, 0.8, 0.7, 1.0)

        self.offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.table_w: float = 0.0
        self.table_l: float = 0.0
        self.lights_height: float = 0.0

    def init(self, table: Any) -> None:
        """Initializes the environment by setting offsets and conditionally loading models and lights."""
        suffix = "_pbr" if settings.graphics.physical_based_rendering else ""
        room_path = panda_path(model_dir / f"room/room{suffix}.glb")
        floor_path = panda_path(model_dir / f"room/floor{suffix}.glb")

        self.set_table_offset(table)

        if settings.graphics.room:
            self.load_room(room_path)
        if settings.graphics.floor:
            self.load_floor(floor_path)
        if settings.graphics.lights:
            self.load_lights()

    def get_slight(
        self,
        light_id: Union[int, str],
        pos: Tuple[float, float, float],
        hpr: Tuple[float, float, float],
        illuminates: Iterable[Optional[NodePath]],
        strength: Optional[float] = None,
        color: Optional[Tuple[float, float, float, float]] = None,
        fov: Union[float, Tuple[float, float]] = 60,
        shadows: bool = False,
        near: float = 0.01,
        far: float = 10.0,
        frustum: bool = False,
    ) -> NodePath:
        """Configures and attaches a Spotlight to the render graph."""
        if strength is None:
            strength = self.slight_str
        if color is None:
            color = self.slight_color

        adjusted_color = (
            strength * color[0],
            strength * color[1],
            strength * color[2],
            1.0,
        )

        slight = Spotlight(f"slight_{light_id}")
        slight.setColor(adjusted_color)
        slight.attenuation = (1, 0, 1)

        lens = PerspectiveLens()
        lens.setFov(fov)
        lens.setNear(near)
        lens.setFar(far)
        lens.setFocalLength(0.01)
        slight.setLens(lens)

        if shadows:
            slight.setShadowCaster(True, 512, 512)
            if frustum:
                slight.showFrustum()

        slnp = Global.render.attachNewNode(slight)
        slnp.setPos(
            (self.offset[0] + pos[0], self.offset[1] + pos[1], self.offset[2] + pos[2])
        )
        slnp.setHpr(hpr)

        for illuminated in illuminates:
            if illuminated is not None:
                illuminated.setLight(slnp)

        return slnp

    def get_plight(
        self,
        light_id: Union[int, str],
        pos: Tuple[float, float, float],
        illuminates: Iterable[Optional[NodePath]],
        strength: Optional[float] = None,
        color: Optional[Tuple[float, float, float, float]] = None,
    ) -> NodePath:
        """Configures and attaches a PointLight to the render graph."""
        if strength is None:
            strength = self.plight_str
        if color is None:
            color = self.plight_color

        adjusted_color = (
            strength * color[0],
            strength * color[1],
            strength * color[2],
            1.0,
        )

        plight = PointLight(f"plight_{light_id}")
        plight.setColor(adjusted_color)
        plight.attenuation = (1, 0, 1)

        plnp = Global.render.attachNewNode(plight)
        plnp.setPos(
            (self.offset[0] + pos[0], self.offset[1] + pos[1], self.offset[2] + pos[2])
        )

        for illuminated in illuminates:
            if illuminated is not None:
                illuminated.setLight(plnp)

        return plnp

    def get_dlight(
        self,
        light_id: Union[int, str],
        hpr: Tuple[float, float, float],
        illuminates: Iterable[Optional[NodePath]],
        strength: Optional[float] = None,
        color: Optional[Tuple[float, float, float, float]] = None,
        shadows: bool = False,
    ) -> NodePath:
        """Configures and attaches a DirectionalLight to the render graph."""
        if strength is None:
            strength = self.dlight_str
        if color is None:
            color = self.dlight_color

        adjusted_color = (
            strength * color[0],
            strength * color[1],
            strength * color[2],
            1.0,
        )

        dlight = DirectionalLight(f"dlight_{light_id}")
        dlight.setColor(adjusted_color)

        if shadows:
            dlight.setShadowCaster(True, 512, 512)

        dlnp = Global.render.attachNewNode(dlight)
        dlnp.setHpr(hpr)

        for illuminated in illuminates:
            if illuminated is not None:
                illuminated.setLight(dlnp)

        return dlnp

    def load_lights(self) -> None:
        """Instantiates all ambient, spot, point, and directional lights in the scene."""
        a_str = 0.1
        alight = AmbientLight("alight")
        alight.setColor((a_str, a_str, a_str, 1.0))
        self.alnp = Global.render.attachNewNode(alight)
        Global.render.setLight(self.alnp)

        self.slights = {
            # under bar #1
            "under_bar_1_1": self.get_slight(
                light_id=0,
                pos=(-4.0343, 0.83994, 0.97004),
                hpr=(-90, -95, 0),
                strength=2.0,
                far=1.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
            "under_bar_1_2": self.get_slight(
                light_id=1,
                pos=(-4.0343, -1.78035, 0.97004),
                hpr=(-90, -95, 0),
                strength=2.0,
                far=1.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
            "under_bar_1_3": self.get_slight(
                light_id=2,
                pos=(-4.0343, 3.18681, 0.97004),
                hpr=(-90, -95, 0),
                strength=2.0,
                far=1.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
            # under bar #2
            "under_bar_2_1": self.get_slight(
                light_id=3,
                pos=(1.6281, -4.7401, 0.96149),
                hpr=(0, -95, 0),
                strength=2.0,
                far=1.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
            "under_bar_2_2": self.get_slight(
                light_id=4,
                pos=(3.0487, -4.7401, 0.96149),
                hpr=(0, -95, 0),
                strength=2.0,
                far=1.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
            "cues": self.get_slight(
                light_id=5,
                pos=(0.068, -4.811 + 0.04, 2.2599 - 0.04),
                hpr=(0, -100, 0),
                fov=(30.0, 30.0),
                far=2.0,
                illuminates=(self.room, self.floor),
                shadows=self.shadow,
            ),
        }

        self.plights = {
            # cocktail corner
            8: self.get_plight(
                light_id=2,
                pos=(4.0877 - 0.08, 3.5745, 2.2042),
                illuminates=(Global.render.find("scene"),),
            ),
            # above bar #1
            5: self.get_plight(
                light_id=0,
                pos=(-4.1358 + 0.08, 1.9538, 2.2042),
                illuminates=(Global.render.find("scene"),),
            ),
            6: self.get_plight(
                light_id=1,
                pos=(-4.1358 + 0.08, -1.281, 2.2042),
                illuminates=(Global.render.find("scene"),),
            ),
            # above bar # 2
            7: self.get_plight(
                light_id=3,
                pos=(2.1875, -4.811 + 0.08, 2.1823),
                illuminates=(Global.render.find("scene"),),
            ),
        }

        self.dlights = {
            # above bar #1
            0: self.get_dlight(
                light_id=0,
                hpr=(0, -90, 0),
                illuminates=(Global.render.find("scene").find("table"),),
                shadows=False,
            ),
        }

        self.lights_loaded = True

    def set_table_offset(self, table: Any) -> None:
        """Calculates and stores offsets based on table geometry."""
        self.offset = (table.w / 2.0, table.l / 2.0, -table.height)
        self.table_w = table.w
        self.table_l = table.l
        self.lights_height = table.lights_height + table.height

    def load_room(self, path: str) -> NodePath:
        """Loads and positions the room model."""
        self.room = Global.loader.loadModel(panda_path(path))
        self.room.reparentTo(Global.render.find("scene"))
        self.room.setPos(self.offset)
        self.room.setName("room")

        self.room_loaded = True
        return self.room

    def load_floor(self, path: str) -> NodePath:
        """Loads and positions the floor model."""
        self.floor = Global.loader.loadModel(panda_path(path))
        self.floor.reparentTo(Global.render.find("scene"))
        self.floor.setPos(self.offset)
        self.floor.setName("floor")

        self.floor_loaded = True
        return self.floor

    def unload_room(self) -> None:
        """Removes the room model from the scene graph and cleans up resources."""
        if not self.room_loaded or self.room is None:
            return

        self.room.removeNode()
        self.room = None
        self.room_loaded = False

    def unload_floor(self) -> None:
        """Removes the floor model from the scene graph and cleans up resources."""
        if not self.floor_loaded or self.floor is None:
            return

        self.floor.removeNode()
        self.floor = None
        self.floor_loaded = False

    def unload_lights(self) -> None:
        """Removes all lighting nodes and clears dictionary caches."""
        Global.render.clearLight()

        if not self.lights_loaded:
            return

        if self.alnp is not None:
            self.alnp.removeNode()
            self.alnp = None

        for light in self.slights.values():
            light.removeNode()
        for light in self.plights.values():
            light.removeNode()
        for light in self.dlights.values():
            light.removeNode()

        self.slights.clear()
        self.plights.clear()
        self.dlights.clear()

        self.lights_loaded = False

    def teardown(self) -> None:
        """Completely resets the environment, cleaning up geometry and lights."""
        self.unload_room()
        self.unload_floor()
        self.unload_lights()
