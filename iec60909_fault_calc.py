#!/usr/bin/env python3
"""
IEC 60909-0:2016 Short-Circuit Current Calculator
==================================================
Network: Grid → HV Cable → Transformer → LV Cable → [Motor] → Fault

Calculates max and min fault currents for:
  - 3-phase bolted fault (I"k3)
  - 2-phase line-to-line fault (I"k2)
  - 1-phase-to-earth fault (I"k1)
  - Peak currents ip (3ph and 1ph-E)

Usage:  python iec60909_fault_calc.py
        python iec60909_fault_calc.py --demo     (runs with example data)
"""

import math
import sys
import pandas as pd

SQRT3 = math.sqrt(3)
SQRT2 = math.sqrt(2)
ALPHA = {'copper': 0.00393, 'cu': 0.00393,
         'aluminium': 0.00403, 'alu': 0.00403, 'aluminum': 0.00403}


# ════════════════════════════════════════════════════════════════════════
#  Voltage factors per IEC 60909-0:2016 Table 1
# ════════════════════════════════════════════════════════════════════════
def voltage_factors(Un):
    """Return (c_max, c_min) based on nominal voltage Un in Volts."""
    if Un / 1000 <= 1.0:
        return 1.05, 0.95
    return 1.10, 1.00


# ════════════════════════════════════════════════════════════════════════
#  Network element classes
# ════════════════════════════════════════════════════════════════════════
class GridSource:
    """Utility grid source defined by short-circuit power or current."""
    def __init__(self, Un, Sk_mva=None, Ik_ka=None, rx_ratio=0.1, z0_z1_ratio=1.0):
        self.Un = Un
        self.rx_ratio = rx_ratio
        self.z0_z1_ratio = z0_z1_ratio
        if Sk_mva is not None:
            self.Z_mag = (Un ** 2) / (Sk_mva * 1e6)
        elif Ik_ka is not None:
            self.Z_mag = Un / (SQRT3 * Ik_ka * 1e3)
        else:
            raise ValueError("Provide Sk_mva or Ik_ka")

    def impedance(self):
        r = self.rx_ratio
        R = self.Z_mag * r / math.sqrt(1 + r**2)
        X = self.Z_mag / math.sqrt(1 + r**2)
        return complex(R, X)

    def zero_seq_impedance(self):
        return self.impedance() * self.z0_z1_ratio


class Cable:
    """Cable with positive and zero sequence R/X, temperature-corrected."""
    def __init__(self, length_m, R1_ohm_km, X1_ohm_km, R0_ohm_km, X0_ohm_km,
                 conductor='copper', op_temp=70):
        self.length = length_m / 1000.0
        self.R1_20 = R1_ohm_km
        self.X1 = X1_ohm_km
        self.R0_20 = R0_ohm_km
        self.X0 = X0_ohm_km
        self.conductor = conductor.lower()
        self.op_temp = op_temp

    def _R_at_temp(self, R_20, temp):
        a = ALPHA.get(self.conductor, 0.00393)
        return R_20 * (1 + a * (temp - 20))

    def positive_seq(self, temp=20):
        return complex(self._R_at_temp(self.R1_20, temp) * self.length,
                       self.X1 * self.length)

    def zero_seq(self, temp=20):
        return complex(self._R_at_temp(self.R0_20, temp) * self.length,
                       self.X0 * self.length)


