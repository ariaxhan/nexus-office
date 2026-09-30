#!/bin/sh
# Source before xcodebuild. An old xcode-select or DEVELOPER_DIR may point at a
# disconnected volume; prefer the locally installed Xcode without changing the
# machine-wide developer selection.
if [ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
if ! xcrun --find xcodebuild >/dev/null 2>&1; then
  echo 'Office needs a local full Xcode installation (App Store: Xcode). Command Line Tools alone do not include xcodebuild.' >&2
  return 1
fi
