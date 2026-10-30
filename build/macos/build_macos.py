"""macOS release build (sections 99, 103, 105).

Produces ``dist/macos/Personal Agent WaterMelon.app``; when ``hdiutil`` is
available, ``dist/macos/PersonalAgentWaterMelon.dmg``; and when ``pkgbuild`` and
``productbuild`` are, ``dist/macos/PersonalAgentWaterMelon.pkg`` - an installer that
asks, as a choice ticked by default, whether the agent starts when the user
signs in (the only page of the macOS Installer that can ask: Installation
Type). The program makes it so itself (``--autostart on|off``): the same
LaunchAgent and setting as Settings > General.

The bundle declares the permissions the agent actually needs - Apple Events for
active-application detection, accessibility usage, notifications - instead of
assuming they are already granted.  Code signing, hardened runtime and
notarization run when signing identities are provided through the environment.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build.common import (  # noqa: E402
    FILE_NAME,
    APP_ID,
    APP_NAME,
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
    write_build_manifest,
)

TARGET = "macos"
BUNDLE_NAME = APP_NAME
#: The application as it was named before (to 1.0.0): replaced by this one.
FORMER_BUNDLE_NAMES = ("Personalized Agent",)
HERE = Path(__file__).resolve().parent

USAGE_DESCRIPTIONS = {
    "NSAppleEventsUsageDescription":
        f"{APP_NAME} reads which application is in the foreground so it can describe your "
        "activity back to you. You can switch activity monitoring off in Settings.",
    "NSSystemAdministrationUsageDescription":
        f"{APP_NAME} observes application usage to build your personal activity overview.",
    "NSDesktopFolderUsageDescription":
        f"{APP_NAME} monitors the folders you choose for file activity.",
    "NSDocumentsFolderUsageDescription":
        f"{APP_NAME} reads and writes files in the workspace you authorize.",
    "NSDownloadsFolderUsageDescription":
        f"{APP_NAME} monitors the folders you choose for file activity.",
}


def bundle_path() -> Path:
    return dist_dir(TARGET) / f"{BUNDLE_NAME}.app"


def build_bundle() -> Path:
    """Package into a staging directory, then publish the finished bundle."""
    staging = staging_dir(TARGET)
    command = pyinstaller_command(
        TARGET,
        name=BUNDLE_NAME,
        windowed=True,
        icon=HERE / "app.icns",
        extra=["--osx-bundle-identifier", APP_ID],
        distpath=staging,
    )
    run(command)
    staged = staging / f"{BUNDLE_NAME}.app"
    if not staged.exists():
        raise BuildError(f"PyInstaller did not produce {staged}")
    app = publish_tree(staged, bundle_path())
    remove_tree(staging, description="staging directory")
    return app


def patch_info_plist(app: Path) -> None:
    """Version metadata and permission declarations (sections 99, 105)."""
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.exists():
        raise BuildError(f"missing {plist_path}")
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info.update(
        {
            # The menu bar's name; the bundle's folder keeps the 1.0.0 name,
            # so an upgrade replaces it in place.
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": APP_ID,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            # The desktop character is an accessory window, not a dock app.
            "LSUIElement": False,
            **USAGE_DESCRIPTIONS,
        }
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)
    print(f"Info.plist updated: {plist_path}")


def sign_and_notarize(app: Path) -> None:
    identity = os.environ.get("MACOS_SIGN_IDENTITY")
    if not identity:
        # SIGNED AGAIN ALL THE SAME, by no one. PyInstaller signs the bundle
        # as it makes it, and the Info.plist written above no longer matches
        # that signature. macOS then refuses the application its keychain
        # (error -67030, "make sure executable is signed") - and the agent
        # quit on start, its identity key unstored, before its window or
        # its menu-bar icon appeared.
        print("note: MACOS_SIGN_IDENTITY is not set - signing ad hoc, no notarization.")
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
        run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        return
    profile = os.environ.get("MACOS_NOTARY_PROFILE")
    if not profile:
        # THE PROJECT'S OWN CERTIFICATE, not Apple's: nothing to notarize,
        # and Apple's time-stamp service stamps only Apple's. What it gives
        # is one identity from version to version. macOS keeps what was
        # allowed (Accessibility, Screen Recording) against the signature;
        # signed ad hoc, each version was another program, and an update
        # left the switches on in System Settings and the agent not allowed.
        print("note: MACOS_NOTARY_PROFILE is not set - signed, not notarized.")
        run(["codesign", "--force", "--deep", "--timestamp=none",
             "--sign", identity, str(app)])
        run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        return
    entitlements = HERE / "entitlements.plist"
    run([
        "codesign", "--force", "--deep", "--timestamp",
        "--options", "runtime",
        "--entitlements", str(entitlements),
        "--sign", identity, str(app),
    ])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    archive = dist_dir(TARGET) / "notarize.zip"
    run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
    notarize(archive)
    run(["xcrun", "stapler", "staple", str(app)])
    archive.unlink(missing_ok=True)


def _notary_args() -> list:
    """How notarytool finds the credentials: the profile stored, and the
    keychain it was stored in when that is not the user's own (CI's)."""
    args = ["--keychain-profile", os.environ["MACOS_NOTARY_PROFILE"]]
    keychain = os.environ.get("MACOS_NOTARY_KEYCHAIN")
    return args + (["--keychain", keychain] if keychain else [])


def notarize(path: Path) -> None:
    """`path` sent to Apple's notary service and waited for. Not accepted,
    what Apple found is printed - each file it refused, and why - and the
    build fails: a release that would not open without a warning is not
    made."""
    import json
    import subprocess
    done = subprocess.run(
        ["xcrun", "notarytool", "submit", str(path), *_notary_args(), "--wait",
         "--output-format", "json"], capture_output=True, text=True)
    print(done.stdout)
    print(done.stderr, file=sys.stderr)
    try:
        said = json.loads(done.stdout or "{}")
    except ValueError:
        said = {}
    if str(said.get("status") or "") != "Accepted":
        if said.get("id"):
            subprocess.run(["xcrun", "notarytool", "log", str(said["id"]), *_notary_args()])
        raise SystemExit(f"notarization of {path.name} was not accepted: "
                         f"{said.get('status') or done.returncode}")


def notarized(path: Path) -> bool:
    """The .dmg or .pkg notarized and its ticket stapled, when Apple's
    Developer ID signs it (MACOS_NOTARY_PROFILE set): one opened from a
    download is not stopped by Gatekeeper, with the network off too."""
    if not os.environ.get("MACOS_NOTARY_PROFILE") or not path.exists():
        return False
    notarize(path)
    run(["xcrun", "stapler", "staple", str(path)])
    return True


def build_dmg(app: Path) -> Path | None:
    if not which("hdiutil"):
        print("note: hdiutil not found - skipping the .dmg.")
        return None
    staging = staging_dir(TARGET)
    dmg_staging = staging / "dmg-contents"
    dmg_staging.mkdir(parents=True)
    dmg = staging / f"{FILE_NAME}.dmg"
    shutil.copytree(app, dmg_staging / app.name, symlinks=True)
    os.symlink("/Applications", dmg_staging / "Applications")
    run([
        "hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(dmg_staging),
        "-ov", "-format", "UDZO", str(dmg),
    ])
    if not dmg.exists():
        return None
    identity = os.environ.get("MACOS_SIGN_IDENTITY")
    if identity and os.environ.get("MACOS_NOTARY_PROFILE"):
        run(["codesign", "--force", "--timestamp", "--sign", identity, str(dmg)])
        notarized(dmg)
    published = publish_file(dmg, dist_dir(TARGET) / f"{FILE_NAME}.dmg")
    remove_tree(staging, description="staging directory")
    return published


#: Run as root by the Installer: the program is run as the user at the
#: console, whose LaunchAgent and settings these are.
_AS_THE_USER = """#!/bin/sh
# The application as it was named before is replaced by this one: left,
# its sign-in entry would be another copy's.
for FORMER in {former}; do
  [ "$FORMER" = "{bundle}" ] || rm -rf "/Applications/$FORMER.app"
