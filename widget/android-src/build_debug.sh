#!/bin/bash
# Builds app/build/outputs/apk/debug/app-debug.apk. Needs a JDK 17+ and the Android SDK (build-tools 34).
export JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
cd "$(dirname "$0")"
[ -f local.properties ] || echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew assembleDebug --no-daemon 2>&1