class Transformer:
    """Two-winding transformer with IEC 60909 impedance correction KT."""
    def __init__(self, Sr_kva, V_HV, V_LV, uk_pct, xr_ratio, vector_group='Dyn11',
                 z0_z1_ratio=1.0, pcu_w=None):
        self.Sr = Sr_kva * 1e3
        self.V_HV = V_HV
        self.V_LV = V_LV
        self.uk = uk_pct / 100.0
        self.xr = xr_ratio
        self.vg = vector_group
        self.z0_z1 = z0_z1_ratio
        self.pcu = pcu_w

    @property
    def turns_ratio(self):
        return self.V_HV / self.V_LV

    def positive_seq(self, side='LV', c_max=1.05):
        Z_base = (self.V_LV**2) / self.Sr if side == 'LV' else (self.V_HV**2) / self.Sr
        Z_mag = self.uk * Z_base
        if self.pcu and self.pcu > 0:
            V_sq = self.V_LV**2 if side == 'LV' else self.V_HV**2
            R = self.pcu * V_sq / (self.Sr**2)
            X = math.sqrt(max(Z_mag**2 - R**2, 0))
        else:
            xr = self.xr
            R = Z_mag / math.sqrt(1 + xr**2)
            X = Z_mag * xr / math.sqrt(1 + xr**2)
        Z = complex(R, X)
        # IEC 60909-0:2016 Clause 8.3.2 — transformer impedance correction KT
        KT = 0.95 * c_max / (1 + 0.6 * self.uk)
        return KT * Z

    def zero_seq(self, side='LV', c_max=1.05):
        return self.positive_seq(side, c_max) * self.z0_z1

    def hv_is_delta(self):
        return self.vg.upper()[0] == 'D'

    def lv_has_neutral(self):
        vg = ''.join(c for c in self.vg.upper() if not c.isdigit())
        return 'N' in vg[1:]


class Motor:
    """Induction motor contribution to fault current."""
    def __init__(self, P_kw, V_rated, cos_phi, xdp_pct, xr_ratio=10):
        self.P = P_kw * 1e3
        self.V = V_rated
        self.cos_phi = cos_phi
        self.xdp = xdp_pct / 100.0
        self.xr = xr_ratio

    def impedance(self):
        S = self.P / self.cos_phi
        Z_base = (self.V**2) / S
        Z_mag = self.xdp * Z_base
        R = Z_mag / math.sqrt(1 + self.xr**2)
        X = Z_mag * self.xr / math.sqrt(1 + self.xr**2)
        return complex(R, X)


# ════════════════════════════════════════════════════════════════════════
#  Network model & fault calculation
# ════════════════════════════════════════════════════════════════════════
class FaultNetwork:
    """Radial network: Grid → HV Cable → Transformer → LV Cable → [Motor] → Fault."""
    def __init__(self, grid, cable_hv, transformer, cable_lv, motor=None):
        self.grid = grid
        self.cable_hv = cable_hv
        self.tx = transformer
        self.cable_lv = cable_lv
        self.motor = motor

    def sequence_impedances(self, condition='max', temp_min=70):
        is_max = condition.lower().startswith('max')
        temp = 20 if is_max else temp_min
        n = self.tx.turns_ratio
        Un_LV = self.tx.V_LV
        c_max_v, c_min_v = voltage_factors(Un_LV)
        c = c_max_v if is_max else c_min_v

        # Positive sequence (referred to LV)
        Zg = self.grid.impedance() / n**2
        Zc1 = self.cable_hv.positive_seq(temp) / n**2
        Zt = self.tx.positive_seq('LV', c_max_v)
        Zc2 = self.cable_lv.positive_seq(temp)
        Z1 = Zg + Zc1 + Zt + Zc2
        Z2 = Z1  # static equipment: Z1 = Z2

        # Zero sequence
        Zg0 = self.grid.zero_seq_impedance() / n**2
        Zc10 = self.cable_hv.zero_seq(temp) / n**2
        Zt0 = self.tx.zero_seq('LV', c_max_v)
        Zc20 = self.cable_lv.zero_seq(temp)

        if self.tx.hv_is_delta():
            # Delta blocks upstream zero-sequence; only TX + LV cable contribute
            Z0 = Zt0 + Zc20
        else:
            Z0 = Zg0 + Zc10 + Zt0 + Zc20

        Zm = self.motor.impedance() if self.motor else None

        return {
            'Z1': Z1, 'Z2': Z2, 'Z0': Z0, 'Z_motor': Zm,
            'c': c, 'Un': Un_LV, 'temp': temp,
            'Z_grid_1': Zg, 'Z_cable1_1': Zc1, 'Z_tx_1': Zt, 'Z_cable2_1': Zc2,
            'Z_grid_0': Zg0, 'Z_cable1_0': Zc10, 'Z_tx_0': Zt0, 'Z_cable2_0': Zc20,
        }


