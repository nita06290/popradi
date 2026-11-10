"""音声結合: セリフWAVを間（ポーズ）とジングルを挟んで1本にし、各セリフの開始/終了秒を返す。

字幕同期はこのタイミングをそのまま使う（音声認識は不要）。
"""

from __future__ import annotations

import array
import io
import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .tts import SAMPLE_RATE, make_wav


@dataclass
class Segment:
    kind: str  # "line" | "jingle" | "chime"
    wav: bytes
    gap_after: float = 0.0
    ref: int | None = None  # line index


def read_frames(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError(
                f"unexpected wav format ch={w.getnchannels()} width={w.getsampwidth()} rate={w.getframerate()}"
            )
        return w.readframes(w.getnframes())


def clean_voice(wav_bytes: bytes, target_rms_db: float = -20.0, peak_db: float = -1.5, gate_db: float = -38.0) -> bytes:
    """セリフ1本の音声を整える（ノイズ対策と、2人の音量差の解消）。

    1. 直流成分の除去 + 70Hz ハイパス（低いうなり・ボコボコ音を消す）
    2. ノイズゲート: 最大音量から gate_db 未満の区間をなめらかに無音化（サーッという残響ノイズ）
    3. 前後の無音をカットし、12msのフェードイン/アウト（つなぎ目のプチッ音を消す）
    4. 発話部分の音量を target_rms_db に揃え、ピークは peak_db で抑える
    """
    import numpy as np

    x = np.frombuffer(read_frames(wav_bytes), dtype="<i2").astype(np.float64)
    if len(x) == 0 or np.abs(x).max() < 1:
        return wav_bytes
    x -= x.mean()
    # 1次ハイパス (70Hz)
    a = math.exp(-2 * math.pi * 70 / SAMPLE_RATE)
    y = np.empty_like(x)
    prev_x = prev_y = 0.0
    for i in range(len(x)):  # 1文 数秒なのでPythonループで十分
        prev_y = a * (prev_y + x[i] - prev_x)
        prev_x = x[i]
        y[i] = prev_y
    x = y
    # 10ms ごとの音量でゲート
    hop = SAMPLE_RATE // 100
    n = len(x) // hop
    if n == 0:
        return wav_bytes
    env = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
    thr = env.max() * 10 ** (gate_db / 20)
    open_ = env > thr
    # 開閉を前後30msずつ広げて子音の頭を切らない
    k = 3
    widened = np.convolve(open_.astype(float), np.ones(2 * k + 1), "same") > 0
    gain = np.repeat(np.convolve(widened.astype(float), np.ones(5) / 5, "same"), hop)
    gain = np.concatenate([gain, np.full(len(x) - len(gain), gain[-1] if len(gain) else 0)])
    x *= gain
    # 前後トリム
    voiced = np.flatnonzero(widened)
    if len(voiced) == 0:
        return wav_bytes
    start, end = voiced[0] * hop, min(len(x), (voiced[-1] + 1) * hop)
    x = x[start:end]
    # フェード
    f = min(int(SAMPLE_RATE * 0.012), len(x) // 2)
    if f > 0:
        ramp = np.linspace(0, 1, f)
        x[:f] *= ramp
        x[-f:] *= ramp[::-1]
    # 音量を揃える
    env_v = env[open_]
    rms = float(np.sqrt((env_v**2).mean())) if len(env_v) else float(np.sqrt((x**2).mean()))
    if rms > 0:
        x *= (32767 * 10 ** (target_rms_db / 20)) / rms
    peak = np.abs(x).max()
    limit = 32767 * 10 ** (peak_db / 20)
    if peak > limit:
        x *= limit / peak
    return make_wav(np.clip(x, -32768, 32767).astype("<i2").tobytes())


def tone(notes: list[tuple[float, float]], volume: float = 0.22) -> bytes:
    """(周波数Hz, 秒) の列から、減衰つきのピコピコ音を作る。依存ライブラリなし。"""
    samples = array.array("h")
    for freq, dur in notes:
        n = int(SAMPLE_RATE * dur)
        for i in range(n):
            t = i / SAMPLE_RATE
            env = min(1.0, i / 240) * math.exp(-3.2 * t / max(dur, 1e-3))
            v = math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(4 * math.pi * freq * t)
            samples.append(int(32767 * volume * env * v / 1.35) if freq > 0 else 0)
    return make_wav(samples.tobytes())


def opening_jingle() -> bytes:
    # C-E-G-C の上昇アルペジオ + キラッ
    return tone([(523.25, 0.14), (659.25, 0.14), (783.99, 0.14), (1046.5, 0.32), (0, 0.05), (1568.0, 0.25)])


def chapter_chime() -> bytes:
    return tone([(880.0, 0.12), (1318.5, 0.22)], volume=0.16)


def ending_jingle() -> bytes:
    return tone([(1046.5, 0.14), (783.99, 0.14), (659.25, 0.14), (523.25, 0.4)])


def concat(segments: list[Segment]) -> tuple[bytes, dict[int, tuple[float, float]], float]:
    """(wav bytes, {line_index: (start, end)}, duration秒)"""
    out = bytearray()
    cues: dict[int, tuple[float, float]] = {}
    for seg in segments:
        frames = read_frames(seg.wav)
        start = len(out) / 2 / SAMPLE_RATE
        out += frames
        end = len(out) / 2 / SAMPLE_RATE
        if seg.kind == "line" and seg.ref is not None:
            cues[seg.ref] = (round(start, 3), round(end, 3))
        out += b"\x00\x00" * int(SAMPLE_RATE * seg.gap_after)
    duration = len(out) / 2 / SAMPLE_RATE
    return make_wav(bytes(out)), cues, round(duration, 3)


def encode(wav_bytes: bytes, out_base: Path) -> Path:
    """ffmpeg があれば MP3(64kbps mono, ラウドネス正規化)、無ければ WAV のまま保存。"""
    out_base.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        wav_path = out_base.with_suffix(".tmp.wav")
        wav_path.write_bytes(wav_bytes)
        mp3 = out_base.with_suffix(".mp3")
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ac", "1", "-ar", "24000", "-b:a", "64k", str(mp3)],
            check=True,
        )
        wav_path.unlink()
        return mp3
    p = out_base.with_suffix(".wav")
    p.write_bytes(wav_bytes)
    return p


# ---------------------------------------------------------------- BGM（自作のループ。外部素材なしなので権利の心配がない）

def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


# スタイルごとの設定。chords は MIDI ノート番号、bpm はテンポ、beats_per_chord は1コードの拍数
BGM_STYLES = {
    "lofi": {  # Fmaj7 → Em7 → Dm7 → Cmaj7。こもった音のゆったりループ（従来）
        "label": "ローファイ（落ち着き）", "bpm": 84, "beats_per_chord": 8,
        "chords": [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]],
        "pad": 0.10, "bass": 0.16, "arp": 0.07, "arp_decay": 4.5, "arp_pattern": [0, 1, 2, 3, 2, 1, 2, 3],
        "drums": 0.0, "lowpass": 6,
    },
    "pop": {  # C → G → Am → F。明るいマリンバ風 + 軽いキック・ハイハット
        "label": "ポップ（明るい）", "bpm": 112, "beats_per_chord": 4,
        "chords": [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]],
        "pad": 0.05, "bass": 0.20, "arp": 0.10, "arp_decay": 9.0, "arp_pattern": [0, 2, 1, 3, 2, 1, 3, 2],
        "drums": 1.0, "lowpass": 3,
    },
    "acoustic": {  # Cadd9 → Am7 → Fmaj7 → G。ピアノ風のポロン、すき間多め
        "label": "アコースティック（やさしい）", "bpm": 72, "beats_per_chord": 8,
        "chords": [[48, 55, 62, 64], [45, 55, 60, 64], [41, 57, 60, 64], [43, 55, 59, 62]],
        "pad": 0.03, "bass": 0.12, "arp": 0.11, "arp_decay": 2.6, "arp_pattern": [0, 2, 3, 1, 3, 2, -1, 3],
        "drums": 0.0, "lowpass": 4,
    },
}


