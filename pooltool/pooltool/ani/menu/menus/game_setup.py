from pooltool.ani.menu._datatypes import (
    BaseMenu,
    MenuBackButton,
    MenuButton,
    MenuHeader,
    MenuTitle,
)
from pooltool.ani.menu._factory import create_elements_from_dataclass
from pooltool.ani.menu._registry import MenuNavigator
from pooltool.config import settings


class GameSetupMenu(BaseMenu):
    name: str = "game_setup"

    def __init__(self) -> None:
        super().__init__()

        self.title = MenuTitle.create(text="New Game")
        self.back_button = MenuBackButton.create(MenuNavigator.go_to_menu("main_menu"))
        self.play_now_button = MenuButton.create(
            text="Start Game",
            command=MenuNavigator.start_new_game,
            description="Play a two-player game with turns, fouls and scoring.",
        )
        self.practice_button = MenuButton.create(
            text="Start Practice",
            command=MenuNavigator.start_practice,
            description="Practice solo. Move any ball with G; no shot calls or fouls.",
        )

    def populate(self) -> None:
        self.add_back_button(self.back_button)

        self.add_title(self.title)
        self.add_header(MenuHeader.create(text="Play"))
        self.add_button(self.play_now_button)
        self.add_button(self.practice_button)

        self.add_header(MenuHeader.create(text="Game Options"))
        for element, field in create_elements_from_dataclass(settings.gameplay):
            if field.name == "enforce_rules":
                element.cleanup()
                continue
            self.add_element(element)
