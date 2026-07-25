// Minimal vanilla chat client. All agent logic lives server-side in Django +
// Gemini (see docs/adr/0002). This only sends messages/files and renders bubbles.
(function () {
  "use strict";
  var chat = document.querySelector(".chat");
  if (!chat) return;
  var API = chat.dataset.api;
  var UPLOAD = chat.dataset.upload;

  var transcript = document.getElementById("transcript");
  var form = document.getElementById("composer");
  var input = document.getElementById("msgInput");
  var sendBtn = document.getElementById("sendBtn");
  var attachBtn = document.getElementById("attachBtn");
  var attachMenu = document.getElementById("attachMenu");
  var fileInput = document.getElementById("fileInput");
  var docType = "both";

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  function fmt(s) {
    // Escape first, then allow **bold** and newlines only.
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }
  function scrollDown() { transcript.scrollTop = transcript.scrollHeight; }

  function addMsg(role, text) {
    var d = document.createElement("div");
    d.className = "msg " + role;
    d.innerHTML = fmt(text);
    transcript.appendChild(d);
    scrollDown();
    return d;
  }
  function typing() {
    var d = document.createElement("div");
    d.className = "msg bot typing";
    d.innerHTML = "<span></span><span></span><span></span>";
    transcript.appendChild(d);
    scrollDown();
    return d;
  }
  function hideOpening() {
    var o = document.getElementById("opening");
    if (o) o.remove();
  }
  function busy(b) {
    input.disabled = b;
    sendBtn.disabled = b;
  }
  function handle(data) {
    (data.messages || []).forEach(function (m) { addMsg("bot", m.text); });
    if (data.done && data.redirect) {
      setTimeout(function () { window.location = data.redirect; }, 1000);
    }
  }

  // Reformat the server-rendered opening bubble(s) for bold/newlines.
  Array.prototype.forEach.call(transcript.querySelectorAll(".msg"), function (el) {
    el.innerHTML = fmt(el.textContent);
  });
  scrollDown();

  async function send(message) {
    hideOpening();
    addMsg("user", message);
    busy(true);
    var t = typing();
    try {
      var r = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ message: message }),
      });
      var data = await r.json();
      t.remove();
      handle(data);
    } catch (e) {
      t.remove();
      addMsg("bot", "Sorry — something went wrong. Mind trying that again?");
    }
    busy(false);
    input.focus();
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value.trim();
    if (!v) return;
    input.value = "";
    send(v);
  });

  // Opening quick replies.
  transcript.addEventListener("click", function (e) {
    var b = e.target.closest(".qchip");
    if (!b) return;
    hideOpening();
    if (b.dataset.action === "import") {
      docType = "both";
      fileInput.click();
    } else {
      send("I'd like to just chat.");
    }
  });

  // Attach (+) menu.
  attachBtn.addEventListener("click", function () {
    attachMenu.hidden = !attachMenu.hidden;
  });
  attachMenu.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-doc]");
    if (!b) return;
    docType = b.dataset.doc;
    attachMenu.hidden = true;
    fileInput.click();
  });
  document.addEventListener("click", function (e) {
    if (!attachMenu.hidden && !e.target.closest(".attach-wrap")) attachMenu.hidden = true;
  });

  fileInput.addEventListener("change", async function () {
    if (!fileInput.files.length) return;
    var fd = new FormData();
    fd.append("docType", docType);
    for (var i = 0; i < fileInput.files.length; i++) fd.append("document", fileInput.files[i]);
    var label = fileInput.files.length > 1
      ? fileInput.files.length + " files"
      : fileInput.files[0].name;
    hideOpening();
    addMsg("user", "📎 " + label);
    busy(true);
    var t = typing();
    try {
      var r = await fetch(UPLOAD, {
        method: "POST",
        headers: { "X-CSRFToken": csrf() },
        body: fd,
      });
      var data = await r.json();
      t.remove();
      handle(data);
    } catch (e) {
      t.remove();
      addMsg("bot", "I had trouble reading that file. Want to try again, or just tell me?");
    }
    fileInput.value = "";
    busy(false);
    input.focus();
  });

  input.focus();
})();
