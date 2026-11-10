/* ポプラジ プレーヤー */
(function () {
  "use strict";
  const L = window.PopLib;
  const $ = (id) => document.getElementById(id);
  const audio = $("audio");

  const store = {
    get(k, d) { try { const v = localStorage.getItem("popradi:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("popradi:" + k, JSON.stringify(v)); } catch (e) { /* private mode etc. */ } },
  };

  const state = { station: null, index: null, ep: null, lineIdx: -2, chapterIdx: -1, speaking: null, raf: 0, lastSave: 0, celebrated: false };
  window.__popradi = state; // デバッグ・E2E検証用

  // ------------------------------------------------------------ data
  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  }

  async function boot() {
    $("greeting").textContent = L.greeting(new Date().getHours());
    renderStreak();
    try {
      [state.station, state.index] = await Promise.all([getJSON("station.json"), getJSON("episodes/index.json")]);
    } catch (e) {
      $("epTitle").textContent = "まだ放送がありません";
      $("epSummary").textContent = "最初の放送は明日の朝5時ごろ。お楽しみに！";
      state.index = { episodes: [] };
    }
    if (state.station) renderKeywords();
    renderArchive();
    window.addEventListener("hashchange", route);
    await route();
  }

  async function route() {
    const h = location.hash.replace(/^#\/?/, "") || "today";
    const [view, arg] = h.split("/");
    if (view === "ep" && arg) { await openEpisode(arg); show("today"); return; }
    if (view === "today" && !state.ep && state.index.episodes.length) await openEpisode(state.index.episodes[0].id);
    show(["today", "archive", "keywords"].includes(view) ? view : "today");
  }

  function show(view) {
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.dataset.view !== view));
    document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === view));
    if (view === "archive") renderArchive();
    state.view = view;
    updateMini();
    window.scrollTo({ top: 0 });
  }

  async function openEpisode(id) {
    if (state.ep && state.ep.id === id) return;
    let ep;
    try { ep = await getJSON("episodes/" + encodeURIComponent(id) + ".json"); }
    catch (e) { toast("この回は見つかりませんでした 🙇"); return; }
    audio.pause();
    state.ep = ep; state.lineIdx = -2; state.chapterIdx = -1; state.celebrated = false;
    renderEpisode();
    if (ep.audio) {
      audio.src = ep.audio;
      audio.playbackRate = store.get("speed", 1.0);
      const saved = L.resumePosition(store.get("pos:" + ep.id, 0), ep.duration);
      $("btnResume").classList.toggle("hidden", !saved);
      $("btnResume").textContent = "⏩ 続きから " + L.formatTime(saved);
      $("btnResume").dataset.t = saved;
    } else {
      audio.removeAttribute("src"); audio.load();
      $("btnResume").classList.add("hidden");
      toast("この回は台本だけ読めます 📜");
      toggleTranscript(true);
    }
    setDisabled(!ep.audio);
    $("miniTitle").textContent = ep.title;
    sync(0);
    setupMediaSession();
    refreshSaved();
  }

  // ------------------------------------------------------------ render
  function renderEpisode() {
    const ep = state.ep, esc = L.escapeHtml, d = L.dateLabel(ep.date);
    $("epDate").innerHTML = "<b>" + d.md + "</b><span>" + d.wd + "曜</span>";
    $("epTitle").textContent = ep.title;
    $("epSummary").textContent = ep.summary || "";
    const emoji = topicEmoji();
    const topics = [...new Set(ep.chapters.map((c) => c.topic).filter(Boolean))];
    $("epTopics").innerHTML = topics.map((t) => '<span class="chip">' + (emoji[t] || "📻") + " " + esc(t) + "</span>").join("");
    $("tDur").textContent = L.formatTime(ep.duration);
    $("markers").innerHTML = L.chapterMarkers(ep.chapters, ep.duration).slice(1).map((m) => '<i style="left:' + m.left + '%"></i>').join("");

    $("setlist").innerHTML = ep.chapters.map((c, i) =>
      '<li data-i="' + i + '"><span class="no">' + (i + 1) + '</span><span class="st"><b>' + (c.corner ? '<span class="corner">' + esc(c.corner) + "</span>" : "") + esc(c.title) + "</b><small>" +
      (c.topic ? (emoji[c.topic] || "📻") + " " + esc(c.topic) : i === 0 ? "🎵 はじまり" : "👋 おわり") +
      '</small></span><span class="tm">' + L.formatTime(c.start) + "</span></li>").join("");

    const chapterOf = {};
    ep.chapters.forEach((c) => (c.source_ids || []).forEach((id) => { chapterOf[id] = c; }));
    $("sources").innerHTML = ep.sources.length ? '<div class="src-wrap">' + ep.sources.map((s) => {
      const c = chapterOf[s.id] || {}, ev = c.event;
      const evHtml = ev ? '<div class="event"><dl>' +
        (ev.venue ? "<dt>📍 会場</dt><dd>" + esc(ev.venue) + "</dd>" : "") +
        (ev.period ? "<dt>🗓 会期</dt><dd>" + esc(ev.period) + "</dd>" : "") +
        (ev.area ? "<dt>🚃 エリア</dt><dd>" + esc(ev.area) + "</dd>" : "") + "</dl>" +
        '<div class="event-actions">' +
        (ev.venue || ev.area ? '<a href="' + esc(L.mapsUrl(ev.venue, ev.area)) + '" target="_blank" rel="noopener noreferrer">📍 地図</a>' : "") +
        '<a href="' + esc(L.calendarUrl(c.title || s.title, ev, s.url)) + '" target="_blank" rel="noopener noreferrer">📅 カレンダー</a>' +
        "</div></div>" : "";
      return '<div class="src-card"><a class="src" href="' + esc(L.safeUrl(s.url)) + '" target="_blank" rel="noopener noreferrer">' +
        '<div class="src-top">' + (c.corner ? '<span class="corner">' + esc(c.corner) + "</span>" : '<span class="src-topic">' + (emoji[s.topic] || "📻") + " " + esc(s.topic) + "</span>") +
        esc(s.source) + '<span class="go" style="margin-left:auto">読む ↗</span></div><b>' + esc(s.title) + "</b></a>" + evHtml + "</div>";
    }).join("") + "</div>"
      : '<p class="empty">ニュース元はありません</p>';

    let lastCh = -1;
    $("transcript").innerHTML = ep.lines.map((ln, i) => {
      const sp = ep.speakers[ln.speaker] || { name: "?", color: "#999" };
      let head = "";
      if (ln.chapter !== lastCh) { lastCh = ln.chapter; head = '<div class="tl-chapter">― ' + esc(ep.chapters[ln.chapter].title) + " ―</div>"; }
      return head + '<div class="tl ' + esc(ln.speaker) + '" data-i="' + i + '"><span class="av" style="background:' + esc(sp.color) + '">' +
        esc(sp.name.slice(0, 1)) + '</span><span class="tx">' + esc(ln.text) + "</span></div>";
    }).join("");

    const credits = [...new Set(Object.values(ep.speakers).map((s) => s.credit).filter(Boolean))];
    $("credits").innerHTML =
      (ep.generator && ep.generator.sample ? '<span class="sample-badge">サンプルデータの放送です</span><br>' : "") +
      "台本: " + esc(ep.generator ? ep.generator.llm : "-") + " / 音声: " + esc(ep.generator ? ep.generator.tts : "-") +
      (credits.length ? "<br>音声クレジット: " + credits.map(esc).join(" / ") : "") +
      "<br>ニュースの内容は各記事の見出し・説明文をもとにAIが紹介しています。正確な情報は元記事で確認してね。";
  }

  function topicEmoji() {
    const m = {};
    (state.station ? state.station.topics : []).forEach((t) => { m[t.name] = t.emoji; });
    return m;
  }

  function renderArchive() {
    const eps = (state.index && state.index.episodes) || [];
    const listened = store.get("listened", []);
    $("archive").innerHTML = eps.length ? eps.map((e) => {
      const d = L.dateLabel(e.date);
      return '<a class="ac" href="#/ep/' + encodeURIComponent(e.id) + '">' +
        (listened.includes(e.id) ? '<span class="stamp">聴いた！</span>' : "") +
        '<span class="ad">' + d.md + "<small>" + d.wd + "</small></span>" +
        '<span class="at">' + L.escapeHtml(e.title) + "</span>" +
        (e.has_audio ? "" : '<span class="noaudio">📜 台本のみ</span>') +
        '<span class="am">⏱ ' + L.formatTime(e.duration) + " ・ 📰 " + e.news_count + "本</span></a>";
    }).join("") : '<p class="empty">まだ放送がありません 📭</p>';
  }

  function renderKeywords() {
    $("topics").innerHTML = state.station.topics.map((t) =>
      '<div class="card topic"><h3><span>' + L.escapeHtml(t.emoji) + "</span>" + L.escapeHtml(t.name) + '</h3><div class="kw">' +
      t.keywords.map((k) => "<span>#" + L.escapeHtml(k) + "</span>").join("") + "</div></div>").join("");
  }

  function renderStreak() {
    $("streakNum").textContent = L.computeStreak(store.get("listenedDays", []), L.isoDate(new Date()));
  }

  function setDisabled(dis) {
    ["btnPlay", "btnBack", "btnFwd", "btnPrev", "btnNext", "btnSpeed"].forEach((id) => { $(id).disabled = dis; $(id).style.opacity = dis ? .45 : 1; });
  }

  // ------------------------------------------------------------ sync (字幕・キャラ・進捗)
  function sync(t) {
    const ep = state.ep; if (!ep) return;
    const dur = ep.duration || audio.duration || 1;
    const pct = L.clamp((t / dur) * 100, 0, 100);
    $("progressFill").style.width = pct + "%";
    $("progressKnob").style.left = pct + "%";
    $("progress").setAttribute("aria-valuenow", Math.round(pct));
    $("tCur").textContent = L.formatTime(t);
    $("miniTime").textContent = L.formatTime(t);
    $("miniFill").style.width = pct + "%";

    const li = L.findLineIndex(ep.lines, t);
    const speaking = L.isSpeaking(ep.lines, li, t) && !audio.paused ? ep.lines[li].speaker : null;
    if (speaking !== state.speaking) {
      state.speaking = speaking;
      document.querySelectorAll(".chara").forEach((c) => c.classList.toggle("talking", c.dataset.speaker === speaking));
      $("stage").classList.toggle("has-speaker", !!speaking);
    }
    if (li !== state.lineIdx) {
      state.lineIdx = li;
      const b = $("bubble");
      if (li >= 0) {
        const ln = ep.lines[li], sp = ep.speakers[ln.speaker];
        b.dataset.speaker = ln.speaker;
        $("bubbleName").textContent = sp ? sp.name : "";
        $("bubbleText").textContent = ln.text;
      } else {
        delete b.dataset.speaker;
        $("bubbleName").textContent = state.station ? state.station.station_name : "ポプラジ";
        $("bubbleText").textContent = t > 0 ? "🎵 〜オープニングジングル〜 🎵" : "▶ を押すと今日の放送がはじまるよ！";
      }
      b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
      document.querySelectorAll(".tl.now").forEach((n) => n.classList.remove("now"));
      const row = document.querySelector('.tl[data-i="' + li + '"]');
      if (row) row.classList.add("now");
      scrollTranscriptToNow();
    }
    const ci = L.findChapterIndex(ep.chapters, t);
    if (ci !== state.chapterIdx) {
      state.chapterIdx = ci;
      $("tChapter").textContent = "♪ " + ep.chapters[ci].title;
      $("miniChapter").textContent = "♪ " + ep.chapters[ci].title;
      document.querySelectorAll("#setlist li").forEach((n, i) => { n.classList.toggle("now", i === ci); n.classList.toggle("done", i < ci); });
      if ("mediaSession" in navigator && navigator.mediaSession.metadata) navigator.mediaSession.metadata.album = ep.chapters[ci].title;
    }
    if (ep.audio && Date.now() - state.lastSave > 4000) { state.lastSave = Date.now(); store.set("pos:" + ep.id, t); }
  }

  function loop() {
    sync(audio.currentTime);
    state.raf = requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ controls
  function seek(t) {
    if (!state.ep || !state.ep.audio) return;
    audio.currentTime = L.clamp(t, 0, state.ep.duration);
    sync(audio.currentTime);
  }
  function togglePlay() {
    if (!state.ep || !state.ep.audio) return;
    if (audio.paused) audio.play().catch(() => toast("再生できませんでした。もう一度タップしてね"));
    else audio.pause();
  }
  function toggleTranscript(force) {
    const box = $("transcript");
    const open = typeof force === "boolean" ? force : box.classList.contains("hidden");
    box.classList.toggle("hidden", !open);
    $("btnTranscript").setAttribute("aria-expanded", String(open));
    if (open) scrollTranscriptToNow();
  }
  function scrollTranscriptToNow() {
    const box = $("transcript"), row = box.querySelector(".tl.now");
    if (!row || box.classList.contains("hidden")) return;
    const rb = row.getBoundingClientRect(), bb = box.getBoundingClientRect();
    box.scrollTop += rb.top - bb.top - box.clientHeight / 2 + rb.height / 2;
  }
  function setSpeed(s) {
    audio.playbackRate = s; store.set("speed", s);
    $("btnSpeed").textContent = (s < 1 ? "🐢 " : s > 1.3 ? "🚀 " : s > 1 ? "🐇 " : "🚶 ") + s.toFixed(s % 1 ? 2 : 1).replace(/0$/, "") + "x";
  }

  $("btnPlay").addEventListener("click", togglePlay);
  $("miniPlay").addEventListener("click", togglePlay);
  $("btnSleep").addEventListener("click", () => setSleep(L.nextSleep(state.sleepMin || 0)));
  $("btnSave").addEventListener("click", saveOffline);
  $("btnBack").addEventListener("click", () => seek(audio.currentTime - 15));
  $("btnFwd").addEventListener("click", () => seek(audio.currentTime + 15));
  $("btnPrev").addEventListener("click", () => state.ep && seek(L.prevChapterTime(state.ep.chapters, audio.currentTime)));
  $("btnNext").addEventListener("click", () => state.ep && seek(L.nextChapterTime(state.ep.chapters, audio.currentTime, state.ep.duration)));
  $("btnSpeed").addEventListener("click", () => setSpeed(L.nextSpeed(audio.playbackRate)));
  $("btnTranscript").addEventListener("click", () => toggleTranscript());
  $("btnResume").addEventListener("click", (e) => { seek(+e.currentTarget.dataset.t); e.currentTarget.classList.add("hidden"); audio.play().catch(() => {}); });
  $("setlist").addEventListener("click", (e) => {
    const li = e.target.closest("li"); if (!li || !state.ep) return;
    seek(state.ep.chapters[+li.dataset.i].start + 0.01);
    if (audio.paused && state.ep.audio) audio.play().catch(() => {});
  });
  $("transcript").addEventListener("click", (e) => {
    const row = e.target.closest(".tl"); if (row && state.ep) seek(state.ep.lines[+row.dataset.i].start + 0.01);
  });

  // 進捗バー: タップ & ドラッグ
  const bar = $("progress");
  function barSeek(ev) {
    const r = bar.getBoundingClientRect();
    seek(L.clamp((ev.clientX - r.left) / r.width, 0, 1) * (state.ep ? state.ep.duration : 0));
  }
  bar.addEventListener("pointerdown", (ev) => { bar.setPointerCapture(ev.pointerId); barSeek(ev); });
  bar.addEventListener("pointermove", (ev) => { if (bar.hasPointerCapture(ev.pointerId)) barSeek(ev); });
  bar.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight") seek(audio.currentTime + 5);
    if (ev.key === "ArrowLeft") seek(audio.currentTime - 5);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.code === "Space" && !["BUTTON", "INPUT", "A"].includes(document.activeElement.tagName)) { ev.preventDefault(); togglePlay(); }
  });

  audio.addEventListener("play", () => {
    document.body.classList.add("playing"); $("btnPlay").setAttribute("aria-label", "一時停止"); $("miniPlay").setAttribute("aria-label", "一時停止");
    state.started = true; updateMini(); cancelAnimationFrame(state.raf); loop();
  });
  audio.addEventListener("pause", () => {
    document.body.classList.remove("playing"); $("btnPlay").setAttribute("aria-label", "再生"); $("miniPlay").setAttribute("aria-label", "再生");
    cancelAnimationFrame(state.raf); sync(audio.currentTime);
    if (state.ep) store.set("pos:" + state.ep.id, audio.currentTime);
  });
  audio.addEventListener("ratechange", () => setSpeed(audio.playbackRate));
  audio.addEventListener("timeupdate", () => { if (audio.paused) sync(audio.currentTime); });
  audio.addEventListener("ended", onEnded);
  audio.addEventListener("error", () => { if (state.ep && state.ep.audio) toast("音声を読み込めませんでした 📡"); });

  function onEnded() {
    const ep = state.ep; if (!ep) return;
    store.set("pos:" + ep.id, 0);
    const listened = store.get("listened", []);
    if (!listened.includes(ep.id)) { listened.push(ep.id); store.set("listened", listened.slice(-200)); }
    const days = store.get("listenedDays", []), today = L.isoDate(new Date());
    if (!days.includes(today)) { days.push(today); store.set("listenedDays", days.slice(-400)); }
    renderStreak();
    if (!state.celebrated) { state.celebrated = true; confetti(); toast("🎉 今日も最後まで聴いてくれてありがとう！ 連続 " + $("streakNum").textContent + " 日"); }
  }

  // ------------------------------------------------------------ Media Session (ロック画面・イヤホン操作)
  function setupMediaSession() {
    if (!("mediaSession" in navigator) || !state.ep) return;
    const ep = state.ep;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: ep.title,
      artist: (state.station ? state.station.show_title : "ポプラジ") + " ・ " + L.dateLabel(ep.date).md,
      album: ep.chapters[0] ? ep.chapters[0].title : "",
      artwork: [{ src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }, { src: "icons/icon.svg", sizes: "any", type: "image/svg+xml" }],
    });
    const h = {
      play: () => audio.play(), pause: () => audio.pause(),
      seekbackward: () => seek(audio.currentTime - 15), seekforward: () => seek(audio.currentTime + 15),
      previoustrack: () => seek(L.prevChapterTime(ep.chapters, audio.currentTime)),
      nexttrack: () => seek(L.nextChapterTime(ep.chapters, audio.currentTime, ep.duration)),
      seekto: (d) => seek(d.seekTime),
    };
    Object.entries(h).forEach(([k, fn]) => { try { navigator.mediaSession.setActionHandler(k, fn); } catch (e) { /* unsupported */ } });
  }

  // ------------------------------------------------------------ ミニプレーヤー
  function updateMini() {
    const show = !!(state.ep && state.ep.audio && state.started && state.view && state.view !== "today");
    $("mini").classList.toggle("hidden", !show);
    document.body.classList.toggle("has-mini", show);
  }

  // ------------------------------------------------------------ スリープタイマー
  function setSleep(min) {
    clearInterval(state.sleepTimer);
    state.sleepMin = min;
    state.sleepUntil = min ? Date.now() + min * 60000 : 0;
    const btn = $("btnSleep");
    btn.classList.toggle("on", !!min);
    if (!min) { btn.textContent = "😴 タイマー"; return; }
    toast("😴 " + min + "分後に止めるね");
    const tick = () => {
      const left = state.sleepUntil - Date.now();
      if (left <= 0) {
        clearInterval(state.sleepTimer);
        let v = 1;  // 5秒かけて小さくしてから止める
        const fade = setInterval(() => {
          v -= 0.1; audio.volume = Math.max(0, v);
          if (v <= 0) { clearInterval(fade); audio.pause(); audio.volume = 1; setSleep(0); toast("おやすみなさい 🌙"); }
        }, 500);
        return;
      }
      btn.textContent = "😴 " + L.formatTime(left / 1000);
    };
    tick();
    state.sleepTimer = setInterval(tick, 1000);
  }
  window.__popradiSetSleep = setSleep;  // E2E検証用

  // ------------------------------------------------------------ オフライン保存
  const AUDIO_CACHE = "popradi-audio";
  async function refreshSaved() {
    const btn = $("btnSave");
    if (!("caches" in window) || !state.ep || !state.ep.audio) { btn.classList.add("hidden"); return; }
    btn.classList.remove("hidden");
    let hit = null;
    try { hit = await (await caches.open(AUDIO_CACHE)).match(new URL(state.ep.audio, location.href).href); } catch (e) { /* noop */ }
    btn.classList.toggle("saved", !!hit);
    btn.textContent = hit ? "✅ 保存済み" : "⬇️ 保存";
  }
  async function saveOffline() {
    if (!state.ep || !state.ep.audio || !("caches" in window)) return;
    const btn = $("btnSave");
    if (btn.classList.contains("saved")) { toast("この回はオフラインでも聴けます ✅"); return; }
    btn.textContent = "⏳ 保存中…";
    try {
      const url = new URL(state.ep.audio, location.href).href;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const cache = await caches.open(AUDIO_CACHE);
      await cache.put(url, res);
      const keys = await cache.keys();  // 容量節約: 新しい3回分まで
      for (const k of keys.slice(0, Math.max(0, keys.length - 3))) await cache.delete(k);
      toast("📥 保存しました。電波がなくても聴けます");
    } catch (e) {
      toast("保存できませんでした 🙇");
    }
    refreshSaved();
  }

  // ------------------------------------------------------------ fun
  let toastTimer = 0;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  function confetti() {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const c = $("confetti"), ctx = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    c.width = innerWidth * dpr; c.height = innerHeight * dpr; ctx.scale(dpr, dpr);
    const colors = ["#FF7A3D", "#2EC4B6", "#FFD23F", "#FF5DA2", "#7B5CFF", "#3A86FF"];
    const ps = Array.from({ length: 140 }, () => ({
      x: innerWidth / 2, y: innerHeight * 0.45, vx: (Math.random() - 0.5) * 14, vy: -Math.random() * 15 - 4,
      r: Math.random() * 7 + 4, c: colors[(Math.random() * colors.length) | 0], a: Math.random() * 6, va: (Math.random() - 0.5) * 0.4,
    }));
    let frames = 0;
    (function step() {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      ps.forEach((p) => {
        p.vy += 0.38; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.a += p.va;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.a); ctx.fillStyle = p.c; ctx.strokeStyle = "#1D1A2F"; ctx.lineWidth = 1.5;
        ctx.fillRect(-p.r / 2, -p.r / 3, p.r, p.r * 0.66); ctx.strokeRect(-p.r / 2, -p.r / 3, p.r, p.r * 0.66); ctx.restore();
      });
      if (++frames < 170) requestAnimationFrame(step); else ctx.clearRect(0, 0, innerWidth, innerHeight);
    })();
  }

  setSpeed(store.get("speed", 1.0));
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  boot();
})();
