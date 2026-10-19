// Demo page script: theme toggle + animated Telegram chat.
// The page can react to the animation via the "tldl:stage" event on document
// (detail: "reset" | "voice" | "status" | "done" | "checked") and [data-step] elements.

// Theme: "auto" follows the OS (live); "light"/"dark" pin it. Remembered per browser.
(() => {
  const btn = document.getElementById("theme");
  if (!btn) return;
  const root = document.documentElement;
  const label = document.getElementById("theme-label") || btn;
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const ORDER = ["auto", "light", "dark"];
  let stored = null;
  try { stored = localStorage.getItem("tldl-theme"); } catch {}
  let mode = ORDER.includes(stored) ? stored : (root.getAttribute("data-theme") || "auto");

  function render() {
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    const effective = mode === "auto" ? (media.matches ? "dark" : "light") : mode;
    label.textContent = mode === "auto" ? `Auto · ${effective}` : mode[0].toUpperCase() + mode.slice(1);
    btn.setAttribute("aria-label", `Theme: ${label.textContent}. Switch theme`);
  }
  btn.addEventListener("click", () => {
    mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
    try { localStorage.setItem("tldl-theme", mode); } catch {}
    render();
  });
  media.addEventListener("change", () => { if (mode === "auto") render(); });
  render();
})();

(() => {
  const $ = (id) => document.getElementById(id);
  const voice = $("voice"), bot = $("bot"), botText = $("bot-text"), edited = $("edited");
  if (!voice || !bot || !botText) return;
  const status = $("tg-status");
  const steps = [...document.querySelectorAll("[data-step]")];
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Plain text, or [quick, checked]: what Parakeet hears first, and Whisper's correction.
  const TRANSCRIPT = [
    "Hey, it's me. I'm at the shop and they're out of the ", ["goat milk", "oat milk"], " you like, " +
    "there's only the barista one or the unsweetened one. Which one do you want? " +
    "Oh, and can you call the landlord about the ", ["eating", "heating"], "? It's still not working " +
    "in the bedroom. Okay, see you tonight, bye!",
  ];
  const STATUS = ["📥 Received", "✍️ Transcribing…"];
  const check = $("bot-check");

  // Waveform: fixed pseudo-random bar heights so it looks like speech.
  const wave = $("wave");
  if (wave && !wave.children.length) {
    let seed = 7;
    for (let i = 0; i < 38; i++) {
      seed = (seed * 9301 + 49297) % 233280;
      const h = 18 + Math.round((seed / 233280) * 82 * (0.55 + 0.45 * Math.sin(i / 3.2) ** 2));
      const bar = document.createElement("i");
      bar.style.height = h + "%";
      wave.appendChild(bar);
    }
  }

  const emit = (stage) => document.dispatchEvent(new CustomEvent("tldl:stage", { detail: stage }));
  function setStep(n) {
    steps.forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
  }
  function setStatus(text, typing) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("typing", typing);
  }
  function setText(text, isEdit) {
    botText.textContent = text;
    botText.classList.remove("swap");
    void botText.offsetWidth;
    botText.classList.add("swap");
    if (edited) edited.hidden = !isEdit;
  }
  // pass 0: the quick transcript; pass 1: corrected words highlighted.
  function setTranscript(pass) {
    botText.replaceChildren(...TRANSCRIPT.map((part) => {
      if (typeof part === "string") return part;
      if (!pass) return part[0];
      const mark = document.createElement("mark");
      mark.className = "fix";
      mark.textContent = part[1];
      return mark;
    }));
    if (check) check.hidden = pass === 1;
  }
  function pop(el) {
    el.hidden = false;
    el.classList.remove("pop");
    void el.offsetWidth;
    el.classList.add("pop");
  }
  function showFinal() {
    voice.hidden = false; bot.hidden = false;
    setTranscript(1);
    if (edited) edited.hidden = false;
    setStatus("bot", false);
    setStep(3);
    emit("done");
  }

  let run = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function play() {
    const id = ++run;
    const alive = async (ms) => { await sleep(ms); return id === run; };

    voice.hidden = true; bot.hidden = true;
    if (edited) edited.hidden = true;
    if (check) check.hidden = true;
    setStatus("bot", false); setStep(0); emit("reset");

    if (!await alive(700)) return;
    pop(voice); setStep(1); emit("voice");

    if (!await alive(1300)) return;
    setText(STATUS[0], false); pop(bot); setStep(2); emit("status");
    setStatus("typing…", true);

    if (!await alive(700)) return;
    setText(STATUS[1], true);

    // The quick transcript arrives within seconds...
    if (!await alive(1100)) return;
    setText("", true); setTranscript(0); setStep(3); emit("done");

    // ...and a moment later the double-check fixes what Parakeet misheard.
    if (!await alive(3400)) return;
    setTranscript(1); emit("checked");
    setStatus("bot", false);

    if (!await alive(7500)) return;
    play();
  }

  document.querySelectorAll("[data-replay]").forEach((b) =>
    b.addEventListener("click", () => (reduce ? showFinal() : play()))
  );

  // Start from the empty chat (no spoiler); with reduced motion, show the result.
  if (reduce) showFinal(); else play();
})();
