"""Distingue a ressonância dos tons e do surdo do ruído da esteira da caixa.

Classifica as peças da bateria por regiões acústicas fundamentais:
- Surdo (Floor Tom): 58 a 115 Hz (Low Floor Tom: 41, High Floor Tom: 43)
- Tons (Rack Toms): 115 a 450 Hz (Low Tom: 45, Low-Mid Tom: 47, Hi-Mid Tom: 48, High Tom: 50)
- Caixa (Snare): ruído de esteira de banda larga (500 a 4500 Hz com alta planaridade espectral).
"""

import numpy as np

TOM_NOTES = frozenset({41, 43, 45, 47, 48, 50})


def classify_body(audio, rate, onset):
    """Retorna (nota de tom/surdo ou None, fração subgrave, presença de esteira).

    Ignora os primeiros 10 ms do estalo inicial e examina até 160 ms do corpo ressonante.
    Tons e surdos concentram energia harmônica pura; a caixa tem ruído espalhado.
    """
    begin = max(0, int((onset + 0.010) * rate))
    end = min(len(audio), int((onset + 0.160) * rate))
    segment = np.asarray(audio[begin:end], dtype=float)
    if len(segment) < 64 or np.max(np.abs(segment), initial=0) < 1e-7:
        return None, 0.0, False

    n_fft = 8192
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment)), n=n_fft)) ** 2
    frequencies = np.fft.rfftfreq(n_fft, 1 / rate)

    def power(lo, hi):
        return float(spectrum[(frequencies >= lo) & (frequencies < hi)].sum())

    sub = power(25, 58)           # Subgrave extremo de bumbo (< 58 Hz)
    surdo_band = power(58, 115)    # Banda acústica do surdo (58 a 115 Hz)
    toms_band = power(115, 450)    # Banda acústica dos tons (115 a 450 Hz)
    body = surdo_band + toms_band  # Corpo total de tambores (58 a 450 Hz)
    noise = power(500, 4500)       # Faixa de esteira da caixa (500 a 4500 Hz)

    total_low = sub + body + 1e-15
    sub_fraction = sub / total_low

    # A esteira da caixa é ruído branco espalhado na faixa média-alta
    noisy_band = spectrum[(frequencies >= 500) & (frequencies < 4500)]
    flatness = float(np.exp(np.mean(np.log(noisy_band + 1e-20))) / (np.mean(noisy_band) + 1e-20))
    has_snare_noise = (noise > body * 0.22 and flatness > 0.25) or (noise > body * 0.45)

    # Identifica o pico de ressonância do tambor (58 Hz a 400 Hz)
    region = (frequencies >= 58) & (frequencies <= 400)
    if not np.any(region) or body <= 1e-12:
        return None, sub_fraction, has_snare_noise

    peak = float(frequencies[np.flatnonzero(region)[np.argmax(spectrum[region])]])
    peak_power = power(max(50, peak - 20), peak + 20)
    concentration = peak_power / (body + 1e-15)

    # Rejeita se for esteira de caixa, se tiver subgrave de bumbo ou se a energia não tiver foco ressonante
    if has_snare_noise or concentration < 0.28 or sub_fraction > 0.32 or (peak < 78 and sub_fraction > 0.20):
        return None, sub_fraction, has_snare_noise

    # Mapeamento General MIDI:
    # 58 a 90 Hz: Low Floor Tom (41) - Surdo grave
    # 90 a 118 Hz: High Floor Tom (43) - Surdo agudo
    # 118 a 160 Hz: Low Tom (45) - Tom grave
    # 160 a 215 Hz: Low-Mid Tom (47) - Tom médio
    # 215 a 280 Hz: Hi-Mid Tom (48) - Tom médio-agudo
    # > 280 Hz: High Tom (50) - Tom agudo
    cutoffs = [90, 118, 160, 215, 280]
    index = int(np.searchsorted(cutoffs, peak))
    tom_pitch = (41, 43, 45, 47, 48, 50)[index]

    return tom_pitch, sub_fraction, has_snare_noise
