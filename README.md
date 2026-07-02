# IEC 60909-0:2016 Fault Calculator — Android APK

## Prerequisites
- Ubuntu 22.04 / WSL2 / macOS (Linux VM if on Windows)
- Python 3.9+
- Java 17, Android SDK/NDK (auto-installed by Buildozer)

## Quick Start (Linux/WSL2)

```bash
# 1. Install dependencies
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf \
    libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
    cmake libffi-dev libssl-dev build-essential ccache

pip3 install --upgrade buildozer Cython==0.29.33 virtualenv kivy pandas numpy

# 2. Copy main.py and buildozer.spec to your project folder
mkdir iec60909_app && cd iec60909_app
# Copy main.py and buildozer.spec here

# 3. Build the APK (first run downloads SDK ~3-4 GB)
buildozer android debug

# 4. Install on device (USB debugging enabled)
buildozer android debug deploy run
```

## Google Colab (no Linux required)
```bash
!pip install buildozer kivy pandas numpy
!buildozer android debug
# Download APK from bin/ folder
```

## Using Google Colab
1. Upload main.py and buildozer.spec
2. Run: `!pip install buildozer && !buildozer android debug`
3. Download the APK from bin/*.apk

## App Features
- 3-phase, 2-phase, and phase-to-earth fault currents
- Maximum and minimum conditions per IEC 60909-0:2016
- Peak fault currents (ip) with kappa factor
- Transformer KT impedance correction
- Cable temperature correction (20C max, 70C min)
- Motor fault contribution
- Delta/Yn zero-sequence topology
- Interactive form with vector group spinner
- Results popup with color-coded values

## Input Fields
| Section | Fields |
|---|---|
| Grid | Voltage, Sk", R/X, Z0/Z1 |
| HV Cable | Length, R1, X1, R0, X0 (ohm/km) |
| Transformer | kVA, V_HV, V_LV, uk%, X/R, Z0/Z1, Pcu, Vector Group |
| LV Cable | Length, R1, X1, R0, X0 (ohm/km) |
| Motor | Power (kW), or 0 for none |

## Output
| Fault Type | Max (kA) | Min (kA) | Peak Max (kA) |
|---|---|---|---|
| 3-Phase I"k3 | ✓ | ✓ | ip3 |
| 2-Phase I"k2 | ✓ | ✓ | — |
| 1-Ph-Earth I"k1 | ✓ | ✓ | ip1 |