def kappa_factor(Z):
    """IEC 60909 peak factor κ from R/X ratio of total impedance."""
    if Z.imag == 0:
        rx = 0
    else:
        rx = Z.real / Z.imag
    return 1.02 + 0.98 * math.exp(-3 * rx), rx


def calculate_faults(network, temp_min=70):
    """Calculate all fault types for max and min conditions per IEC 60909-0:2016."""
    rows = []
    for cond in ['max', 'min']:
        label = 'Maximum' if cond == 'max' else 'Minimum'
        Z = network.sequence_impedances(cond, temp_min)
        c, Un = Z['c'], Z['Un']
        Z1, Z0, Zm = Z['Z1'], Z['Z0'], Z['Z_motor']

        # 3-phase bolted fault: I"k3 = c·Un / (√3 · |Z1|)
        Ik3 = c * Un / (SQRT3 * abs(Z1))
        Ik3_motor = c * Un / (SQRT3 * abs(Zm)) if Zm else 0
        Ik3_total = Ik3 + Ik3_motor

        # 2-phase (line-to-line) fault: I"k2 = c·Un / (2·|Z1|) [since Z1 = Z2]
        Ik2 = c * Un / (2 * abs(Z1))
        Ik2_motor = c * Un / (2 * abs(Zm)) if Zm else 0
        Ik2_total = Ik2 + Ik2_motor

        # 1-phase-to-earth fault: I"k1 = √3·c·Un / |2Z1 + Z0|
        Ik1 = SQRT3 * c * Un / abs(2 * Z1 + Z0)

        # Peak short-circuit current: ip = κ·√2·I"k
        kap3, rx3 = kappa_factor(Z1)
        ip3 = kap3 * SQRT2 * Ik3_total
        Z_loop_1ph = (2 * Z1 + Z0) / 3
        kap1, rx1 = kappa_factor(Z_loop_1ph)
        ip1 = kap1 * SQRT2 * Ik1

        rows.append({
            'Condition': label,
            'c factor': c,
            'Temp (°C)': Z['temp'],
            'I"k3 (3ph) kA': round(Ik3_total / 1e3, 3),
            'I"k2 (2ph) kA': round(Ik2_total / 1e3, 3),
            'I"k1 (1ph-E) kA': round(Ik1 / 1e3, 3),
            'ip3 (peak 3ph) kA': round(ip3 / 1e3, 3),
            'ip1 (peak 1ph) kA': round(ip1 / 1e3, 3),
            'κ3': round(kap3, 3),
            'R/X (3ph)': round(rx3, 4),
            'Z1 (Ω)': f"{Z1.real:.5f} + j{Z1.imag:.5f}",
            'Z0 (Ω)': f"{Z0.real:.5f} + j{Z0.imag:.5f}",
            'Motor contrib kA': round(Ik3_motor / 1e3, 3) if Zm else 0,
        })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════
#  Interactive input
# ════════════════════════════════════════════════════════════════════════
class InputHelper:
    def __init__(self, mock_values=None):
        self.mock = mock_values
        self.idx = 0

    def get(self, label, default=None, cast=str):
        if self.mock is not None:
            val = str(self.mock[self.idx]) if self.idx < len(self.mock) else str(default)
            self.idx += 1
            return cast(val)
        d = f" [{default}]" if default is not None else ""
        raw = input(f"{label}{d}: ").strip()
        if raw == '' and default is not None:
            return cast(default)
        return cast(raw)

    def get_float(self, label, default=None):
        return self.get(label, default, float)


