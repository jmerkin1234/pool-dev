# Playing pool

Restart pooltool after updating the code. An already running copy keeps the old controls.

In **New Game**, select the rack and table, then choose:

- **Start Game**: two local players, with the selected game's rules, fouls and turns.
- **Start Practice**: solo play, with no required shot calls or fouls. Hold G to reposition balls. A scratched cue ball is respotted automatically.

## Controls

| Action | Control |
| --- | --- |
| Fast aim | Move the mouse |
| Fine aim | Scroll the mouse wheel; each notch adjusts aim by 0.1 degrees |
| Change view | V toggles overhead and 3D aiming |
| Draw the cue / set power | Hold S and move the mouse down |
| Keep the cue drawn | Release S; the cue and power stay where you left them |
| Shoot | Space; the drawn cue moves forward and strikes once |
| Cancel the draw | A returns to aiming |
| Adjust power without drawing | Hold X and move the mouse, then Space to shoot |
| Spin | Hold E and move the mouse |
| Cue elevation | Hold B and move the mouse |
| Call a shot when required | Hold C, select the ball, then the pocket |
| Move an allowed ball | Hold G and click to select, then click to place |
| Open settings | Escape |
| Resume | Resume Game inside Settings, or Escape from the menu |
| Show controls | H |

Opening settings preserves the live table, drawn cue, power, and the progress of a moving shot. After the balls stop, the game applies the result and prepares the next turn automatically. Replay, undo, rewind, slow motion, and shot-history navigation are unavailable in game and practice mode.

## Verification and remaining observations

The automated suite passed, including regression checks for cue hold, Space-only firing, wheel aiming, settings visibility, suspended shots, and the game-over transition. A separate rendered runtime check exercised both play modes, repeated settings/resume cycles, cue hold, and a complete practice shot with pauses during calculation and ball motion.

Two existing rendering issues were observed: the right-side power/spin display is partly clipped at 1600 x 900, and the offscreen graphics check logged generated-shader/OpenGL errors with the current graphics settings. These need a separate rendering pass.
