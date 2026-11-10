import io
import json
import sys
import wave
from datetime import date, timedelta

import pytest

from radio.audio import Segment, chapter_chime, concat, encode, opening_jingle, read_frames
from radio.episode import build_episode, build_lines, fmt_vtt_time, prune_and_index, seen_urls, to_vtt, write_episode
from radio.reading import Reader
from radio.script import template_script
from radio.tts import SAMPLE_RATE, SilentTTS, TTSItem, make_wav
from tests.conftest import NOW, ROOT


@pytest.fixture
def reader():
    return Reader(json.loads((ROOT / "config" / "reading_dict.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- reading

def test_reader_dictionary_longest_first(reader):
    assert reader("GitHub Actionsで動かす") == "ギットハブ アクションズで動かす"
    assert reader("OpenAIとAI") == "オープンエーアイとエーアイ"


def test_reader_spells_unknown_acronyms_and_removes_urls(reader):
    assert reader("NHKの記事 https://example.com/a を見て") == "エヌエイチケーの記事 リンク を見て"


def test_reader_keeps_japanese_and_removes_brackets(reader):
    assert reader("「生成AI」が話題🎉") == "生成エーアイが話題"


# ---------------------------------------------------------------- audio

def wav_seconds(sec):
    return make_wav(b"\x00\x00" * int(SAMPLE_RATE * sec))


def test_concat_timings_are_exact():
    segs = [
        Segment("jingle", wav_seconds(1.0), 0.5),
        Segment("line", wav_seconds(2.0), 0.25, ref=0),
        Segment("line", wav_seconds(1.5), 0.0, ref=1),
    ]
    wav, cues, dur = concat(segs)
    assert cues == {0: (1.5, 3.5), 1: (3.75, 5.25)}
    assert dur == 5.25
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(5.25)


def test_read_frames_rejects_wrong_format():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\x00" * 400)
    with pytest.raises(ValueError):
        read_frames(buf.getvalue())


def test_jingles_are_valid_and_not_silent():
    for j in (opening_jingle(), chapter_chime()):
        frames = read_frames(j)
        assert len(frames) > SAMPLE_RATE * 0.2
        assert max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 200)) > 1000


def test_encode_falls_back_to_wav_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("radio.audio.shutil.which", lambda _: None)
    p = encode(wav_seconds(0.5), tmp_path / "audio" / "x")
    assert p.suffix == ".wav" and p.stat().st_size > 1000


def test_silent_tts_length_scales_with_text():
    a, b = SilentTTS().synthesize_many([TTSItem("haru", "あ" * 10), TTSItem("sora", "あ" * 40)])
    assert len(read_frames(b)) > len(read_frames(a))


# ---------------------------------------------------------------- episode

def make_episode(articles, characters, reader, d=date(2026, 11, 8), audio="audio/x.wav"):
    script = template_script(articles, characters, "11月8日")
    lines = build_lines(script, reader)
    cues = {i: (1.0 + i * 2, 2.5 + i * 2) for i in range(len(lines))}
    return build_episode(
        episode_date=d, script=script, lines=lines, cues=cues, duration=3.0 + len(lines) * 2, audio_rel=audio,
        articles=articles, characters=characters, generator={"llm": "template", "tts": "silent"}, warnings=[], generated_at=NOW,
    )


def test_build_episode_structure(articles, characters, reader):
    ep = make_episode(articles, characters, reader)
    assert ep["id"] == "2026-11-08"
    assert ep["lines"][0]["start"] == 1.0
    assert all("reading" in ln for ln in ep["lines"])
    assert ep["chapters"][1]["topic"] == articles[0].topic
    assert ep["chapters"][0]["start"] == 1.0
    assert {s["id"] for s in ep["sources"]} == {a.id for a in articles}
    assert set(ep["speakers"]) == {"haru", "sora"}
    assert "voicevox_speaker" not in ep["speakers"]["haru"]  # 内部設定は公開しない


def test_vtt(articles, characters, reader):
    assert fmt_vtt_time(3725.5) == "01:02:05.500"
    vtt = to_vtt(make_episode(articles, characters, reader))
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.500" in vtt and "<v ハル>" in vtt


def test_prune_removes_old_audio_but_keeps_script(tmp_path, articles, characters, reader):
    site = tmp_path
    today = date(2026, 11, 8)
    for days_ago in (0, 10, 31, 40):
        d = today - timedelta(days=days_ago)
        audio = f"audio/{d}.wav"
        (site / "audio").mkdir(exist_ok=True)
        (site / audio).write_bytes(b"x" * 2000)
        write_episode(site, make_episode(articles, characters, reader, d=d, audio=audio))
    res = prune_and_index(site, today, keep_days=30)
    assert sorted(res["removed_audio"]) == ["audio/2026-09-29.wav", "audio/2026-10-08.wav"]
    assert not (site / "audio/2026-10-08.wav").exists() and (site / "audio/2026-10-29.wav").exists()
    index = json.loads((site / "episodes/index.json").read_text(encoding="utf-8"))
    assert [e["date"] for e in index["episodes"]] == ["2026-11-08", "2026-10-29", "2026-10-08", "2026-09-29"]
    assert [e["has_audio"] for e in index["episodes"]] == [True, True, False, False]
    assert (site / "episodes/2026-09-29.json").exists()


def test_seen_urls_covers_recent_days_only(tmp_path, articles, characters, reader):
    write_episode(tmp_path, make_episode(articles[:1], characters, reader, d=date(2026, 11, 7)))
    write_episode(tmp_path, make_episode(articles[1:2], characters, reader, d=date(2026, 11, 1)))
    urls = seen_urls(tmp_path, days=3, today=date(2026, 11, 8))
    assert len(urls) == 1