done
USER_NAME=$(stat -f%Su /dev/console)
[ -z "$USER_NAME" ] || [ "$USER_NAME" = "root" ] && exit 0
sudo -u "$USER_NAME" "/Applications/{bundle}.app/Contents/MacOS/{bundle}" --autostart {state} || true
exit 0
"""

_DISTRIBUTION = """<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>{name}</title>
  <options customize="always" require-scripts="false" hostArchitectures="arm64,x86_64"/>
  <domains enable_localSystem="true"/>
  <choices-outline>
    <line choice="app"/>
    <line choice="autostart"/>
  </choices-outline>
  <choice id="app" title="{name}" enabled="false" selected="true"
          description="The application, in /Applications.">
    <pkg-ref id="{app_id}"/>
  </choice>
  <choice id="autostart" title="Start {name} when I sign in" start_selected="true"
          description="The agent starts when you sign in to this Mac. It can be changed later in Settings &gt; General.">
    <pkg-ref id="{app_id}.autostart"/>
  </choice>
  <pkg-ref id="{app_id}" version="{version}">app.pkg</pkg-ref>
  <pkg-ref id="{app_id}.autostart" version="{version}">autostart.pkg</pkg-ref>
</installer-gui-script>
"""


def build_pkg(app: Path) -> Path | None:
    """An installer whose choices end with starting at sign-in, ticked."""
    if not (which("pkgbuild") and which("productbuild")):
        print("note: pkgbuild/productbuild not found - skipping the .pkg.")
        return None
    staging = staging_dir(TARGET)
    work = staging / "pkg"
    scripts_off, scripts_on = work / "scripts-app", work / "scripts-autostart"
    for folder, state in ((scripts_off, "off"), (scripts_on, "on")):
        folder.mkdir(parents=True)
        script = folder / "postinstall"
        script.write_text(_AS_THE_USER.format(
            bundle=BUNDLE_NAME, state=state,
            former=" ".join(f'"{one}"' for one in FORMER_BUNDLE_NAMES)), encoding="utf-8")
        script.chmod(0o755)
    # The application first, saying "off"; then, if ticked, "on" - so a
    # choice left unticked is not turned on by the program's own default.
    run(["pkgbuild", "--component", str(app), "--install-location", "/Applications",
         "--identifier", APP_ID, "--version", __version__,
         "--scripts", str(scripts_off), str(work / "app.pkg")])
    run(["pkgbuild", "--nopayload", "--identifier", f"{APP_ID}.autostart",
         "--version", __version__, "--scripts", str(scripts_on), str(work / "autostart.pkg")])
    distribution = work / "distribution.xml"
    distribution.write_text(_DISTRIBUTION.format(name=APP_NAME, app_id=APP_ID,
                                                 version=__version__), encoding="utf-8")
    product = staging / f"{FILE_NAME}.pkg"
    command = ["productbuild", "--distribution", str(distribution),
               "--package-path", str(work), str(product)]
    identity = os.environ.get("MACOS_INSTALLER_IDENTITY")
    if identity:
        command[1:1] = ["--sign", identity, "--timestamp"]
    run(command)
    if not product.exists():
        return None
    if identity:
        notarized(product)
    published = publish_file(product, dist_dir(TARGET) / f"{FILE_NAME}.pkg")
    remove_tree(staging, description="staging directory")
    return published


def build() -> int:
    require_native_platform(TARGET)
    ensure_pyinstaller()
    prepare_assets()
    app = build_bundle()
    patch_info_plist(app)
    sign_and_notarize(app)
    artifacts = [app]
    dmg = build_dmg(app)
    if dmg:
        artifacts.append(dmg)
    pkg = build_pkg(app)
    if pkg:
        artifacts.append(pkg)
    write_build_manifest(TARGET, artifacts)
    print("\nmacOS build complete:")
    for artifact in artifacts:
        print(f"  {artifact}")
    return 0


def main() -> int:
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
