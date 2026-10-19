"""A stand-in for the one librosa function Parakeet needs (librosa.filters.mel, Slaney scale and norm).
The packaged app ships this instead of librosa, which would pull in ~240 MB (numba, llvmlite, scipy, sklearn)."""

import sys
import types

import numpy as np


def _hz_to_mel(f):
    f = np.asanyarray(f, dtype=np.float64)
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, np.log(6.4) / 27.0
    mels = f / f_sp
    return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, min_log_hz) / min_log_hz) / logstep, mels)


def _mel_to_hz(m):
    m = np.asanyarray(m, dtype=np.float64)
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, np.log(6.4) / 27.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)


def mel(*, sr, n_fft, n_mels=128, fmin=0.0, fmax=None, htk=False, norm="slaney", dtype=np.float32):
    if htk or norm != "slaney":
        raise NotImplementedError("only the Slaney mel filterbank is provided")
    fmax = sr / 2 if fmax is None else fmax
    fft_freqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fft_freqs)
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_f[2 : n_mels + 2] - mel_f[:n_mels]))[:, None]
    return weights.astype(dtype)


def install():
    """Register as `librosa` if the real one isn't available."""
    try:
        import librosa  # noqa: F401
        return
    except ImportError:
        pass
    mod = types.ModuleType("librosa")
    mod.filters = types.SimpleNamespace(mel=mel)
    sys.modules["librosa"] = mod