def make_bgm_loop(style: str = "lofi"):
    """スタイルに応じたループを合成する。float32 [-1, 1] の配列を返す。"""
    import numpy as np

    cfg = BGM_STYLES.get(style, BGM_STYLES["lofi"])
    sr = SAMPLE_RATE
    beat = 60 / cfg["bpm"]
    chord_sec = beat * cfg["beats_per_chord"]
    n_chord = int(sr * chord_sec)
    t = np.arange(n_chord) / sr
    rng = np.random.default_rng(7)
    out = []
    for chord in cfg["chords"]:
        seg = np.zeros(n_chord)
        # パッド: 少しデチューンした正弦波を重ね、ゆっくり立ち上げて消す
        env = np.minimum(1, t / 1.0) * np.minimum(1, (chord_sec - t) / 1.0)
        for note in chord:
            f = _midi(note)
            seg += cfg["pad"] * env * (np.sin(2 * np.pi * f * t) + 0.6 * np.sin(2 * np.pi * f * 1.003 * t) + 0.15 * np.sin(4 * np.pi * f * t))
        # ベース: 拍の頭で鳴らす
        fb = _midi(chord[0] - 12)
        for b in range(cfg["beats_per_chord"]):
            s0 = int(b * beat * sr)
            n = min(int(beat * sr), n_chord - s0)
            tt = np.arange(n) / sr
            seg[s0:s0 + n] += cfg["bass"] * np.exp(-2.2 * tt) * np.minimum(1, tt / 0.01) * np.sin(2 * np.pi * fb * tt)
        # アルペジオ: 8分音符（パターンの -1 は休符）
        step = beat / 2
        pat = cfg["arp_pattern"]
        for k in range(int(round(chord_sec / step))):
            idx = pat[k % len(pat)]
            if idx < 0:
                continue
            s0 = int(k * step * sr)
            n = min(int(sr * 1.2), n_chord - s0)
            if n <= 0:
                break
            tt = np.arange(n) / sr
            f = _midi(chord[idx] + 12)
            tone_ = np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(6 * np.pi * f * tt) + 0.12 * np.sin(8 * np.pi * f * tt)
            seg[s0:s0 + n] += cfg["arp"] * np.exp(-cfg["arp_decay"] * tt) * np.minimum(1, tt / 0.004) * tone_
        # ドラム: 1・3拍目にキック、裏拍にハイハット（とても控えめ）
        if cfg["drums"]:
            for b in range(cfg["beats_per_chord"]):
                s0 = int(b * beat * sr)
                if b % 2 == 0:
                    n = min(int(sr * 0.25), n_chord - s0)
                    tt = np.arange(n) / sr
                    freq = 50 + 70 * np.exp(-30 * tt)
                    seg[s0:s0 + n] += 0.35 * cfg["drums"] * np.exp(-14 * tt) * np.sin(2 * np.pi * np.cumsum(freq) / sr)
                h0 = int((b + 0.5) * beat * sr)
                n = min(int(sr * 0.05), n_chord - h0)
                if n > 0:
                    noise = rng.standard_normal(n)
                    noise = noise - np.convolve(noise, np.ones(4) / 4, "same")  # 高い成分だけ残す
                    seg[h0:h0 + n] += 0.05 * cfg["drums"] * np.exp(-90 * np.arange(n) / sr) * noise
        out.append(seg)
    loop = np.concatenate(out)
    # 軽いローパス（声とぶつからないように）
    k = cfg["lowpass"]
    if k > 1:
        loop = np.convolve(loop, np.ones(k) / k, "same")
    return (loop / max(1e-9, np.abs(loop).max())).astype(np.float32)


