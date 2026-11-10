"""TTSバックエンド。すべて 24kHz / 16bit / mono の WAV bytes を返す。

- voicevox: VOICEVOX ENGINE (HTTP)。GitHub Actions では service コンテナで起動。
- windows:  Windows標準の OneCore 日本語音声（Haruka / Ichiro / Ayumi）。ローカル検証用。
            SSML で文ごとに高さ・速さ・間を変えて抑揚をつける。
- silent:   テスト用。文字数に比例した長さの無音。
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .prosody import build_ssml, voicevox_params, voicevox_style

SAMPLE_RATE = 24000


@dataclass
class TTSItem:
    speaker: str  # "haru" / "sora"
    text: str  # 読み上げ用テキスト
    emotion: str = "neutral"
    seed: int = 0


class TTS(Protocol):
    name: str

    def synthesize_many(self, items: list[TTSItem]) -> list[bytes]: ...


class TTSError(RuntimeError):
    pass


def make_wav(frames: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def to_standard_wav(wav_bytes: bytes) -> bytes:
    """任意の 16bit PCM WAV を 24kHz mono に揃える（OneCoreは16kHzで出てくる）。"""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise TTSError(f"16bit以外のWAVは未対応: {width * 8}bit")
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SAMPLE_RATE and len(x):
        n_out = int(round(len(x) * SAMPLE_RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return make_wav(np.clip(x, -32768, 32767).astype("<i2").tobytes())


@dataclass
class SilentTTS:
    seconds_per_char: float = 0.11
    name: str = "silent"

    def synthesize_many(self, items: list[TTSItem]) -> list[bytes]:
        out = []
        for it in items:
            n = int(SAMPLE_RATE * max(0.3, len(it.text) * self.seconds_per_char))
            out.append(make_wav(b"\x00\x00" * n))
        return out


@dataclass
class VoicevoxTTS:
    speakers: dict  # characters.json の speakers
    base_url: str = "http://localhost:50021"
    name: str = "voicevox"
    session: object = None
    log: object = print
    _bad_styles: set = field(default_factory=set)

    def _s(self):
        if self.session is None:
            import requests

            self.session = requests.Session()
        return self.session

    def _synth(self, text: str, style: int, params: dict) -> bytes:
        s = self._s()
        q = s.post(f"{self.base_url}/audio_query", params={"text": text, "speaker": style}, timeout=60)
        q.raise_for_status()
        query = q.json()
        query.update(params, outputSamplingRate=SAMPLE_RATE, outputStereo=False, prePhonemeLength=0.08, postPhonemeLength=0.08)
        r = s.post(f"{self.base_url}/synthesis", params={"speaker": style}, json=query, timeout=120)
        r.raise_for_status()
        return r.content

    def synthesize_many(self, items: list[TTSItem]) -> list[bytes]:
        out = []
        for it in items:
            cfg = self.speakers[it.speaker]["voicevox"]
            params = voicevox_params(cfg, it.emotion)
            style = voicevox_style(cfg, it.emotion)
            if style in self._bad_styles:
                style = cfg["speaker"]
            try:
                wav = self._synth(it.text, style, params)
            except Exception as e:  # noqa: BLE001 - 感情スタイルが無いエンジンでも基本スタイルで続行
                if style == cfg["speaker"]:
                    raise
                self.log(f"[tts] style {style} failed ({e}) → base {cfg['speaker']}")
                self._bad_styles.add(style)
                wav = self._synth(it.text, cfg["speaker"], params)
            out.append(to_standard_wav(wav))
        return out

    def is_alive(self) -> bool:
        try:
            return self._s().get(f"{self.base_url}/version", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001
            return False


# Windows PowerShell 5.1 で WinRT の OneCore 音声合成を呼ぶ。
# 指定した声が無ければ黙って別の声にせず、終了コード2で失敗させる。
WINDOWS_PS = r"""
param([string]$jobPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$jobs = Get-Content -Raw -Encoding UTF8 $jobPath | ConvertFrom-Json
$all = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
foreach ($j in $jobs) {
  $v = $all | Where-Object { $_.DisplayName -eq $j.voice } | Select-Object -First 1
  if (-not $v) { [Console]::Error.WriteLine("VOICE_NOT_FOUND: " + $j.voice); exit 2 }
  $synth.Voice = $v
  $st = Await ($synth.SynthesizeSsmlToStreamAsync($j.ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
  $in = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($st.GetInputStreamAt(0))
  $fs = [System.IO.File]::Create($j.out); $in.CopyTo($fs); $fs.Close(); $st.Dispose()
}
"""


@dataclass
class WindowsTTS:
    speakers: dict
    name: str = "windows"

    def synthesize_many(self, items: list[TTSItem]) -> list[bytes]:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            jobs = []
            for i, it in enumerate(items):
                cfg = self.speakers[it.speaker]["windows"]
                ssml = build_ssml(it.text, cfg["voice"], it.emotion, cfg.get("pitch_pct", 0), cfg.get("rate", 1.0), seed=it.seed)
                jobs.append({"voice": cfg["voice"], "ssml": ssml, "out": str(dp / f"{i:04d}.wav")})
            (dp / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
            # PowerShell 5.1 は BOM 無しの .ps1 を Shift-JIS として読むので BOM 付きで書く
            (dp / "tts.ps1").write_text(WINDOWS_PS, encoding="utf-8-sig")
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(dp / "tts.ps1"), str(dp / "jobs.json")],
                capture_output=True,
                timeout=60 + 20 * len(items),
            )
            if res.returncode != 0:
                err = res.stderr.decode("utf-8", "replace") or res.stderr.decode("cp932", "replace")
                raise TTSError(f"Windows TTS failed (code {res.returncode}): {err.strip()[:300]}")
            return [to_standard_wav(Path(j["out"]).read_bytes()) for j in jobs]


@dataclass
class CoeiroinkTTS:
    """COEIROINK v2 (http://127.0.0.1:50032)。/v1/synthesis に直接テキストを渡す方式。

    characters.json の speakers.<id>.coeiroink に speaker_uuid / style_id を設定する。
    設定は `python -m tools.coeiroink_speakers` で一覧・自動記入できる。
    """

    speakers: dict
    base_url: str = "http://127.0.0.1:50032"
    name: str = "coeiroink"
    session: object = None
    log: object = print
    _bad_styles: set = field(default_factory=set)

    def _s(self):
        if self.session is None:
            import requests

            self.session = requests.Session()
        return self.session

    def _cfg(self, speaker: str) -> dict:
        cfg = self.speakers[speaker].get("coeiroink") or {}
        if not cfg.get("speaker_uuid"):
            raise TTSError(
                f"{self.speakers[speaker]['name']} の COEIROINK の声が未設定です。"
                f" `python -m tools.coeiroink_speakers --set {speaker}=キャラ名:スタイル名` で設定してください"
            )
        return cfg

    def _synth(self, text: str, uuid: str, style: int, params: dict) -> bytes:
        body = {
            "speakerUuid": uuid,
            "styleId": style,
            "text": text,
            "prosodyDetail": [],
            "prePhonemeLength": 0.08,
            "postPhonemeLength": 0.08,
            "outputSamplingRate": SAMPLE_RATE,
            **params,
        }
        r = self._s().post(f"{self.base_url}/v1/synthesis", json=body, timeout=120)
        r.raise_for_status()
        return r.content

    def synthesize_many(self, items: list[TTSItem]) -> list[bytes]:
        for sp in {it.speaker for it in items}:
            self._cfg(sp)  # 未設定なら合成を始める前に止める
        out = []
        for it in items:
            cfg = self._cfg(it.speaker)
            base = {"speed": cfg.get("speed", 1.1), "pitch": cfg.get("pitch", 0.0), "intonation": cfg.get("intonation", 1.2),
                    "volume": cfg.get("volume", 1.0)}
            params = voicevox_params(base, it.emotion)  # 速さ・高さ・抑揚のパラメータ体系はVOICEVOXと同じ
            base_style = int(cfg.get("style_id", 0))
            style = int(cfg.get("styles", {}).get(it.emotion, base_style))
            if (cfg["speaker_uuid"], style) in self._bad_styles:
                style = base_style
            try:
                wav = self._synth(it.text, cfg["speaker_uuid"], style, params)
            except Exception as e:  # noqa: BLE001 - 感情スタイル未ダウンロードでも基本スタイルで続行
                if style == base_style:
                    raise
                self.log(f"[tts] coeiroink style {style} failed ({e}) → base {base_style}")
                self._bad_styles.add((cfg["speaker_uuid"], style))
                wav = self._synth(it.text, cfg["speaker_uuid"], base_style, params)
            out.append(to_standard_wav(wav))
        return out

    def list_speakers(self) -> list[dict]:
        r = self._s().get(f"{self.base_url}/v1/speakers", timeout=10)
        r.raise_for_status()
        return [
            {"speakerName": s["speakerName"], "speakerUuid": s["speakerUuid"],
             "styles": [{"styleName": st["styleName"], "styleId": st["styleId"]} for st in s.get("styles", [])]}
            for s in r.json()
        ]

    def is_alive(self) -> bool:
        try:
            return self._s().get(f"{self.base_url}/v1/speakers", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001
            return False
