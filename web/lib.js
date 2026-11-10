/* ポプラジ: UIから切り離した純粋関数（tests.html でテストする） */
(function (root) {
  "use strict";

  const SPEEDS = [0.8, 1.0, 1.25, 1.5, 2.0];

  function formatTime(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
  }

  /** 時刻 t で話しているセリフの index。セリフ間の無音では直前のセリフを返す。開始前は -1。 */
  function findLineIndex(lines, t) {
    let lo = 0, hi = lines.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (lines[mid].start <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  }

  /** そのセリフを「今まさに発話中」か（キャラの口パク判定） */
  function isSpeaking(lines, idx, t) {
    return idx >= 0 && idx < lines.length && t >= lines[idx].start && t <= lines[idx].end;
  }

  function findChapterIndex(chapters, t) {
    let ans = 0;
    for (let i = 0; i < chapters.length; i++) if (chapters[i].start <= t + 0.05) ans = i;
    return ans;
  }

  function nextSpeed(cur) {
    const i = SPEEDS.findIndex((s) => Math.abs(s - cur) < 1e-6);
    return SPEEDS[(i + 1) % SPEEDS.length];
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  /** チャプター開始位置を進捗バー上の % に */
  function chapterMarkers(chapters, duration) {
    if (!duration) return [];
    return chapters.map((c, i) => ({ i, left: clamp((c.start / duration) * 100, 0, 100), title: c.title }));
  }

  /** 前のチャプター: 現チャプター開始から3秒以内なら1つ前、それ以降なら現チャプター先頭へ */
  function prevChapterTime(chapters, t) {
    const i = findChapterIndex(chapters, t);
    if (t - chapters[i].start > 3 || i === 0) return chapters[i].start;
    return chapters[i - 1].start;
  }

  function nextChapterTime(chapters, t, duration) {
    const i = findChapterIndex(chapters, t);
    return i + 1 < chapters.length ? chapters[i + 1].start : duration;
  }

  /** 続きから再生する位置。最初の5秒・残り10秒以内なら最初から */
  function resumePosition(saved, duration) {
    if (!saved || !duration || saved < 5 || saved > duration - 10) return 0;
    return saved;
  }

  function isoDate(d) {
    const z = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate());
  }

  /** 連続リスニング日数。今日聴いていなくても昨日まで続いていれば継続中として数える */
  function computeStreak(listenedDates, todayIso) {
    const set = new Set(listenedDates);
    const d = new Date(todayIso + "T00:00:00");
    if (!set.has(todayIso)) d.setDate(d.getDate() - 1);
    let n = 0;
    while (set.has(isoDate(d))) { n++; d.setDate(d.getDate() - 1); }
    return n;
  }

  const WD = ["日", "月", "火", "水", "木", "金", "土"];
  function dateLabel(iso) {
    const d = new Date(iso + "T00:00:00");
    return { md: (d.getMonth() + 1) + "/" + d.getDate(), wd: WD[d.getDay()], dow: d.getDay() };
  }

  function greeting(hour) {
    if (hour < 4) return "夜ふかしさん、こんばんは🌙";
    if (hour < 11) return "おはよう！今日のラジオできてるよ☀️";
    if (hour < 17) return "こんにちは！今日のニュース、聴いた？🎧";
    return "おつかれさま！今日のまとめをどうぞ🌆";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /** 外部リンクは http(s) のみ許可（javascript: などを弾く） */
  function safeUrl(u) {
    try {
      const url = new URL(u);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
    } catch (e) { return "#"; }
  }

  /** Google マップ検索URL */
  function mapsUrl(venue, area) {
    const q = [venue, area].filter(Boolean).join(" ");
    return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q);
  }

  /** Googleカレンダーの予定作成URL（会期は説明欄に入れ、日時はユーザーが選ぶ） */
  function calendarUrl(title, ev, link) {
    const p = new URLSearchParams({ action: "TEMPLATE", text: title });
    if (ev && ev.venue) p.set("location", ev.venue);
    const details = [ev && ev.period ? "会期: " + ev.period : "", link || ""].filter(Boolean).join("\n");
    if (details) p.set("details", details);
    return "https://calendar.google.com/calendar/render?" + p.toString();
  }

  const SLEEP_STEPS = [0, 15, 30, 60];
  function nextSleep(cur) {
    const i = SLEEP_STEPS.indexOf(cur);
    return SLEEP_STEPS[(i + 1) % SLEEP_STEPS.length];
  }

  const api = { SPEEDS, mapsUrl, calendarUrl, SLEEP_STEPS, nextSleep, formatTime, findLineIndex, isSpeaking, findChapterIndex, nextSpeed, clamp, chapterMarkers,
    prevChapterTime, nextChapterTime, resumePosition, computeStreak, isoDate, dateLabel, greeting, escapeHtml, safeUrl };
  root.PopLib = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