def add_bgm(
    wav_bytes: bytes, speech_spans: list[tuple[float, float]], start: float, level_db: float = -26.0, duck_db: float = -12.0, style: str = "lofi"
) -> bytes:
    """BGMを敷く。話している区間（speech_spans）は duck_db だけ下げ、start 秒からフェードイン、最後はフェードアウト。"""
    import numpy as np

    sr = SAMPLE_RATE
    x = np.frombuffer(read_frames(wav_bytes), dtype="<i2").astype(np.float32)
    n = len(x)
    if n == 0:
        return wav_bytes
    loop = make_bgm_loop(style)
    bgm = np.tile(loop, n // len(loop) + 1)[:n]
    gain = np.full(n, 10 ** (level_db / 20), dtype=np.float32)
    duck = 10 ** (duck_db / 20)
    for s, e in speech_spans:
        gain[int(s * sr):int(e * sr)] *= duck
    # ダッキングの切り替えをなめらかに（約0.25秒）
    w = int(sr * 0.25)
    gain = np.convolve(gain, np.ones(w) / w, "same").astype(np.float32)
    # フェードイン（start から2秒）とフェードアウト（最後の3秒）
    s0 = int(start * sr)
    gain[:s0] = 0
    fi = min(int(sr * 2), n - s0)
    if fi > 0:
        gain[s0:s0 + fi] *= np.linspace(0, 1, fi)
    fo = min(int(sr * 3), n)
    gain[n - fo:] *= np.linspace(1, 0, fo)
    y = x + bgm * gain * 32767
    peak = np.abs(y).max()
    if peak > 32767 * 0.95:
        y *= 32767 * 0.95 / peak
    return make_wav(np.clip(y, -32768, 32767).astype("<i2").tobytes())
