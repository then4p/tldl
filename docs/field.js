// Waveform bars behind the phone that flatten into lines of text when the
// transcript arrives. Configured on <canvas id="field">:
//   data-color="--css-var"  color token to draw with (default --accent)
//   data-alpha="0.22"       opacity of the bars (lines get +0.1)
//   data-bar="0.45"         bar width as a share of the spacing between bars
(() => {
  const cv = document.getElementById("field");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const colorVar = cv.dataset.color || "--accent";
  const alpha = parseFloat(cv.dataset.alpha || "0.22");
  const barShare = parseFloat(cv.dataset.bar || "0.45");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const N = 56, ROWS = 14, PER_ROW = N / ROWS;
  let seed = 11;
  const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  const amp = Array.from({ length: N }, (_, i) => 0.25 + 0.75 * rnd() * (0.5 + 0.5 * Math.sin(i / 4) ** 2));
  // Text layout: ROWS lines of PER_ROW "words" each; the last line is shorter.
  const words = [];
  for (let r = 0; r < ROWS; r++) {
    const ws = Array.from({ length: PER_ROW }, () => 0.5 + rnd());
    const lineLen = r === ROWS - 1 ? 0.45 : 0.82 + 0.18 * rnd();
    const sum = ws.reduce((a, b) => a + b, 0);
    let x = 0;
    for (const w of ws) {
      const width = (w / sum) * lineLen;
      words.push({ x: x + 0.012, w: width - 0.024, r });
      x += width;
    }
  }
  let t = reduce ? 1 : 0, target = t, w = 0, h = 0, last = 0, raf = 0;
  const ease = (x) => x * x * (3 - 2 * x);

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    w = cv.clientWidth; h = cv.clientHeight;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw(performance.now());
  }
  function draw(now) {
    // Resolve through CSS so any color syntax works (e.g. light-dark()).
    cv.style.color = `var(${colorVar})`;
    const color = getComputedStyle(cv).color;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = color;
    const pad = Math.min(24, w * 0.04), top = h * 0.12, rowGap = (h * 0.76) / ROWS, lineH = Math.max(3, rowGap * 0.32);
    const barGap = (w - pad * 2) / N, barW = Math.max(2, barGap * barShare);
    for (let i = 0; i < N; i++) {
      const k = ease(Math.min(1, Math.max(0, t * 1.6 - (i / N) * 0.6)));
      const wobble = reduce ? 1 : 0.65 + 0.35 * Math.sin(now / 260 + i * 0.7);
      const bh = amp[i] * h * 0.62 * wobble;
      const bar = { x: pad + i * barGap + (barGap - barW) / 2, y: h / 2 - bh / 2, w: barW, h: bh };
      const wd = words[i];
      const txt = { x: pad + wd.x * (w - pad * 2), y: top + wd.r * rowGap, w: wd.w * (w - pad * 2), h: lineH };
      const x = bar.x + (txt.x - bar.x) * k, y = bar.y + (txt.y - bar.y) * k;
      const rw = bar.w + (txt.w - bar.w) * k, rh = bar.h + (txt.h - bar.h) * k;
      ctx.globalAlpha = alpha + 0.1 * k;
      ctx.beginPath();
      ctx.roundRect(x, y, rw, rh, Math.min(rw, rh) / 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    t += Math.sign(target - t) * Math.min(Math.abs(target - t), dt / 1.1);
    draw(now);
    raf = requestAnimationFrame(frame);
  }
  document.addEventListener("tldl:stage", (e) => {
    target = e.detail === "done" ? 1 : 0;
    if (reduce) { t = target; draw(0); }
  });
  new ResizeObserver(resize).observe(cv);
  new MutationObserver(() => draw(performance.now())).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => draw(performance.now()));
  if (!reduce) { last = performance.now(); raf = requestAnimationFrame(frame); }
})();