def build_network(ih):
    print("\n" + "=" * 70)
    print("  IEC 60909-0:2016 FAULT CALCULATION — NETWORK DATA INPUT")
    print("=" * 70)

    print("\n── 1. GRID / UTILITY SOURCE ──")
    Un_HV = ih.get_float("Grid nominal voltage (kV)", 11.0) * 1e3
    use_sk = ih.get("Enter fault level as (1) Sk MVA  (2) Ik kA", "1", int)
    if use_sk == 1:
        Sk = ih.get_float("Short-circuit power Sk'' (MVA)", 250.0)
        Ik = None
    else:
        Ik = ih.get_float("Short-circuit current Ik'' (kA)", 13.0)
        Sk = None
    rx_g = ih.get_float("Grid R/X ratio", 0.1)
    z0z1_g = ih.get_float("Grid Z0/Z1 ratio", 1.0)
    grid = GridSource(Un_HV, Sk_mva=Sk, Ik_ka=Ik, rx_ratio=rx_g, z0_z1_ratio=z0z1_g)

    print("\n── 2. HV CABLE (grid to transformer) ──")
    L1 = ih.get_float("HV cable length (m)", 500)
    R1 = ih.get_float("R1 at 20°C (Ω/km)", 0.32)
    X1 = ih.get_float("X1 (Ω/km)", 0.08)
    R0c1 = ih.get_float("R0 at 20°C (Ω/km)", 1.28)
    X0c1 = ih.get_float("X0 (Ω/km)", 0.24)
    mat1 = ih.get("Conductor (copper/aluminium)", "copper")
    temp1 = ih.get_float("Operating temperature (°C)", 70)
    cable_hv = Cable(L1, R1, X1, R0c1, X0c1, mat1, temp1)

    print("\n── 3. TRANSFORMER ──")
    Sr = ih.get_float("Transformer rating (kVA)", 1000)
    V_HV = ih.get_float("HV voltage (V)", 11000)
    V_LV = ih.get_float("LV voltage (V)", 400)
    uk = ih.get_float("Impedance voltage uk (%)", 5.0)
    xr_tx = ih.get_float("X/R ratio (0 = use copper losses)", 10.0)
    vg = ih.get("Vector group (e.g. Dyn11)", "Dyn11")
    z0z1_tx = ih.get_float("Transformer Z0/Z1 ratio", 0.95)
    pcu = ih.get_float("Copper losses Pcu (W, 0 if using X/R)", 0)
    if pcu > 0:
        tx = Transformer(Sr, V_HV, V_LV, uk, 0, vg, z0z1_tx, pcu)
    else:
        tx = Transformer(Sr, V_HV, V_LV, uk, xr_tx, vg, z0z1_tx)

    print("\n── 4. LV CABLE (transformer to load) ──")
    L2 = ih.get_float("LV cable length (m)", 50)
    R1lv = ih.get_float("R1 at 20°C (Ω/km)", 0.087)
    X1lv = ih.get_float("X1 (Ω/km)", 0.083)
    R0lv = ih.get_float("R0 at 20°C (Ω/km)", 0.349)
    X0lv = ih.get_float("X0 (Ω/km)", 0.249)
    mat2 = ih.get("Conductor (copper/aluminium)", "copper")
    temp2 = ih.get_float("Operating temperature (°C)", 70)
    cable_lv = Cable(L2, R1lv, X1lv, R0lv, X0lv, mat2, temp2)

    print("\n── 5. MOTOR / LOAD (optional) ──")
    motor_kw = ih.get_float("Motor rated power (kW, 0 for none)", 0)
    motor = None
    if motor_kw > 0:
        cosphi = ih.get_float("Motor cos φ", 0.85)
        xdp = ih.get_float("Motor xd'' (%)", 17.0)
        xr_m = ih.get_float("Motor X/R ratio", 10.0)
        motor = Motor(motor_kw, V_LV, cosphi, xdp, xr_m)

    temp_min = ih.get_float("Min fault cable temperature (°C)", 70)
    return FaultNetwork(grid, cable_hv, tx, cable_lv, motor), temp_min


