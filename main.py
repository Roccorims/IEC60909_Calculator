# main.py — IEC 60909-0:2016 Fault Calculator (Kivy Android App)
import math
import pandas as pd
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.graphics import Color, Rectangle, Line
from kivy.core.window import Window

SQRT3 = math.sqrt(3)
SQRT2 = math.sqrt(2)
ALPHA = {'copper': 0.00393, 'aluminium': 0.00403}


def voltage_factors(Un):
    if Un / 1000 <= 1.0:
        return 1.05, 0.95
    return 1.10, 1.00


class GridSource:
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
    def __init__(self, length_m, R1, X1, R0, X0, conductor='copper', op_temp=70):
        self.length = length_m / 1000.0
        self.R1_20, self.X1 = R1, X1
        self.R0_20, self.X0 = R0, X0
        self.conductor = conductor.lower()
        self.op_temp = op_temp

    def _R_at_temp(self, R_20, temp):
        a = ALPHA.get(self.conductor, 0.00393)
        return R_20 * (1 + a * (temp - 20))

    def positive_seq(self, temp=20):
        return complex(self._R_at_temp(self.R1_20, temp) * self.length, self.X1 * self.length)

    def zero_seq(self, temp=20):
        return complex(self._R_at_temp(self.R0_20, temp) * self.length, self.X0 * self.length)


class Transformer:
    def __init__(self, Sr_kva, V_HV, V_LV, uk_pct, xr_ratio, vector_group='Dyn11',
                 z0_z1_ratio=1.0, pcu_w=None):
        self.Sr = Sr_kva * 1e3
        self.V_HV, self.V_LV = V_HV, V_LV
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
        KT = 0.95 * c_max / (1 + 0.6 * self.uk)
        return KT * Z

    def zero_seq(self, side='LV', c_max=1.05):
        return self.positive_seq(side, c_max) * self.z0_z1

    def hv_is_delta(self):
        return self.vg.upper()[0] == 'D'


class Motor:
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


class FaultNetwork:
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

        Zg = self.grid.impedance() / n**2
        Zc1 = self.cable_hv.positive_seq(temp) / n**2
        Zt = self.tx.positive_seq('LV', c_max_v)
        Zc2 = self.cable_lv.positive_seq(temp)
        Z1 = Zg + Zc1 + Zt + Zc2

        Zg0 = self.grid.zero_seq_impedance() / n**2
        Zc10 = self.cable_hv.zero_seq(temp) / n**2
        Zt0 = self.tx.zero_seq('LV', c_max_v)
        Zc20 = self.cable_lv.zero_seq(temp)

        if self.tx.hv_is_delta():
            Z0 = Zt0 + Zc20
        else:
            Z0 = Zg0 + Zc10 + Zt0 + Zc20

        Zm = self.motor.impedance() if self.motor else None
        return {'Z1': Z1, 'Z0': Z0, 'Z_motor': Zm, 'c': c, 'Un': Un_LV, 'temp': temp}


def kappa_factor(Z):
    if Z.imag == 0:
        rx = 0
    else:
        rx = Z.real / Z.imag
    return 1.02 + 0.98 * math.exp(-3 * rx), rx


def calculate_faults(network, temp_min=70):
    results = []
    for cond in ['max', 'min']:
        label = 'Maximum' if cond == 'max' else 'Minimum'
        Z = network.sequence_impedances(cond, temp_min)
        c, Un = Z['c'], Z['Un']
        Z1, Z0, Zm = Z['Z1'], Z['Z0'], Z['Z_motor']

        Ik3 = c * Un / (SQRT3 * abs(Z1))
        Ik3_motor = c * Un / (SQRT3 * abs(Zm)) if Zm else 0
        Ik3_total = Ik3 + Ik3_motor

        Ik2 = c * Un / (2 * abs(Z1))
        Ik2_motor = c * Un / (2 * abs(Zm)) if Zm else 0
        Ik2_total = Ik2 + Ik2_motor

        Ik1 = SQRT3 * c * Un / abs(2 * Z1 + Z0)

        kap3, rx3 = kappa_factor(Z1)
        ip3 = kap3 * SQRT2 * Ik3_total
        Z_loop = (2 * Z1 + Z0) / 3
        kap1, _ = kappa_factor(Z_loop)
        ip1 = kap1 * SQRT2 * Ik1

        results.append({
            'condition': label, 'c': c, 'temp': Z['temp'],
            'Ik3': Ik3_total / 1e3, 'Ik2': Ik2_total / 1e3, 'Ik1': Ik1 / 1e3,
            'ip3': ip3 / 1e3, 'ip1': ip1 / 1e3,
            'Z1': Z1, 'Z0': Z0,
            'motor_contrib': (Ik3_motor / 1e3) if Zm else 0,
        })
    return results


