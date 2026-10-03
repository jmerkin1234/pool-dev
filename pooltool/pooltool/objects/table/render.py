import numpy as np
from panda3d.core import CollisionNode, CollisionPlane, LineSegs, Plane, Point3, Vec3

from pooltool.ani.globals import Global
from pooltool.config import settings
from pooltool.objects.datatypes import Render
from pooltool.objects.table.datatypes import Table, TableModelDescr, TableType


class TableRender(Render):
    """A class for all pool table associated panda3d nodes"""

    POCKET_JAW_IDS = frozenset({"1", "2", "4", "5", "7", "8", "10", "11", "13", "14", "16", "17"})

    def __init__(self, table: Table):
        self._table = table
        self.debug_edge_names = []
        self.jaw_edge_names = []
        Render.__init__(self)

    def init_table(self):
        if (
            not self._table.model_descr
            or self._table.model_descr == TableModelDescr.null()
            or not settings.graphics.table
        ):
            # Rectangular playing surface (not a real table)
            model = Global.loader.loadModel(
                TableModelDescr.null().get_path(
                    settings.graphics.physical_based_rendering
                )
            )
            node = Global.render.find("scene").attachNewNode("table")
            model.reparentTo(node)
            model.setScale(self._table.w, self._table.l, 1)
        else:
            # Real table
            node = Global.loader.loadModel(
                self._table.model_descr.get_path(
                    settings.graphics.physical_based_rendering
                )
            )
            node.reparentTo(Global.render.find("scene"))
            node.setName("table")

        self.nodes["table"] = node
        self.collision_nodes = {}

    def init_collisions(self):
        if not settings.gameplay.cue_collision:
            return

        if self._table.table_type not in (
            TableType.BILLIARD,
            TableType.POCKET,
            TableType.SNOOKER,
        ):
            raise NotImplementedError()

        # Make 4 planes
        # For diagram of cushion ids, see
        # https://ekiefl.github.io/2020/12/20/pooltool-alg/#ball-cushion-collision-times
        for cushion_id in ["3", "9", "12", "18"]:
            cushion = self._table.cushion_segments.linear[cushion_id]

            x1, y1, z1 = cushion.p1
            x2, y2, z2 = cushion.p2

            n1, n2, n3 = cushion.normal
            if cushion_id in ["9", "12"]:
                # These normals need to be flipped
                n1, n2, n3 = -n1, -n2, -n3

            collision_node = self.nodes["table"].attachNewNode(
                CollisionNode(f"cushion_cplane_{cushion_id}")
            )
            collision_node.node().addSolid(
                CollisionPlane(Plane(Vec3(n1, n2, n3), Point3(x1, y1, z1)))
            )

            self.collision_nodes[f"cushion_ccapsule_{cushion_id}"] = collision_node

            # Keep collision planes active for gameplay. They are intentionally not
            # shown here: debug mode uses the cushion/pocket wireframe below, while
            # rendering Panda's collision solids makes the table unreadable.

        return collision_node

    def init_cushion_line(self, cushion_id):
        cushion = self._table.cushion_segments.linear[cushion_id]
        is_jaw = cushion_id in self.POCKET_JAW_IDS
        drawer = self.jaw_drawer if is_jaw else self.cushion_drawer

        drawer.moveTo(cushion.p1[0], cushion.p1[1], cushion.p1[2])
        drawer.drawTo(cushion.p2[0], cushion.p2[1], cushion.p2[2])
        node = (
            Global.render.find("scene")
            .find("table")
            .attachNewNode(drawer.create())
        )
        node.set_shader_auto(True)
        if is_jaw:
            node.setDepthOffset(20)

        node_name = f"jaw_{cushion_id}" if is_jaw else f"cushion_{cushion_id}"
        self.nodes[node_name] = node
        self.debug_edge_names.append(node_name)
        if is_jaw:
            self.jaw_edge_names.append(node_name)

    def init_cushion_circle(self, cushion_id):
        cushion = self._table.cushion_segments.circular[cushion_id]

        radius = cushion.radius
        center_x, center_y, center_z = cushion.center
        height = center_z

        circle = self.draw_circle(
            self.cushion_drawer, (center_x, center_y, height), radius, 30
        )
        node = Global.render.find("scene").find("table").attachNewNode(circle)
        node.set_shader_auto(True)
        self.nodes[f"cushion_{cushion_id}"] = node
        self.debug_edge_names.append(f"cushion_{cushion_id}")

    def init_cushion_edges(self):
        for cushion_id in self._table.cushion_segments.linear:
            self.init_cushion_line(cushion_id)

        for cushion_id in self._table.cushion_segments.circular:
            self.init_cushion_circle(cushion_id)

    def init_pocket(self, pocket_id):
        pocket = self._table.pockets[pocket_id]
        center = np.array(pocket.center, dtype=float, copy=True)
        cushion_heights = [
            segment.p1[2]
            for segment in self._table.cushion_segments.linear.values()
        ] + [
            segment.center[2]
            for segment in self._table.cushion_segments.circular.values()
        ]
        if cushion_heights:
            center[2] = max(center[2], max(cushion_heights) + 0.002)

        circle = self.draw_circle(self.pocket_drawer, center, pocket.radius, 100)
        node = Global.render.find("scene").find("table").attachNewNode(circle)
        node.set_shader_auto(True)
        node.setDepthOffset(20)
        self.nodes[f"pocket_{pocket_id}"] = node
        self.debug_edge_names.append(f"pocket_{pocket_id}")

    def set_debug_visualization(self, enabled: bool) -> None:
        """Show or hide table calibration wireframes in the live scene."""
        normal_edges = (
            not self._table.model_descr
            or self._table.model_descr == TableModelDescr.null()
            or not settings.graphics.table
        )

        for name in self.debug_edge_names:
            node = self.nodes.get(name)
            if node is None or node.isEmpty():
                continue

            if enabled:
                if name.startswith("jaw_"):
                    node.setColor(0.1, 1.0, 0.3, 1)
                elif name.startswith("cushion_"):
                    node.setColor(1, 0.2, 0.1, 1)
                else:
                    node.setColor(1, 0.9, 0.1, 1)
                node.show()
            elif normal_edges:
                node.setColor(1, 1, 1, 1)
                node.show()
            else:
                node.hide()

    def init_pockets(self):
        for pocket_id in self._table.pockets:
            self.init_pocket(pocket_id)

    def render(self):
        super().render()

        self.init_table()

        # Build the wireframe even when debug starts disabled so F3 can enable it
        # without rebuilding the scene.
        self.cushion_drawer = LineSegs()
        self.cushion_drawer.setThickness(3)
        self.cushion_drawer.setColor(1, 0.2, 0.1)
        self.jaw_drawer = LineSegs()
        self.jaw_drawer.setThickness(5)
        self.jaw_drawer.setColor(0.1, 1.0, 0.3)
        self.init_cushion_edges()

        self.pocket_drawer = LineSegs()
        self.pocket_drawer.setThickness(3)
        self.pocket_drawer.setColor(1, 0.9, 0.1)
        self.init_pockets()
        self.set_debug_visualization(settings.graphics.debug)

        self.init_collisions()

    def draw_circle(self, drawer, center, radius, num_points):
        center_x, center_y, height = center

        thetas = np.linspace(0, 2 * np.pi, num_points)
        for i in range(1, len(thetas)):
            curr_theta, prev_theta = thetas[i], thetas[i - 1]

            x_prev = center_x + radius * np.cos(prev_theta)
            y_prev = center_y + radius * np.sin(prev_theta)
            drawer.moveTo(x_prev, y_prev, height)

            x_curr = center_x + radius * np.cos(curr_theta)
            y_curr = center_y + radius * np.sin(curr_theta)
            drawer.drawTo(x_curr, y_curr, height)

        return drawer.create()

    def get_render_state(self):
        raise NotImplementedError("Can't call get_render_state for class 'TableRender'")

    def set_object_state_as_render_state(self):
        raise NotImplementedError(
            "Can't call set_object_state_as_render_state for class 'TableRender'"
        )

    def set_render_state_as_object_state(self):
        raise NotImplementedError(
            "Can't call set_render_state_as_object_state for class 'TableRender'"
        )
