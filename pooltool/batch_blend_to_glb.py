"""Batch-convert .blend files to .glb using the bpy 5.2 pip module.

Nothing here is inferred or assumed silently:
- Each input file's actual header bytes are checked to see if it's
  zstd-compressed. If it is, it gets explicitly decompressed to a temp
  file first. If it isn't, it's opened as-is. Either way the check
  happens - it's never skipped.
- Every failure prints a full traceback and is listed by name in the
  final summary. A bad file doesn't halt the batch, but it also never
  disappears silently.

One-time setup:
    pip install uv --break-system-packages
    uv python install 3.13
    uv venv ~/venv-bpy --python 3.13
    uv pip install --python ~/venv-bpy/bin/python bpy

Usage:
    ~/venv-bpy/bin/python batch_blend_to_glb.py [<file_or_dir>] [output_dir] [custom_name]

    If <file_or_dir> is omitted, a native system file selection dialog opens.
    A GUI dialog will automatically prompt you to enter a custom name if one isn't provided via the command line.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def get_file_via_gui() -> Path | None:
    """Open a native system dialog to select a .blend file."""
    # macOS GUI selection
    if sys.platform == "darwin":
        cmd = (
            "osascript -e 'Formpos clean file selection' -e "
            "'(POSIX path of (choose file with prompt \"Select a .blend file\" of type {\"blend\"}))'"
        )
        try:
            out = subprocess.check_output(cmd, shell=True, text=True).strip()
            return Path(out) if out else None
        except subprocess.CalledProcessError:
            return None

    # Windows GUI selection
    elif sys.platform == "win32":
        cmd = (
            "powershell -Command \"Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.OpenFileDialog; "
            "$f.Filter = 'Blender Files (*.blend)|*.blend'; "
            "$f.ShowHelp = $true; "
            "if ($f.ShowDialog() -eq 'OK') { $f.FileName }\""
        )
        try:
            out = subprocess.check_output(cmd, shell=True, text=True).strip()
            return Path(out) if out else None
        except subprocess.CalledProcessError:
            return None

    # Linux fallback via kdialog (KDE) or Zenity (GTK)
    elif sys.platform.startswith("linux"):
        if shutil.which("kdialog"):
            try:
                out = subprocess.check_output(
                    ["kdialog", "--getopenfilename", ".", "*.blend | Blender Files (*.blend)", "--title", "Select a .blend file"],
                    text=True,
                ).strip()
                return Path(out) if out else None
            except subprocess.CalledProcessError:
                return None
        elif shutil.which("zenity"):
            try:
                out = subprocess.check_output(
                    ["zenity", "--file-selection", "--file-filter=*.blend", "--title=Select a .blend file"],
                    text=True,
                ).strip()
                return Path(out) if out else None
            except subprocess.CalledProcessError:
                return None
    return None


def get_name_via_gui() -> str:
    """Open a native system dialog to ask for a custom name."""
    # macOS GUI text input
    if sys.platform == "darwin":
        cmd = "osascript -e 'text returned of (display dialog \"Enter a custom name for the output file(s) (or leave blank to keep original):\" default answer \"\")'"
        try:
            return subprocess.check_output(cmd, shell=True, text=True).strip()
        except subprocess.CalledProcessError:
            return ""

    # Windows GUI text input
    elif sys.platform == "win32":
        cmd = (
            "powershell -Command \"[Reflection.Assembly]::LoadWithPartialName('Microsoft.VisualBasic') | Out-Null; "
            "[Microsoft.VisualBasic.Interaction]::InputBox('Enter a custom name for the output file(s) (or leave blank to keep original):', 'Custom Name')\""
        )
        try:
            return subprocess.check_output(cmd, shell=True, text=True).strip()
        except subprocess.CalledProcessError:
            return ""

    # Linux text input via kdialog (KDE) or Zenity (GTK)
    elif sys.platform.startswith("linux"):
        if shutil.which("kdialog"):
            try:
                return subprocess.check_output(
                    ["kdialog", "--inputbox", "Enter a custom name for the output file(s) (or leave blank to keep original):", "--title", "Output Name"],
                    text=True,
                ).strip()
            except subprocess.CalledProcessError:
                return ""
        elif shutil.which("zenity"):
            try:
                return subprocess.check_output(
                    ["zenity", "--entry", "--title=Output Name", "--text=Enter a custom name for the output file(s) (or leave blank to keep original):"],
                    text=True,
                ).strip()
            except subprocess.CalledProcessError:
                return ""
    return ""


def resolve_input_blend(blend_path: Path, tmp_dir: Path) -> Path:
    """Return a path bpy can open directly.

    Reads the file's actual magic bytes rather than assuming a format.
    zstd-compressed .blend files are decompressed to a temp copy;
    plain or gzip-compressed .blend files (bpy reads gzip natively)
    are returned untouched.
    """
    with open(blend_path, "rb") as f:
        header = f.read(4)

    if header != ZSTD_MAGIC:
        return blend_path

    if shutil.which("zstd") is None:
        raise RuntimeError(
            f"{blend_path.name} is zstd-compressed and the 'zstd' CLI isn't "
            "installed (apt-get install zstd / brew install zstd)."
        )

    decompressed = tmp_dir / f"{blend_path.stem}__decompressed.blend"
    subprocess.run(
        ["zstd", "-d", "-f", str(blend_path), "-o", str(decompressed)],
        check=True,
        capture_output=True,
    )
    return decompressed


def convert_one(blend_path: Path, output_dir: Path, tmp_dir: Path, custom_name: str | None = None) -> tuple[Path, float, int]:
    """Decompress if needed, open, and export to a raw Panda3D-ready .glb.

    Maintains uncompressed structures and clean naming maps.
    """
    openable_path = resolve_input_blend(blend_path, tmp_dir)

    bpy.ops.wm.open_mainfile(filepath=str(openable_path))

    # Apply custom name if provided, otherwise fallback to original stem
    if custom_name:
        output_path = output_dir / f"{custom_name}.glb"
    else:
        output_path = output_dir / f"{blend_path.stem}.glb"

    # Panda3D-optimized export configuration:
    # NO DRACO COMPRESSION, NO IMAGE DOWNSCALING, FULL NAMING ATTRIBUTES PRESERVED
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_image_format="AUTO",  # Does not change image types or quality
        export_attributes=True,      # FIXED: Replaced old export_colors with export_attributes for Blender 5.x
        export_extras=True,          # Exports original Blender custom names and attributes
        export_draco_mesh_compression_enable=False, # EXPLICITLY DISABLED MESH COMPRESSION
        export_cameras=False,
        export_lights=False,
        use_selection=False,
    )

    size_mb = output_path.stat().st_size / (1024 * 1024)
    verts = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH" and o.data is not None)
    return output_path, size_mb, verts


def main() -> None:
    # If no file/directory argument is given, trigger the GUI pop-up window
    if len(sys.argv) < 2:
        gui_path = get_file_via_gui()
        if not gui_path:
            raise SystemExit("No file selected. Exiting.")
        input_arg = gui_path
    else:
        input_arg = Path(sys.argv[1])

    blend_files = [input_arg] if input_arg.is_file() else sorted(input_arg.glob("*.blend"))
    if not blend_files:
        raise SystemExit(f"No .blend files found at {input_arg}")

    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        input_arg.parent if input_arg.is_file() else input_arg
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Capture the optional custom name argument
    custom_name_arg = sys.argv[3] if len(sys.argv) > 3 else None

    # Trigger GUI text input if running without CLI arguments
    if not custom_name_arg and len(sys.argv) < 4:
        user_input = get_name_via_gui()
        if user_input:
            custom_name_arg = user_input

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for i, blend_path in enumerate(blend_files):
            print(f"\n=== {blend_path.name} ===")

            # Format the name safely for batch runs
            current_name = custom_name_arg
            if current_name and len(blend_files) > 1:
                current_name = f"{custom_name_arg}_{i+1}"

            try:
                out, size_mb, verts = convert_one(blend_path, output_dir, tmp_dir, current_name)
                print(f"OK -> {out.name} ({size_mb:.1f} MB, {verts:,} verts)")
            except Exception:
                print(f"FAILED: {blend_path.name}")
                print(traceback.format_exc())
                failures.append(blend_path.name)

    print(f"\n{len(blend_files) - len(failures)}/{len(blend_files)} converted -> {output_dir}")
    if failures:
        print(f"FAILED FILES ({len(failures)}): {', '.join(failures)}")


if __name__ == "__main__":
    main()
