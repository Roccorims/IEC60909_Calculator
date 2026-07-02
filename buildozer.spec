[app]
title = IEC 60909 Fault Calculator
package.name = iec60909faultcalc
package.domain = org.faultcalc
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
version = 1.0.0

requirements = python3,kivy,pandas,numpy

orientation = portrait
fullscreen = 0

android.permissions = 
android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
