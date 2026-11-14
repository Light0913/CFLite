// CFLite 前端脚本（原生 JS，无外部依赖）
(function () {
  "use strict";

  window.verdictClass = function (v) {
    if (!v) return "verdict-gray";
    if (v === "AC" || v === "PRETESTS_PASSED") return "verdict-ac";
    if (["WA", "PE", "RE", "OLE", "HACKED"].indexOf(v) >= 0) return "verdict-fail";
    if (v === "TLE" || v === "MLE") return "verdict-tle";
    if (v === "CE") return "verdict-ce";
    if (v === "SE") return "verdict-se";
    if (v === "CANCELLED" || v === "SKIPPED") return "verdict-gray";
    if (v === "WAITING" || v === "JUDGING") return "verdict-waiting";
    return "verdict-gray";
  };

  window.verdictName = function (v) {
    var map = {
      AC: "Accepted", PRETESTS_PASSED: "Pretests Passed", WA: "Wrong Answer",
      PE: "Presentation Error", RE: "Runtime Error", OLE: "Output Limit Exceeded",
      TLE: "Time Limit Exceeded", MLE: "Memory Limit Exceeded", CE: "Compilation Error",
      SE: "System Error", HACKED: "Hacked", CANCELLED: "Cancelled",
      WAITING: "Waiting", JUDGING: "Judging", SKIPPED: "Skipped",
      SUCCESS: "Successful Hack", FAIL: "Unsuccessful Hack", INVALID_INPUT: "Invalid Input"
    };
    if (!v) return "—";
    return map[v] || v;
  };

  window.badgeClass = function (v) {
    var c = window.verdictClass(v);
    if (c === "verdict-ac") return "badge-ac";
    if (c === "verdict-fail") return "badge-fail";
    if (c === "verdict-tle") return "badge-tle";
    if (c === "verdict-ce") return "badge-ce";
    if (c === "verdict-se") return "badge-se";
    return "badge-gray";
  };

  // CodeMirror 替代：textarea + tab 键缩进
  document.addEventListener("keydown", function (e) {
    if (e.key === "Tab" && e.target && e.target.classList && e.target.classList.contains("code-editor")) {
      e.preventDefault();
      var start = e.target.selectionStart, end = e.target.selectionEnd;
      e.target.value = e.target.value.substring(0, start) + "    " + e.target.value.substring(end);
      e.target.selectionStart = e.target.selectionEnd = start + 4;
    }
  });

  // 内联查看代码：管理员/本人显示代码块，选手（锁题防抄袭）显示图片
  window.showSource = function (sid, containerId) {
    var el = document.getElementById(containerId);
    if (!el) return;
    el.textContent = "加载中…";
    fetch("/api/submissions/" + sid + "/source", { headers: { "Accept": "text/plain" } })
      .then(function (r) {
        var ct = r.headers.get("Content-Type") || "";
        if (ct.indexOf("image/") === 0) {
          return r.blob().then(function (b) {
            var img = document.createElement("img");
            img.src = URL.createObjectURL(b);
            img.style.maxWidth = "100%";
            img.style.border = "1px solid var(--border)";
            el.innerHTML = "";
            el.appendChild(img);
          });
        }
        return r.text().then(function (t) {
          var pre = document.createElement("pre");
          pre.className = "code-block";
          pre.textContent = t;
          el.innerHTML = "";
          el.appendChild(pre);
        });
      })
      .catch(function () { el.textContent = "加载失败"; });
  };

  // 通知铃铛
  function initBell() {
    var bell = document.getElementById("notif-bell");
    if (!bell) return;
    var badge = document.getElementById("notif-badge");
    var dd = document.getElementById("notif-dropdown");
    function refresh() {
      fetch("/api/notifications/unread-count").then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.count > 0) { badge.style.display = "block"; badge.textContent = d.count > 99 ? "99+" : d.count; }
          else badge.style.display = "none";
        }).catch(function () {});
    }
    function loadList() {
      fetch("/api/notifications").then(function (r) { return r.json(); }).then(function (items) {
        if (!dd) return;
        if (!items.length) { dd.innerHTML = '<div class="empty">暂无通知</div>'; return; }
        dd.innerHTML = items.map(function (n) {
          var t = n.type === "hacked" ? "被 Hack" : (n.type === "system_rejudged" ? "重测结果" : "通知");
          var body = "";
          if (n.type === "hacked" && n.payload) {
            body = "提交 #" + n.payload.submission_id + " 被 " + n.payload.hacker_name + " Hack";
          } else if (n.type === "system_rejudged" && n.payload) {
            body = "提交 #" + n.payload.submission_id + " 重测为 " + n.payload.verdict;
          }
          return '<div class="item ' + (n.is_read ? "" : "unread") + '">' +
            '<div>' + t + '</div><div class="muted">' + body + '</div></div>';
        }).join("");
      }).catch(function () {});
      fetch("/api/notifications/read", { method: "POST" }).then(refresh).catch(function () {});
    }
    bell.addEventListener("click", function (e) {
      e.stopPropagation();
      if (dd) { dd.classList.toggle("open"); if (dd.classList.contains("open")) loadList(); }
    });
    document.addEventListener("click", function () { if (dd) dd.classList.remove("open"); });
    refresh();
    if (window.EventSource) {
      var es = new EventSource("/api/notifications/stream");
      es.onerror = function () { es.close(); };
      es.addEventListener("notification", function () { refresh(); });
      es.addEventListener("unread_count", function (e) {
        var d = JSON.parse(e.data);
        if (d.count > 0) { badge.style.display = "block"; badge.textContent = d.count; }
        else badge.style.display = "none";
      });
    } else {
      setInterval(refresh, 30000);
    }
  }

  // 提交结果 SSE
  function initSubmissionStream() {
    var el = document.getElementById("live-status");
    var sid = el && el.getAttribute("data-sid");
    if (!el || !sid) return;
    if (!window.EventSource) {
      // 降级：2s 轮询
      setInterval(function () {
        fetch("/api/submissions/" + sid + "/final-fragment").then(function (r) {
          if (r.ok) return r.text();
          throw new Error();
        }).then(function (html) { el.outerHTML = html; }).catch(function () {});
      }, 2000);
      return;
    }
    var es = new EventSource("/api/submissions/" + sid + "/stream");
    es.onerror = function () { es.close(); };
    es.addEventListener("test_start", function (e) {
      var ev = JSON.parse(e.data);
      var tr = el.querySelector('tr[data-stage="' + ev.stage + '"][data-name="' + ev.name + '"]') ||
               el.querySelector('tr[data-stage="' + ev.stage + '"].waiting');
      if (tr) {
        tr.dataset.name = ev.name; tr.className = "current";
        tr.innerHTML = '<td>' + ev.name + '</td><td class="verdict-waiting">Judging</td><td>—</td><td>—</td><td></td>';
      }
    });
    es.addEventListener("test_done", function (e) {
      var ev = JSON.parse(e.data);
      var tr = el.querySelector('tr[data-stage="' + ev.stage + '"][data-name="' + ev.name + '"]') ||
               el.querySelector('tr[data-stage="' + ev.stage + '"].waiting') ||
               el.querySelector('tr[data-stage="' + ev.stage + '"].current');
      if (!tr) return;
      tr.dataset.name = ev.name; tr.className = "";
      tr.innerHTML = '<td>' + ev.name + '</td>' +
        '<td class="' + verdictClass(ev.verdict) + '">' + verdictName(ev.verdict) + '</td>' +
        '<td>' + (ev.time_ms != null ? ev.time_ms : "—") + ' ms</td>' +
        '<td>' + (ev.memory_kb != null ? ev.memory_kb : "—") + ' KB</td>' +
        '<td class="checker-comment">' + (ev.checker_comment || "") + '</td>';
    });
    es.addEventListener("final", function (e) {
      var ev = JSON.parse(e.data);
      if (ev.stage !== "system_tests" && ev.stage !== "pretests") return;
      es.close();
      (function load(n) {
        fetch("/api/submissions/" + sid + "/final-fragment").then(function (r) {
          if (!r.ok) throw new Error("not ready");
          return r.text();
        }).then(function (html) { el.outerHTML = html; }).catch(function () {
          if (n < 6) setTimeout(function () { load(n + 1); }, 400);
        });
      })(0);
    });
  }

  // Hack 结果 SSE
  function initHackStream() {
    var el = document.getElementById("hack-status");
    var hid = el && el.getAttribute("data-hid");
    if (!el || !hid) return;
    if (!window.EventSource) { setInterval(function () { location.reload(); }, 3000); return; }
    var es = new EventSource("/api/hacks/" + hid + "/stream");
    es.onerror = function () { es.close(); };
    es.addEventListener("final", function (e) {
      es.close();
      // 已经是最终状态就不再刷新，避免无限刷新
      if (el.getAttribute("data-final") !== "1") location.reload();
    });
  }

  // 注册状态 5s 轮询（登录/注册页）
  function initRegistrationPolling() {
    var el = document.getElementById("registration-status");
    if (!el) return;
    var last = el.getAttribute("data-enabled") === "true";
    setInterval(function () {
      fetch("/api/registration/status").then(function (r) { return r.json(); }).then(function (d) {
        if (d.enabled !== last) location.reload();
      }).catch(function () {});
    }, 5000);
  }

  // 为 Markdown 代码块（样例输入/输出等）添加复制按钮
  function initCodeCopy() {
    document.querySelectorAll(".md pre, pre.code-block").forEach(function (pre) {
      if (pre.parentNode && pre.parentNode.classList && pre.parentNode.classList.contains("code-wrap")) return;
      var wrap = document.createElement("div");
      wrap.className = "code-wrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary btn-sm copy-btn";
      btn.textContent = "复制";
      btn.addEventListener("click", function () {
        var text = pre.innerText;
        function done() {
          btn.textContent = "已复制";
          setTimeout(function () { btn.textContent = "复制"; }, 1500);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () { fallback(); });
        } else {
          fallback();
        }
        function fallback() {
          var ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy"); } catch (e) {}
          document.body.removeChild(ta);
          done();
        }
      });
      wrap.appendChild(btn);
    });
  }

  // 管理员「重载」：重扫数据 + 清模板缓存；若代码有更新则自动重启服务（run.bat 拉起）
  function initReload() {
    var btn = document.getElementById("reload-btn");
    if (!btn) return;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      var old = btn.textContent;
      btn.disabled = true;
      btn.textContent = "更新中…";
      fetch("/api/admin/reload", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.errors && d.errors.length) {
            alert("重载完成，但有错误：\n" + d.errors.join("\n"));
          }
          if (d && d.restart) {
            btn.textContent = "服务重启中…";
            waitForServer(function () {
              location.href = location.pathname + "?r=" + Date.now();
            });
          } else {
            location.href = location.pathname + "?r=" + Date.now();
          }
        })
        .catch(function () {
          btn.disabled = false;
          btn.textContent = old;
          alert("更新失败，请查看服务端日志");
        });
    });
  }

  // 等待服务重启完成（轮询首页，最多约 60 秒）
  function waitForServer(done) {
    var tries = 0;
    function tick() {
      tries++;
      fetch("/", { cache: "no-store" }).then(function (r) {
        if (r.ok) done();
        else retry();
      }).catch(retry);
    }
    function retry() {
      if (tries > 120) { done(); return; }
      setTimeout(tick, 500);
    }
    setTimeout(tick, 800);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initBell();
    initSubmissionStream();
    initHackStream();
    initRegistrationPolling();
    initCodeCopy();
    initReload();
  });
})();
