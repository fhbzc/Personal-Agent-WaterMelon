"""Linux release build (sections 99, 103).

Produces ``dist/linux/PersonalizedAgent/`` with a launchable binary and, when
the tooling is present, ``PersonalizedAgent.AppImage`` and/or
``personalized-agent_<version>_amd64.deb``.  A tar.gz is always produced so a
build is never left without a distributable artifact.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build.common import (  # noqa: E402
    APP_NAME,
    APP_SLUG,
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

TARGET = "linux"
BINARY_NAME = "PersonalizedAgent"
HERE = Path(__file__).resolve().parent

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name={app_name}
Comment=Personal desktop AI agent
Exec={exec_name}
Icon={slug}
Terminal=false
Categories=Utility;Office;
StartupNotify=true
X-AppVersion={version}
"""


def build_binary() -> Path:
    """Package into a staging directory, then publish the finished tree."""
    staging = staging_dir(TARGET)
    run(pyinstaller_command(TARGET, name=BINARY_NAME, windowed=True,
                            icon=HERE / "app.png", distpath=staging))
    staged = staging / BINARY_NAME
    if not (staged / BINARY_NAME).exists():
        raise BuildError(f"PyInstaller did not produce {staged / BINARY_NAME}")
    published = publish_tree(staged, dist_dir(TARGET) / BINARY_NAME)
    remove_tree(staging, description="staging directory")
    return published / BINARY_NAME


def write_desktop_entry(path: Path, exec_name: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DESKTOP_ENTRY.format(app_name=APP_NAME, exec_name=exec_name, slug=APP_SLUG,
                             version=__version__),
        encoding="utf-8",
    )
    return path


def build_tarball() -> Path:
    staging = staging_dir(TARGET)
    archive = shutil.make_archive(
        str(staging / f"{APP_SLUG}-{__version__}-linux-x86_64"),
        "gztar", root_dir=str(dist_dir(TARGET)), base_dir=BINARY_NAME,
    )
    published = publish_file(
        Path(archive), dist_dir(TARGET) / f"{APP_SLUG}-{__version__}-linux-x86_64.tar.gz"
    )
    remove_tree(staging, description="staging directory")
    return published


def build_appimage() -> Path | None:
    tool = which("appimagetool") or which("appimagetool-x86_64.AppImage")
    if not tool:
        print("note: appimagetool not found - skipping the AppImage.")
        return None
    appdir = work_dir(TARGET) / f"{BINARY_NAME}.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr" / "bin").mkdir(parents=True)
    shutil.copytree(dist_dir(TARGET) / BINARY_NAME, appdir / "usr" / "bin" / BINARY_NAME)
    write_desktop_entry(appdir / f"{APP_SLUG}.desktop", "AppRun")
    icon_source = HERE / "app.png"
    if icon_source.exists():
        shutil.copy2(icon_source, appdir / f"{APP_SLUG}.png")
    else:  # AppImage requires an icon file to exist
        (appdir / f"{APP_SLUG}.png").write_bytes(_placeholder_png())
    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        f'exec "$HERE/usr/bin/{BINARY_NAME}/{BINARY_NAME}" "$@"\n',
        encoding="utf-8",
    )
    apprun.chmod(0o755)
    output = dist_dir(TARGET) / f"{BINARY_NAME}.AppImage"
    run([tool, str(appdir), str(output)], check=False)
    return output if output.exists() else None


def build_deb() -> Path | None:
    if not which("dpkg-deb"):
        print("note: dpkg-deb not found - skipping the .deb package.")
        return None
    arch = "amd64" if platform.machine() in ("x86_64", "AMD64") else platform.machine()
    root = work_dir(TARGET) / "deb"
    if root.exists():
        shutil.rmtree(root)
    install_dir = root / "opt" / APP_SLUG
    install_dir.mkdir(parents=True)
    shutil.copytree(dist_dir(TARGET) / BINARY_NAME, install_dir, dirs_exist_ok=True)

    bin_dir = root / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / APP_SLUG
    launcher.write_text(f'#!/bin/sh\nexec /opt/{APP_SLUG}/{BINARY_NAME} "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)

    write_desktop_entry(root / "usr" / "share" / "applications" / f"{APP_SLUG}.desktop", APP_SLUG)

    control_dir = root / "DEBIAN"
    control_dir.mkdir(parents=True)
    (control_dir / "control").write_text(
        f"Package: {APP_SLUG}\n"
        f"Version: {__version__}\n"
        f"Section: utils\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Maintainer: {PUBLISHER}\n"
        f"Description: {APP_NAME}\n"
        f" Personal desktop AI agent with activity awareness, a knowledge graph\n"
        f" and a scheduler. Bundles its own runtime.\n",
        encoding="utf-8",
    )
    output = dist_dir(TARGET) / f"{APP_SLUG}_{__version__}_{arch}.deb"
    run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(output)], check=False)
    return output if output.exists() else None


def _placeholder_png() -> bytes:
    # 1x1 transparent PNG, used only when no icon asset is provided.
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def build() -> int:
    require_native_platform(TARGET)
    ensure_pyinstaller()
    prepare_assets()
    binary = build_binary()
    write_desktop_entry(dist_dir(TARGET) / f"{APP_SLUG}.desktop", APP_SLUG)
    artifacts = [binary, build_tarball()]
    for optional in (build_appimage(), build_deb()):
        if optional:
            artifacts.append(optional)
    write_build_manifest(TARGET, artifacts)
    print("\nLinux build complete:")
    for artifact in artifacts:
        print(f"  {artifact}")
    return 0


def main() -> int:
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