class StyledLabel(Label):
    pass


class FaultCalculatorApp(App):
    def build(self):
        Window.clearcolor = (0.12, 0.15, 0.2, 1)
        self.title = 'IEC 60909 Fault Calculator'
        self.inputs = {}

        root = BoxLayout(orientation='vertical', padding=10, spacing=5)

        header = Label(
            text='[b]IEC 60909-0:2016[/b]\nShort-Circuit Fault Calculator',
            markup=True, font_size='20sp', size_hint_y=0.08,
            color=(0.3, 0.7, 1, 1))
        root.add_widget(header)

        scroll = ScrollView(size_hint_y=0.82)
        form = GridLayout(cols=2, spacing=5, size_hint_y=None, padding=5)
        form.bind(minimum_height=form.setter('height'))

        def add_field(parent, key, label_text, default=''):
            parent.add_widget(Label(text=label_text, font_size='13sp',
                                    color=(0.8, 0.85, 0.9, 1), size_hint_y=None, height=40))
            ti = TextInput(text=str(default), font_size='14sp',
                          size_hint_y=None, height=40,
                          background_color=(0.15, 0.18, 0.25, 1),
                          foreground_color=(1, 1, 1, 1))
            parent.add_widget(ti)
            self.inputs[key] = ti

        add_field(form, 'Un_grid', 'Grid Voltage (kV)', '11')
        add_field(form, 'Sk', 'Grid Sk" (MVA)', '250')
        add_field(form, 'rx_grid', 'Grid R/X ratio', '0.1')
        add_field(form, 'z0z1_grid', 'Grid Z0/Z1', '1.0')

        add_field(form, 'L_hv', 'HV Cable Length (m)', '500')
        add_field(form, 'R1_hv', 'HV R1 ohm/km', '0.32')
        add_field(form, 'X1_hv', 'HV X1 ohm/km', '0.08')
        add_field(form, 'R0_hv', 'HV R0 ohm/km', '1.28')
        add_field(form, 'X0_hv', 'HV X0 ohm/km', '0.24')

        add_field(form, 'Sr_tx', 'Transformer (kVA)', '1000')
        add_field(form, 'V_hv', 'TX HV (V)', '11000')
        add_field(form, 'V_lv', 'TX LV (V)', '400')
        add_field(form, 'uk', 'TX uk (%)', '5.0')
        add_field(form, 'xr_tx', 'TX X/R ratio', '10')
        add_field(form, 'z0z1_tx', 'TX Z0/Z1', '0.95')
        add_field(form, 'pcu', 'TX Pcu (W, 0=X/R)', '0')

        form.add_widget(Label(text='Vector Group', font_size='13sp',
                              color=(0.8, 0.85, 0.9, 1), size_hint_y=None, height=40))
        vg_spinner = Spinner(text='Dyn11', values=['Dyn11', 'Yyn0', 'YNd11', 'Dzn0', 'Dyn1', 'Yzn11'],
                            size_hint_y=None, height=40, font_size='14sp',
                            background_color=(0.2, 0.3, 0.5, 1))
        form.add_widget(vg_spinner)
        self.inputs['vg'] = vg_spinner

        add_field(form, 'L_lv', 'LV Cable Length (m)', '50')
        add_field(form, 'R1_lv', 'LV R1 ohm/km', '0.087')
        add_field(form, 'X1_lv', 'LV X1 ohm/km', '0.083')
        add_field(form, 'R0_lv', 'LV R0 ohm/km', '0.349')
        add_field(form, 'X0_lv', 'LV X0 ohm/km', '0.249')

        add_field(form, 'motor_kw', 'Motor (kW, 0=none)', '0')
        add_field(form, 'temp_min', 'Min Fault Temp (C)', '70')

        scroll.add_widget(form)
        root.add_widget(scroll)

        btn_layout = BoxLayout(size_hint_y=0.10, spacing=5)
        calc_btn = Button(text='CALCULATE FAULTS', font_size='16sp',
                         background_color=(0.2, 0.6, 0.3, 1), bold=True)
        calc_btn.bind(on_press=self.on_calculate)
        btn_layout.add_widget(calc_btn)
        root.add_widget(btn_layout)

        return root

    def _f(self, key):
        try:
            return float(self.inputs[key].text)
        except (ValueError, AttributeError):
            return 0.0

    def on_calculate(self, instance):
        try:
            Un = self._f('Un_grid') * 1e3
            grid = GridSource(Un, Sk_mva=self._f('Sk'), rx_ratio=self._f('rx_grid'),
                              z0_z1_ratio=self._f('z0z1_grid'))

            cable_hv = Cable(self._f('L_hv'), self._f('R1_hv'), self._f('X1_hv'),
                             self._f('R0_hv'), self._f('X0_hv'), 'copper', 70)

            pcu = self._f('pcu')
            tx = Transformer(self._f('Sr_tx'), self._f('V_hv'), self._f('V_lv'),
                             self._f('uk'), self._f('xr_tx') if pcu <= 0 else 0,
                             self.inputs['vg'].text, self._f('z0z1_tx'),
                             pcu if pcu > 0 else None)

            cable_lv = Cable(self._f('L_lv'), self._f('R1_lv'), self._f('X1_lv'),
                             self._f('R0_lv'), self._f('X0_lv'), 'copper', 70)

            motor = None
            m_kw = self._f('motor_kw')
            if m_kw > 0:
                motor = Motor(m_kw, self._f('V_lv'), 0.85, 17.0, 10.0)

            temp_min = self._f('temp_min')
            network = FaultNetwork(grid, cable_hv, tx, cable_lv, motor)
            results = calculate_faults(network, temp_min)
            self.show_results(results, tx)

        except Exception as e:
            popup = Popup(title='Error', content=Label(text=str(e)),
                         size_hint=(0.8, 0.4))
            popup.open()

    def show_results(self, results, tx):
        content = BoxLayout(orientation='vertical', padding=10, spacing=5)

        info = Label(
            text=f'Transformer: {tx.Sr/1e3:.0f} kVA, {tx.V_HV/1e3:.1f}/{tx.V_LV:.0f} V, {tx.vg}\n'
                 f'uk={tx.uk*100:.1f}%  |  HV Delta: {tx.hv_is_delta()}',
            font_size='13sp', size_hint_y=0.10, color=(0.8, 0.85, 0.9, 1))
        content.add_widget(info)

        scroll = ScrollView(size_hint_y=0.80)
        results_grid = GridLayout(cols=4, spacing=3, size_hint_y=None, padding=5)
        results_grid.bind(minimum_height=results_grid.setter('height'))

        headers = ['Fault Type', 'Maximum (kA)', 'Minimum (kA)', 'Peak Max (kA)']
        for h in headers:
            results_grid.add_widget(Label(text=f'[b]{h}[/b]', markup=True,
                                         font_size='12sp', size_hint_y=None, height=35,
                                         color=(0.3, 0.7, 1, 1)))

        fault_types = [
            ('3-Phase (I"k3)', 'Ik3', 'Ik3', 'ip3'),
            ('2-Phase (I"k2)', 'Ik2', 'Ik2', None),
            ('1-Ph-Earth (I"k1)', 'Ik1', 'Ik1', 'ip1'),
        ]
        for name, kmax, kmin, kpeak in fault_types:
            results_grid.add_widget(Label(text=name, font_size='11sp',
                                         size_hint_y=None, height=35, color=(0.9, 0.9, 0.9, 1)))
            results_grid.add_widget(Label(text=f'{results[0][kmax]:.3f}', font_size='12sp',
                                         size_hint_y=None, height=35, color=(1, 0.8, 0.4, 1)))
            results_grid.add_widget(Label(text=f'{results[1][kmin]:.3f}', font_size='12sp',
                                         size_hint_y=None, height=35, color=(0.7, 0.9, 0.7, 1)))
            peak_val = f'{results[0][kpeak]:.3f}' if kpeak and kpeak in results[0] else '—'
            results_grid.add_widget(Label(text=peak_val, font_size='12sp',
                                         size_hint_y=None, height=35, color=(1, 0.6, 0.4, 1)))

        results_grid.add_widget(Label(text='', size_hint_y=None, height=10))
        for r in results:
            results_grid.add_widget(Label(
                text=f'{r["condition"]}: Z1={r["Z1"].real:.5f}+j{r["Z1"].imag:.5f} Ohm',
                font_size='10sp', size_hint_y=None, height=30, color=(0.7, 0.75, 0.8, 1)))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))

            results_grid.add_widget(Label(
                text=f'{r["condition"]}: Z0={r["Z0"].real:.5f}+j{r["Z0"].imag:.5f} Ohm',
                font_size='10sp', size_hint_y=None, height=30, color=(0.7, 0.75, 0.8, 1)))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))
            results_grid.add_widget(Label(text='', size_hint_y=None, height=30))

        scroll.add_widget(results_grid)
        content.add_widget(scroll)

        close_btn = Button(text='CLOSE', font_size='14sp', size_hint_y=0.10,
                          background_color=(0.5, 0.2, 0.2, 1))
        content.add_widget(close_btn)

        popup = Popup(title='IEC 60909 Fault Results', content=content,
                     size_hint=(0.95, 0.85))
        close_btn.bind(on_press=popup.dismiss)
        popup.open()


if __name__ == '__main__':
    FaultCalculatorApp().run()
