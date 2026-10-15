"""Windows release build (sections 99, 102, 105).

Produces ``dist/windows/PersonalizedAgent/PersonalizedAgent.exe`` and, when Inno
Setup is available, ``dist/windows/PersonalizedAgent-Setup.exe`` with a Start
Menu entry, an icon, user-level configuration and a clean uninstall.

Build releases from the build environment, not from a developer's own Python:
PyInstaller packages whatever the Python running it has installed, and a CUDA
torch or TensorFlow there adds gigabytes to the installer. Make it once (it
lives outside the repository, in %LOCALAPPDATA%\\PersonalizedAgent-build\\venv,
with the application's requirements, PyInstaller and a CPU-only torch), then
build with its Python::

    python build/make_build_env.py
    %LOCALAPPDATA%\\PersonalizedAgent-build\\venv\\Scripts\\python.exe build/windows/build_windows.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build.common import (  # noqa: E402
    APP_DIR_NAME,
    APP_NAME,
    PUBLISHER,
    BuildError,
    __version__,
    dist_dir,
    ensure_pyinstaller,
    prepare_assets,
    publish_file,
    publish_tree,
    pyinstaller_command,
    remove_tree,
    require_native_platform,
    run,
    staging_dir,
    which,
    work_dir,
    write_build_manifest,
)

TARGET = "windows"
EXE_NAME = "PersonalizedAgent"
HERE = Path(__file__).resolve().parent

VERSION_RESOURCE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{publisher}'),
        StringStruct('FileDescription', '{app_name}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{exe}'),
        StringStruct('OriginalFilename', '{exe}.exe'),
        StringStruct('ProductName', '{app_name}'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_resource() -> Path:
    major, minor, patch = (list(int(p) for p in __version__.split(".")) + [0, 0, 0])[:3]
    content = VERSION_RESOURCE.format(
        major=major, minor=minor, patch=patch, version=__version__,
        app_name=APP_NAME, publisher=PUBLISHER, exe=EXE_NAME,
    )
    path = work_dir(TARGET) / "version_info.txt"
    path.write_text(content, encoding="utf-8")
    return path


def build_executable(clean: bool = True) -> Path:
    """Package into a staging directory, then publish the finished tree."""
    version_file = write_version_resource()
    staging = staging_dir(TARGET)
    command = pyinstaller_command(
        TARGET,
        name=EXE_NAME,
        windowed=True,
        icon=HERE / "app.ico",
        extra=["--version-file", str(version_file)],
        distpath=staging,
        clean=clean,
    )
    run(command)
    staged = staging / EXE_NAME
    if not (staged / f"{EXE_NAME}.exe").exists():
        raise BuildError(f"PyInstaller did not produce {staged / (EXE_NAME + '.exe')}")
    published = publish_tree(staged, dist_dir(TARGET) / EXE_NAME)
    remove_tree(staging, description="staging directory")
    return published / f"{EXE_NAME}.exe"


def _iscc() -> str | None:
    """Inno Setup's compiler: on the PATH, or where its installer puts it -
    for this user only, or for everyone."""
    found = which("iscc") or which("ISCC")
    if found:
        return found
    import os
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("ProgramFiles")):
        if base:
            candidate = Path(base) / ("Programs" if base == os.environ.get("LOCALAPPDATA")
                                      else "") / "Inno Setup 6" / "ISCC.exe"
            if candidate.exists():
                return str(candidate)
    return None


def build_installer() -> Path | None:
    """Inno Setup installer; skipped with an explicit message when unavailable."""
    iscc = _iscc()
    script = HERE / "installer.iss"
    if not iscc:
        print("note: Inno Setup (iscc) not found - skipping the installer. "
              "Install Inno Setup 6 to produce PersonalizedAgent-Setup.exe.")
        return None
    run([
        iscc,
        f"/DMyAppVersion={__version__}",
        f"/DMyAppName={APP_NAME}",
        f"/DMyAppPublisher={PUBLISHER}",
        f"/DMyAppDirName={APP_DIR_NAME}",
        f"/DMySourceDir={dist_dir(TARGET) / EXE_NAME}",
        f"/DMyOutputDir={dist_dir(TARGET)}",
        str(script),
    ])
    installer = dist_dir(TARGET) / f"{EXE_NAME}-Setup.exe"
    return installer if installer.exists() else None


def build(skip_installer: bool = False, fast: bool = False) -> int:
    """Build the Windows artifact.

    ``fast`` is for iterating: it keeps PyInstaller's analysis cache and skips
    the installer and the portable archive, which are for shipping rather than
    for trying the application out.
    """
    require_native_platform(TARGET)
    ensure_pyinstaller()
    prepare_assets()
    executable = build_executable(clean=not fast)
    artifacts = [executable]
    installer = None if (skip_installer or fast) else build_installer()
    if installer:
        artifacts.append(installer)
    if not fast:
        # Build the archive in the staging area so the sync client sees one
        # final write rather than a file that grows for a minute.
        staging = staging_dir(TARGET)
        portable = shutil.make_archive(
            str(staging / f"{EXE_NAME}-{__version__}-windows-portable"),
            "zip", root_dir=str(dist_dir(TARGET) / EXE_NAME),
        )
        artifacts.append(
            publish_file(Path(portable),
                         dist_dir(TARGET) / f"{EXE_NAME}-{__version__}-windows-portable.zip")
        )
        remove_tree(staging, description="staging directory")
    write_build_manifest(TARGET, artifacts)
    print("\nWindows build complete:")
    for artifact in artifacts:
        print(f"  {artifact}")
    return 0


def main() -> int:
    return build(skip_installer="--no-installer" in sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
