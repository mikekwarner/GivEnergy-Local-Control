---
name: release
description: >-
  Bump version, update documentation, rebuild Windows/Linux/Android release artifacts,
  commit and push to GitHub, and publish a GitHub Release with all dist/ assets.
  Use when the user asks to release a new version, upload to GitHub, or publish a release.
---

# Release Workflow for GivEnergy Local Control

When the user asks to release a new version `vX.Y.Z` (e.g. `release v2.1.2`):

1. **Bump versions**
   - Update `__version__ = "X.Y.Z"` in `givenergy_web/__init__.py`.
   - Increment `versionCode` by 1 and set `versionName = "X.Y.Z"` in `givenergy_android/app/build.gradle.kts`.

2. **Update documentation**
   - Update version references (`vX.Y.Z`) in `README.md` (title, Downloads heading, release tag links).
   - Document any new features or fixes in `README.md` (and `README_LINUX.md` if Linux/Pi install steps changed).

3. **Rebuild all release artifacts**
   - Run `cmd /c build_wizard_exe.bat` from the workspace root.
   - Verify exit code 0 and that all three artifacts are present in `dist/`:
     - `dist/GivEnergy-Local-Control-Installer.exe`
     - `dist/GivEnergy-Local-Control-Linux.tar.gz`
     - `dist/givenergy-local-control.apk`

4. **Commit & push to GitHub**
   - Review `git status` and stage changes (`git add -A` or targeted files — never commit secrets or `data/` DBs).
   - Commit with message: `Release vX.Y.Z: <concise summary of changes>`.
   - Push to `origin`: `git push`.

5. **Publish GitHub Release**
   - Run `gh release create vX.Y.Z dist/GivEnergy-Local-Control-Installer.exe dist/GivEnergy-Local-Control-Linux.tar.gz dist/givenergy-local-control.apk --title "GivEnergy Local Control vX.Y.Z" --notes "<changelog>"`.
   - Confirm the release URL and attached assets.
