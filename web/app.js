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
    renderKeywords();
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
      audio.playbackRate = 1.0;
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
        esc(s.source) + '<span class="go" style="margin-left:auto">読む ↗</span></div><b>' + esc(s.title) + "</b></a>" + evHtml +
        ((s.notes && s.notes.length) || (s.related && s.related.length) ? '<div class="refs">' +
          (s.related && s.related.length ? "<span>📰 関連記事 " + s.related.length + "件</span>" : "") +
          (s.notes || []).map((n) => '<a href="' + esc(L.safeUrl(n.url)) + '" target="_blank" rel="noopener noreferrer">📖 ' + esc(n.source) + "</a>").join("") +
          "</div>" : "") + "</div>";
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
      "<br>ニュースの内容は各記事の見出し・説明文をもとにAIが紹介しています。正確な情報は元記事で確認してね。" +
      ((ep.sources || []).some((s) => s.notes && s.notes.length) ? "<br>背景情報の一部は Wikipedia（CC BY-SA）を参考にしています。" : "");
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
    ["btnPlay", "btnBack", "btnFwd", "btnPrev", "btnNext", "volume"].forEach((id) => { $(id).disabled = dis; $(id).style.opacity = dis ? .45 : 1; });
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
    if (open) scrollTranscriptToNow();
  }
  function scrollTranscriptToNow() {
    const box = $("transcript"), row = box.querySelector(".tl.now");
    if (!row || box.classList.contains("hidden")) return;
    const rb = row.getBoundingClientRect(), bb = box.getBoundingClientRect();
    box.scrollTop += rb.top - bb.top - box.clientHeight / 2 + rb.height / 2;
  }
  // ------------------------------------------------------------ 音量
  function setVolume(v) {
    v = L.clamp(+v, 0, 1);
    audio.volume = v;
    $("volume").value = v;
    $("volume").style.setProperty("--vol", Math.round(v * 100) + "%");
    store.set("volume", v);
  }
  function initVolume() {
    // iPhone/iPad は JavaScript から音量を変えられない（本体ボタンのみ）ので、その場合はバーを隠す
    const probe = new Audio();
    probe.volume = 0.5;
    const supported = Math.abs(probe.volume - 0.5) < 0.01;
    $("volumeWrap").classList.toggle("hidden", !supported);
    $("volumeNote").classList.toggle("hidden", supported);
    if (supported) setVolume(store.get("volume", 1));
  }
  $("volume").addEventListener("input", (e) => setVolume(e.target.value));

  $("btnPlay").addEventListener("click", togglePlay);
  $("miniPlay").addEventListener("click", togglePlay);
  $("btnBack").addEventListener("click", () => seek(audio.currentTime - 15));
  $("btnFwd").addEventListener("click", () => seek(audio.currentTime + 15));
  $("btnPrev").addEventListener("click", () => state.ep && seek(L.prevChapterTime(state.ep.chapters, audio.currentTime)));
  $("btnNext").addEventListener("click", () => state.ep && seek(L.nextChapterTime(state.ep.chapters, audio.currentTime, state.ep.duration)));
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


  // ------------------------------------------------------------ GitHub 連携（キーワード編集・今すぐ更新）
  const GH_API = "https://api.github.com";
  const WORKFLOW = "daily-radio.yml";
  const gh = {
    repo() {
      return store.get("ghRepo", null) || L.repoFromLocation(location.hostname, location.pathname) || { owner: "nita06290", repo: "popradi" };
    },
    token() { return store.get("ghToken", ""); },
    async call(path, opts) {
      const r = await fetch(GH_API + path, Object.assign({}, opts, {
        headers: Object.assign({ Accept: "application/vnd.github+json", Authorization: "Bearer " + gh.token(), "X-GitHub-Api-Version": "2022-11-28" }, (opts && opts.headers) || {}),
      }));
      if (r.status === 401) throw new Error("トークンが無効か期限切れみたい（⚙️から設定し直してね）");
      if (r.status === 403 || r.status === 404) throw new Error("権限が足りないみたい（Contents と Actions を Read and write にしてね）");
      if (!r.ok) throw new Error("GitHub エラー " + r.status);
      return r.status === 204 ? null : r.json();
    },
    path() { const { owner, repo } = gh.repo(); return "/repos/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo); },
    async loadTopics() {
      const f = await gh.call(gh.path() + "/contents/config/topics.json?ref=main");
      return { cfg: JSON.parse(L.b64ToUtf8(f.content)), sha: f.sha };
    },
    async saveTopics(cfg, sha, message) {
      const body = { message: message, content: L.utf8ToB64(JSON.stringify(cfg, null, 2) + "\n"), sha: sha, branch: "main" };
      const r = await gh.call(gh.path() + "/contents/config/topics.json", { method: "PUT", body: JSON.stringify(body) });
      return r.content.sha;
    },
    async dispatch() {
      await gh.call(gh.path() + "/actions/workflows/" + WORKFLOW + "/dispatches", { method: "POST", body: JSON.stringify({ ref: "main" }) });
    },
    async latestRun() {
      const r = await gh.call(gh.path() + "/actions/workflows/" + WORKFLOW + "/runs?per_page=1");
      return r.workflow_runs[0] || null;
    },
  };

  // ---- 設定シート
  function openSheet() {
    const { owner, repo } = gh.repo();
    $("repoName").textContent = repo;
    $("tokenInput").value = "";
    $("ghStatus").textContent = gh.token() ? "✅ 連携中（" + owner + "/" + repo + "）" : "まだ連携していません";
    $("ghStatus").classList.toggle("ok", !!gh.token());
    $("btnDisconnect").classList.toggle("hidden", !gh.token());
    $("sheet").classList.remove("hidden");
  }
  function closeSheet() { $("sheet").classList.add("hidden"); }
  $("btnSettings").addEventListener("click", openSheet);
  $("btnConnect").addEventListener("click", openSheet);
  $("btnSheetClose").addEventListener("click", closeSheet);
  $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
  $("btnDisconnect").addEventListener("click", () => {
    store.set("ghToken", ""); toast("連携を解除しました"); closeSheet(); renderKeywords();
  });
  $("btnTokenSave").addEventListener("click", async () => {
    const t = $("tokenInput").value.trim();
    if (!t) { toast("トークンを貼り付けてね"); return; }
    const prev = gh.token();
    store.set("ghToken", t);
    try {
      await gh.loadTopics();
      toast("🔗 連携できました！");
      closeSheet();
      await renderKeywords(true);
    } catch (e) {
      store.set("ghToken", prev);
      toast("連携できませんでした: " + e.message);
    }
  });

  // ---- キーワード編集
  state.kw = { cfg: null, sha: null, busy: false, addingTo: null };
  async function renderKeywords(reload) {
    const connected = !!gh.token();
    $("connectCard").classList.toggle("hidden", connected);
    $("btnAddTopic").classList.toggle("hidden", !connected);
    $("kwHint").textContent = connected ? "タグの × で削除、＋ で追加。変更は次の更新から反映されます。" : "毎朝このキーワードでニュースを集めて番組にしています。";
    if (connected && (reload || !state.kw.cfg)) {
      try { Object.assign(state.kw, await gh.loadTopics()); }
      catch (e) { toast(e.message); }
    }
    const topics = connected && state.kw.cfg ? state.kw.cfg.topics : (state.station ? state.station.topics : []);
    const esc = L.escapeHtml;
    $("topics").innerHTML = topics.map((t, ti) =>
      '<div class="card topic" data-ti="' + ti + '"><div class="topic-head"><h3><span>' + esc(t.emoji || "📻") + "</span>" + esc(t.name) + "</h3>" +
      (connected ? '<button class="topic-del" data-act="delTopic" aria-label="トピックを削除">🗑</button>' : "") + '</div><div class="kw">' +
      t.keywords.map((k) => '<span class="tag">#' + esc(k) + (connected ? '<button data-act="delKw" data-kw="' + esc(k) + '" aria-label="' + esc(k) + 'を削除">×</button>' : "") + "</span>").join("") +
      (connected ? (state.kw.addingTo === t.name
        ? '<div class="kw-form"><input data-role="kwInput" maxlength="40" placeholder="例: 美術展 横浜" aria-label="追加するキーワード"><button class="pill primary" data-act="kwDone">完了</button></div>'
        : '<button class="add" data-act="addKw">＋</button>') : "") +
      "</div></div>").join("");
    const input = $("topics").querySelector('[data-role="kwInput"]');
    if (input) input.focus();
  }

  async function applyEdit(op, okMsg) {
    if (state.kw.busy) return;
    let next;
    try { next = L.editTopics(state.kw.cfg, op); } catch (e) { toast(e.message); return; }
    state.kw.busy = true;
    document.querySelectorAll(".topic").forEach((n) => n.classList.add("saving"));
    try {
      state.kw.sha = await gh.saveTopics(next, state.kw.sha, "キーワード変更（アプリから）: " + okMsg);
      state.kw.cfg = next;
      toast("✅ " + okMsg + "。次の更新から反映されます");
    } catch (e) {
      toast("保存できませんでした: " + e.message);
      try { Object.assign(state.kw, await gh.loadTopics()); } catch (e2) { /* noop */ }
    }
    state.kw.busy = false;
    state.kw.addingTo = null;
    renderKeywords();
  }

  $("topics").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]"); if (!btn) return;
    const card = btn.closest(".topic"); const topic = state.kw.cfg.topics[+card.dataset.ti];
    const act = btn.dataset.act;
    if (act === "addKw") { state.kw.addingTo = topic.name; renderKeywords(); }
    else if (act === "kwDone") {
      const v = card.querySelector('[data-role="kwInput"]').value;
      if (!v.trim()) { state.kw.addingTo = null; renderKeywords(); return; }
      applyEdit({ type: "addKeyword", topic: topic.name, keyword: v }, "「" + v.trim() + "」を追加しました");
    }
    else if (act === "delKw") applyEdit({ type: "removeKeyword", topic: topic.name, keyword: btn.dataset.kw }, "「" + btn.dataset.kw + "」を削除しました");
    else if (act === "delTopic") {
      if (!window.confirm("トピック「" + topic.name + "」を削除しますか？")) return;
      applyEdit({ type: "removeTopic", topic: topic.name }, "トピック「" + topic.name + "」を削除しました");
    }
  });
  $("topics").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.dataset.role === "kwInput") { e.preventDefault(); e.target.closest(".topic").querySelector('[data-act="kwDone"]').click(); }
  });
  $("btnAddTopic").addEventListener("click", () => { $("addTopicForm").classList.remove("hidden"); $("btnAddTopic").classList.add("hidden"); $("newTopicName").focus(); });
  $("btnTopicCancel").addEventListener("click", () => { $("addTopicForm").classList.add("hidden"); $("btnAddTopic").classList.remove("hidden"); });
  $("btnTopicDone").addEventListener("click", async () => {
    const name = $("newTopicName").value, kw = $("newTopicKw").value, emoji = $("newTopicEmoji").value;
    await applyEdit({ type: "addTopic", name: name, keyword: kw, emoji: emoji }, "トピック「" + name.trim() + "」を追加しました");
    if (state.kw.cfg && state.kw.cfg.topics.some((t) => t.name === name.trim())) {
      ["newTopicName", "newTopicKw", "newTopicEmoji"].forEach((id) => { $(id).value = ""; });
      $("addTopicForm").classList.add("hidden"); $("btnAddTopic").classList.remove("hidden");
    }
  });

  // ---- 今すぐ更新
  const EXPECTED_SEC = 15 * 60;
  async function startRefresh() {
    if (!gh.token()) { toast("今すぐ更新するには、キーワード画面の ⚙️ でGitHubと連携してね"); location.hash = "#/keywords"; return; }
    if (state.building) { toast("いま作っているところだよ 📻"); return; }
    if (!window.confirm("今の最新ニュースで番組を作り直しますか？（15分くらいかかります）")) return;
    try {
      const before = await gh.latestRun();
      await gh.dispatch();
      state.building = { since: Date.now(), prevRunId: before ? before.id : 0, runId: 0 };
      store.set("building", state.building);
      showBuilding();
      toast("🔄 番組づくりを始めました");
    } catch (e) { toast("開始できませんでした: " + e.message); }
  }
  function showBuilding() {
    const b = state.building;
    $("building").classList.toggle("hidden", !b);
    $("btnRefresh").classList.toggle("spin", !!b);
    if (!b) return;
    const sec = (Date.now() - b.since) / 1000;
    $("buildingFill").style.width = L.buildProgress(sec, EXPECTED_SEC) + "%";
    $("buildingSub").textContent = b.phase === "publish" ? "できあがり！公開の準備中…" : "ニュース集め → 台本 → 音声（" + L.formatTime(sec) + " 経過・目安15分）";
    clearTimeout(state.buildTimer);
    state.buildTimer = setTimeout(pollBuilding, 15000);
  }
  async function pollBuilding() {
    const b = state.building; if (!b) return;
    try {
      if (b.phase !== "publish") {
        const run = await gh.latestRun();
        if (run && run.id !== b.prevRunId) {
          b.runId = run.id;
          if (run.status === "completed") {
            if (run.conclusion !== "success") { finishBuilding(false); return; }
            b.phase = "publish"; b.doneAt = Date.now();
          }
        }
      } else {
        // gh-pages の公開を待って新しい回を読み込む
        const idx = await getJSON("episodes/index.json?t=" + Date.now());
        const top = idx.episodes[0];
        const ep = top ? await getJSON("episodes/" + top.id + ".json?t=" + Date.now()) : null;
        if (ep && new Date(ep.generated_at).getTime() > b.since) { state.index = idx; finishBuilding(true, ep.id); return; }
        if (Date.now() - b.doneAt > 10 * 60000) { finishBuilding(true); return; }
      }
    } catch (e) { /* 通信エラーは次の確認で */ }
    store.set("building", b);
    showBuilding();
  }
  function finishBuilding(ok, epId) {
    state.building = null; store.set("building", null);
    clearTimeout(state.buildTimer); showBuilding();
    if (!ok) { toast("番組づくりに失敗しました 🙇 GitHubの通知を確認してね"); return; }
    toast("🎉 新しい番組ができました！");
    renderArchive();
    if (epId) { state.ep = null; openEpisode(epId); }
  }
  $("btnRefresh").addEventListener("click", startRefresh);

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

  initVolume();
  state.building = store.get("building", null);
  if (state.building && Date.now() - state.building.since > 60 * 60000) { state.building = null; store.set("building", null); }
  if (state.building) showBuilding();
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  boot();
})();