# ════════════════════════════════════════════════════════════════════════
#  Report
# ════════════════════════════════════════════════════════════════════════
def print_report(network, df, temp_min):
    tx, grid = network.tx, network.grid
    print("\n" + "=" * 70)
    print("  IEC 60909-0:2016 SHORT-CIRCUIT CALCULATION REPORT")
    print("=" * 70)
    print(f"\nNetwork: Grid ({grid.Un/1e3:.1f} kV) → HV Cable → Transformer "
          f"({tx.Sr/1e3:.0f} kVA, {tx.V_HV/1e3:.1f}/{tx.V_LV:.0f} V, {tx.vg}) "
          f"→ LV Cable → Load")
    print(f"Transformer: uk = {tx.uk*100:.1f}%, Z0/Z1 = {tx.z0_z1:.2f}")
    print(f"HV delta blocks upstream zero-sequence: {tx.hv_is_delta()}")

    Z = network.sequence_impedances('max')
    print(f"\nPositive-sequence impedance at fault point (max): "
          f"{Z['Z1'].real:.5f} + j{Z['Z1'].imag:.5f} Ω, |Z1| = {abs(Z['Z1']):.5f} Ω")
    print(f"Zero-sequence impedance at fault point (max): "
          f"{Z['Z0'].real:.5f} + j{Z['Z0'].imag:.5f} Ω, |Z0| = {abs(Z['Z0']):.5f} Ω")

    print(f"\nImpedance breakdown (max, referred to {tx.V_LV:.0f} V side):")
    print(f"  Grid source:      {Z['Z_grid_1'].real:.6f} + j{Z['Z_grid_1'].imag:.6f} Ω")
    print(f"  HV cable:         {Z['Z_cable1_1'].real:.6f} + j{Z['Z_cable1_1'].imag:.6f} Ω")
    print(f"  Transformer (KT): {Z['Z_tx_1'].real:.6f} + j{Z['Z_tx_1'].imag:.6f} Ω")
    print(f"  LV cable:         {Z['Z_cable2_1'].real:.6f} + j{Z['Z_cable2_1'].imag:.6f} Ω")
    print(f"  Total Z1:         {Z['Z1'].real:.6f} + j{Z['Z1'].imag:.6f} Ω")

    if tx.hv_is_delta():
        print(f"\n  Zero-seq path (delta blocks upstream):")
        print(f"  Transformer Z0:   {Z['Z_tx_0'].real:.6f} + j{Z['Z_tx_0'].imag:.6f} Ω")
        print(f"  LV cable Z0:      {Z['Z_cable2_0'].real:.6f} + j{Z['Z_cable2_0'].imag:.6f} Ω")
        print(f"  Total Z0:         {Z['Z0'].real:.6f} + j{Z['Z0'].imag:.6f} Ω")

    print("\n" + "-" * 70)
    print("  FAULT CURRENT RESULTS (IEC 60909-0:2016)")
    print("-" * 70)
    print(df.to_string(index=False))
    print("=" * 70)


# ════════════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    demo = '--demo' in sys.argv

    if demo:
        print("Running with demo data (11 kV / 400 V network)...\n")
        mock = [
            11.0, 1, 250.0, 0.1, 1.0,          # grid
            500, 0.32, 0.08, 1.28, 0.24, "copper", 70,  # HV cable
            1000, 11000, 400, 5.0, 10.0, "Dyn11", 0.95, 0,  # transformer
            50, 0.087, 0.083, 0.349, 0.249, "copper", 70,  # LV cable
            0, 70  # motor + temp_min
        ]
        ih = InputHelper(mock_values=mock)
    else:
        ih = InputHelper()

    network, temp_min = build_network(ih)
    df = calculate_faults(network, temp_min)
    print_report(network, df, temp_min)

    df.to_csv('iec60909_fault_results.csv', index=False)
    print(f"\nResults saved to iec60909_fault_results.csv")
