/* 《归潮之岛》AI NPC 后端 —— 验收回放页面逻辑
 *
 * 纯前端、零依赖、无网络请求。所有内容来自 data.js，而 data.js 逐字摘自仓库中的真实运行报告。
 */

(function () {
  "use strict";

  var DATA = window.DEMO_DATA;
  var ARC_STAGES = ["fast_path", "guarded", "conflicted", "confession"];
  var STAGE_LABEL = {
    fast_path: "fast_path",
    guarded: "guarded",
    conflicted: "conflicted",
    confession: "confession",
    "-": "—",
  };
  var KIND_LABEL = {
    chat: "对话轮次",
    infra: "就绪检查",
    auth: "认证",
    boundary: "契约边界",
  };

  var state = { run: 0, step: 0, timer: null };

  /* ------------------------------ 工具 ------------------------------ */

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function el(id) { return document.getElementById(id); }

  function currentRun() { return DATA.runs[state.run]; }

  function currentStep() { return currentRun().steps[state.step]; }

  function fmtMs(ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms >= 1000) return (ms / 1000).toFixed(2) + " s";
    return Math.round(ms) + " ms";
  }

  /* ---------------------------- 顶部说明 ---------------------------- */

  function renderChrome() {
    el("replay-notice").innerHTML = esc(DATA.meta.replayNotice);

    var srcHost = el("src-list");
    srcHost.innerHTML = DATA.meta.sources
      .map(function (s) {
        return "<li><code>" + esc(s.label) + "</code><span>" + esc(s.detail) + "</span></li>";
      })
      .join("");
  }

  /* --------------------------- 运行选择条 --------------------------- */

  function renderRunSwitch() {
    var host = el("run-switch");
    host.innerHTML = DATA.runs
      .map(function (run, i) {
        var cls = "run-btn" + (i === state.run ? " is-active" : "") + (run.passed ? "" : " is-failed");
        return (
          '<button class="' + cls + '" data-run="' + i + '" type="button">' +
          "<strong>" + esc(run.label) + "</strong>" +
          "<span>" + esc(run.model) + " · " + esc(run.badge) + "</span>" +
          "</button>"
        );
      })
      .join("");

    Array.prototype.forEach.call(host.querySelectorAll("[data-run]"), function (btn) {
      btn.addEventListener("click", function () {
        state.run = parseInt(btn.getAttribute("data-run"), 10);
        state.step = 0;
        renderAll();
      });
    });

    var run = currentRun();
    el("run-summary").textContent =
      run.summary + "  生成时间 " + run.generatedAt + " · " + run.baseUrl;
  }

  /* ---------------------------- 步骤列表 ---------------------------- */

  function renderStepList() {
    var run = currentRun();
    var host = el("step-list");
    el("step-count").textContent = run.steps.filter(function (s) { return s.passed; }).length +
      "/" + run.steps.length + " 通过";

    host.innerHTML = run.steps
      .map(function (step, i) {
        var dotCls = step.passed ? "ok" : "bad";
        var dotTxt = step.passed ? "✓" : "!";
        var active = i === state.step ? " is-active" : "";
        return (
          '<li><button class="step-item' + active + '" data-step="' + i + '" type="button">' +
          '<span class="step-dot ' + dotCls + '">' + dotTxt + "</span>" +
          '<span class="step-body">' +
          '<span class="step-name">' + esc(step.title) + "</span>" +
          '<span class="step-sub">' + esc(KIND_LABEL[step.kind] || step.kind) + "</span>" +
          "</span></button></li>"
        );
      })
      .join("");

    Array.prototype.forEach.call(host.querySelectorAll("[data-step]"), function (btn) {
      btn.addEventListener("click", function () {
        state.step = parseInt(btn.getAttribute("data-step"), 10);
        renderAll();
      });
    });
  }

  /* ------------------------------ 对话 ------------------------------ */

  function infraCard(step, animate) {
    var deps = (step.dependencies || [])
      .map(function (d) {
        return '<span class="dep"><b>' + esc(d.name) + "</b> " + esc(d.detail) + "</span>";
      })
      .join("");
    return (
      '<div class="syscard">' +
      "<h4>" + esc(step.title) + "</h4>" +
      '<div class="sys-meta">' + esc(step.brief) + " · " + esc(fmtMs(step.latencyMs)) + "</div>" +
      '<div class="deps">' + deps + "</div>" +
      "</div>"
    );
  }

  function authCard(step) {
    return (
      '<div class="syscard">' +
      "<h4>" + esc(step.title) + "</h4>" +
      '<div class="sys-meta">' + esc(step.brief) + " · " + esc(fmtMs(step.latencyMs)) + "</div>" +
      '<div class="deps">' +
      '<span class="dep"><b>username</b> ' + esc(step.username) + "</span>" +
      '<span class="dep"><b>token_type</b> ' + esc(step.tokenType) + "</span>" +
      "</div></div>"
    );
  }

  function boundaryCard(step) {
    return (
      '<div class="syscard">' +
      "<h4>" + esc(step.title) + "</h4>" +
      '<div class="sys-meta">' + esc(step.brief) + " · " + esc(fmtMs(step.latencyMs)) + "</div>" +
      '<div class="fail-note" style="margin-top:9px">' +
      "<b>HTTP " + esc(step.result.statusCode) + "</b> — " + esc(step.result.detail) +
      "</div></div>"
    );
  }

  function eventChips(step) {
    var types = (step.result && step.result.eventTypes) || [];
    if (!types.length) return "";
    return (
      '<div class="events" data-events>' +
      types
        .map(function (t) {
          var cls = "ev ev-" + t;
          return '<span class="' + cls + '" data-ev="' + esc(t) + '">' + esc(t) + "</span>";
        })
        .join('<span class="ev-arrow">→</span>') +
      "</div>"
    );
  }

  function turnHtml(step, index, isLast) {
    var req = step.request || {};
    var res = step.result || {};
    var head =
      '<div class="bubble-head">' +
      '<span class="who">' + esc(req.npcId) + "</span>" +
      "<span>intent_hint=" + esc(req.intentHint || "—") + "</span>" +
      (res.arcStage ? "<span>· " + esc(STAGE_LABEL[res.arcStage] || res.arcStage) + "</span>" : "") +
      "</div>";

    var note = "";

    var fail = step.failure
      ? '<div class="fail-note"><b>本项检查未通过</b> — ' + esc(step.failure.reason) +
        '<div style="margin-top:6px;font-family:var(--mono);font-size:11.5px;opacity:.85">' +
        esc(step.failure.error) + "</div></div>"
      : "";

    return (
      '<div class="turn" data-turn="' + index + '">' +
      '<div class="turn-q"><div class="qtext">' + esc(req.question) + "</div></div>" +
      '<div class="turn-a">' +
      '<div class="avatar">' + esc((req.npcId || "?").slice(0, 2)) + "</div>" +
      '<div class="bubble' + (step.passed ? "" : " is-failed") + '">' +
      eventChips(step) +
      head +
      '<div class="answer" data-answer>' + esc(res.answer || "") + "</div>" +
      note + fail +
      "</div></div></div>"
    );
  }

  function renderChat() {
    var run = currentRun();
    var host = el("chat-log");
    var html = "";

    for (var i = 0; i <= state.step; i++) {
      var step = run.steps[i];
      if (step.kind === "infra") html += infraCard(step, i === state.step);
      else if (step.kind === "auth") html += authCard(step, i === state.step);
      else if (step.kind === "boundary") html += boundaryCard(step, i === state.step);
      else html += turnHtml(step, i, i === state.step);
    }

    host.innerHTML = html;

    var step = currentStep();
    var threads = run.steps
      .slice(0, state.step + 1)
      .filter(function (s) { return s.result && s.result.threadId; })
      .map(function (s) { return s.result.threadId; });
    var unique = threads.filter(function (t, idx) { return threads.indexOf(t) === idx; });
    el("thread-label").textContent = unique.length ? unique.length + " 条线程" : "";

    host.scrollTop = host.scrollHeight;
    playCurrent();
  }

  /* --------------------------- 流式回放动画 -------------------------- */

  function clearTimer() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  function playCurrent() {
    clearTimer();
    var step = currentStep();
    if (step.kind !== "chat") return;

    var turn = document.querySelector('[data-turn="' + state.step + '"]');
    if (!turn) return;
    var answerEl = turn.querySelector("[data-answer]");
    var chips = turn.querySelectorAll("[data-ev]");
    var full = (step.result && step.result.answer) || "";

    answerEl.textContent = "";
    Array.prototype.forEach.call(chips, function (c) { c.classList.remove("is-on"); });

    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      Array.prototype.forEach.call(chips, function (c) { c.classList.add("is-on"); });
      answerEl.textContent = full;
      return;
    }

    var i = 0;
    // 先按协议顺序点亮事件，再流式吐字
    var reveal = setInterval(function () {
      if (i < chips.length) {
        chips[i].classList.add("is-on");
        i++;
        if (chips[i - 1].getAttribute("data-ev") === "answer_delta") {
          clearInterval(reveal);
          typewrite(answerEl, full);
        }
        return;
      }
      clearInterval(reveal);
      typewrite(answerEl, full);
    }, 190);

    function typewrite(node, text) {
      var n = 0;
      var caret = document.createElement("span");
      caret.className = "caret";
      node.appendChild(caret);
      state.timer = setInterval(function () {
        if (n >= text.length) {
          clearTimer();
          if (caret.parentNode) caret.parentNode.removeChild(caret);
          return;
        }
        n += 1;
        caret.insertAdjacentText("beforebegin", text.charAt(n - 1));
      }, 22);
    }
  }

  /* --------------------------- 元数据检查器 -------------------------- */

  function kv(label, value, cls) {
    return '<div class="kv"><dt>' + esc(label) + "</dt><dd" +
      (cls ? ' class="' + cls + '"' : "") + ">" + esc(value) + "</dd></div>";
  }

  function section(title, inner) {
    return '<div class="ins-section"><div class="ins-title">' + esc(title) + "</div>" + inner + "</div>";
  }

  function stageTrack(run, uptoIndex, activeStage) {
    var visited = [];
    for (var i = 0; i <= uptoIndex; i++) {
      var st = run.steps[i];
      var s = st.result && st.result.arcStage;
      if (s && visited.indexOf(s) === -1) visited.push(s);
    }
    return (
      '<div class="stage-track">' +
      ARC_STAGES.map(function (s) {
        var isNow = s === activeStage;
        var isDone = !isNow && visited.indexOf(s) !== -1;
        var cls = "stage-row" + (isNow ? " now" : isDone ? " done" : "");
        return '<div class="' + cls + '"><span class="stage-dot"></span>' +
          esc(STAGE_LABEL[s]) + (isDone ? " ✓" : "") + "</div>";
      }).join("") +
      "</div>"
    );
  }

  function renderInspector() {
    var run = currentRun();
    var step = currentStep();
    var host = el("inspect-body");
    var html = "";

    html += section(
      "本轮",
      '<div class="kv"><dt>步骤</dt><dd>' + esc((state.step + 1) + " / " + run.steps.length) + "</dd></div>" +
        kv("check", step.checkName, "hi") +
        kv("类型", KIND_LABEL[step.kind] || step.kind) +
        kv("结果", step.passed ? "通过" : "失败", step.passed ? "ok" : "bad") +
        kv("耗时", fmtMs(step.latencyMs))
    );

    if (step.kind === "infra") {
      html += section(
        "依赖就绪",
        '<div class="chip-row">' +
          (step.dependencies || []).map(function (d) {
            return '<span class="chip green">' + esc(d.name) + " · " + esc(d.detail) + "</span>";
          }).join("") +
          "</div>"
      );
    }

    if (step.kind === "auth") {
      html += section(
        "认证",
        kv("username", step.username) + kv("token_type", step.tokenType, "ok")
      );
    }

    if (step.kind === "boundary") {
      html += section(
        "契约拒绝",
        kv("status_code", step.result.statusCode, "bad") +
          '<div class="ins-insight" style="margin-top:8px">' + esc(step.result.detail) + "</div>"
      );
    }

    if (step.kind === "chat") {
      var res = step.result;
      html += section(
        "请求",
        kv("npc_id", step.request.npcId, "hi") +
          kv("intent_hint", step.request.intentHint || "—") +
          kv("thread_id", (step.request.threadId || "（服务端新建）").slice(0, 18) + (step.request.threadId ? "…" : ""))
      );

      var playerLines = [];
      if (step.request.player) {
        Object.keys(step.request.player).forEach(function (k) {
          var v = step.request.player[k];
          playerLines.push(k + ": " + (Array.isArray(v) ? "[" + v.join(", ") + "]" : v));
        });
      }
      html += section(
        "游戏状态",
        kv("trust", step.request.trust) +
          kv("favorability", step.request.favorability) +
          kv("annoyance", step.request.annoyance, step.request.annoyance >= 100 ? "bad" : "") +
          kv("unlock_level", step.request.storyLevel) +
          (playerLines.length
            ? '<div class="player-box" style="margin-top:8px">' + esc(playerLines.join("\n")) + "</div>"
            : "")
      );

      html += section("角色阶段", stageTrack(run, state.step, res.arcStage));

      html += section(
        "规划结果",
        kv("arc_stage", res.arcStage || "—", res.arcStage ? "hi" : "") +
          kv("dialogue_strategy", res.dialogueStrategy || "—", res.dialogueStrategy ? "hi" : "") +
          kv("plan_source", res.planSource || "—", res.planSource ? "hi" : "")
      );

      html += section(
        "检索",
        kv("retrieved_count", res.retrievedCount === null ? "—" : res.retrievedCount,
          res.retrievedCount === null ? "" : res.retrievedCount > 0 ? "ok" : "") +
          kv("source_count", res.sourceCount === null ? "—" : res.sourceCount, "")
      );

      html += section(
        "事件序列",
        res.eventTypes
          ? '<div class="chip-row">' + res.eventTypes.map(function (t) {
              return '<span class="chip blue">' + esc(t) + "</span>";
            }).join("") + "</div>"
          : '<div class="ins-empty">—</div>'
      );

      if (step.failure) {
        html += section(
          "本轮判定",
          '<div class="ins-insight">该轮检查要求回答中出现指定道具名以确认长期记忆被召回；' +
            "本轮回答为回避式台词，未命中该断言，因此本项检查未通过。</div>"
        );
      }
    }

    html += section("这一轮说明了什么", '<div class="ins-insight">' + esc(step.insight) + "</div>");

    host.innerHTML = html;
  }

  /* ---------------------------- 控制按钮 ---------------------------- */

  function renderControls() {
    var run = currentRun();
    el("btn-prev").disabled = state.step === 0;
    el("btn-next").disabled = state.step >= run.steps.length - 1;
  }

  /* ---------------------------- 声线区块 ---------------------------- */

  function renderVoices() {
    var host = el("voice-grid");
    host.innerHTML = DATA.voices
      .map(function (v) {
        var samples = v.samples
          .map(function (s) {
            var guardCls = s.guard === "passed" ? "green" : "gold";
            var guardTxt = s.guard === "passed" ? "guard: passed" : "guard: replaced";
            return (
              '<div class="sample">' +
              '<div class="sample-meta">' +
              '<span class="chip blue">' + esc(s.scenario) + "</span>" +
              '<span class="chip">' + esc(s.variant) + "</span>" +
              '<span class="chip">turn ' + esc(s.turn) + "</span>" +
              '<span class="chip ' + guardCls + '">' + esc(guardTxt) + "</span>" +
              '<span class="chip">prompt ' + esc(s.promptChars) + " 字符</span>" +
              "</div>" +
              '<div class="sample-text">' + esc(s.answer) + "</div>" +
              "</div>"
            );
          })
          .join("");
        return (
          '<article class="voice-card">' +
          '<div class="voice-head">' +
          '<span class="voice-name">' + esc(v.npcId) + "</span>" +
          '<span class="voice-role">' + esc(v.role) + "</span>" +
          "</div>" +
          '<p class="voice-tone">' + esc(v.tone) + "</p>" +
          samples +
          "</article>"
        );
      })
      .join("");
  }

  /* ------------------------------ 页签 ------------------------------ */

  function bindTabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
          var on = t === tab;
          t.classList.toggle("is-active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
        });
        Array.prototype.forEach.call(document.querySelectorAll(".panel"), function (p) {
          p.classList.toggle("is-active", p.id === "panel-" + name);
        });
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  }

  function bindControls() {
    el("btn-replay").addEventListener("click", playCurrent);
    el("btn-prev").addEventListener("click", function () {
      if (state.step > 0) { state.step -= 1; renderAll(); }
    });
    el("btn-next").addEventListener("click", function () {
      if (state.step < currentRun().steps.length - 1) { state.step += 1; renderAll(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === "ArrowRight") { el("btn-next").click(); }
      if (e.key === "ArrowLeft") { el("btn-prev").click(); }
    });
  }

  /* ------------------------------ 启动 ------------------------------ */

  /* 支持 #run=N&step=M 深链，便于直接分享某一轮 */
  function parseHash() {
    var m = /(?:^|[#&])run=(\d+)/.exec(location.hash);
    var s = /(?:^|[#&])step=(\d+)/.exec(location.hash);
    if (m) state.run = Math.min(Math.max(parseInt(m[1], 10), 0), DATA.runs.length - 1);
    if (s) {
      var max = DATA.runs[state.run].steps.length - 1;
      state.step = Math.min(Math.max(parseInt(s[1], 10), 0), max);
    }
  }

  function syncHash() {
    if (!window.history || !history.replaceState) return;
    history.replaceState(null, "", "#run=" + state.run + "&step=" + state.step);
  }

  function renderAll() {
    renderRunSwitch();
    renderStepList();
    renderChat();
    renderInspector();
    renderControls();
    syncHash();
  }

  function init() {
    if (!DATA) {
      document.body.innerHTML = '<p style="padding:40px;color:#f85149">data.js 未加载。</p>';
      return;
    }
    renderChrome();
    renderVoices();
    bindTabs();
    bindControls();
    parseHash();
    renderAll();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
