document.querySelectorAll('.submit-form').forEach(form => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = form.querySelector('.message');
    msg.textContent = '';
    const fd = new FormData(form);
    fd.append('challenge_id', form.dataset.challenge);

    const res = await fetch('/api/submit', { method: 'POST', body: fd });
    const data = await res.json();
    if (!data.ok) {
      msg.textContent = data.error || data.message || 'Er ging iets mis';
      msg.className = 'message text-sm text-red-700';
      return;
    }
if (data.correct) {
  msg.textContent = data.message || 'Goed!';
  msg.className = 'message text-sm text-green-700';
  // Confetti laden en vuren
  (async () => {
    try {
      if (!window.confetti) {
        await new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = 'https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js';
          s.onload = res; s.onerror = rej; document.head.appendChild(s);
        });
      }
      window.confetti && window.confetti();
    } catch(e) {}
  })();
  setTimeout(() => window.location.reload(), 800);
} else {
      msg.textContent = data.message || 'Niet correct';
      msg.className = 'message text-sm text-red-700';
    }
  });
});

// Deterministic color from string
function hashHsl(str){
  let h = 0;
  for (let i=0;i<str.length;i++){ h = (h*31 + str.charCodeAt(i)) >>> 0; }
  const hue = h % 360;
  const sat = 65;
  const light = 82;
  return `hsl(${hue} ${sat}% ${light}%)`;
}
const ICONS = ['🏝️','🐠','🦩','🐢','🐚','🌊','🪙','🧭','🌴','🐳'];
function pickIcon(str){
  let s = 0;
  for (let i=0;i<str.length;i++){ s = (s*33 + str.charCodeAt(i)) >>> 0; }
  return ICONS[s % ICONS.length];
}
function decorateTeamBadges(selector='.team-badge'){
  document.querySelectorAll(selector).forEach(el => {
    const name = el.dataset.team || el.textContent.trim();
    const color = el.dataset.color || hashHsl(name);
    el.style.background = color;
    el.classList.add('pill');
    const ic = document.createElement('span');
    ic.className = 'icon';
    ic.textContent = pickIcon(name);
    if(!el.querySelector('.icon')) el.prepend(ic);
  });
}
document.addEventListener('DOMContentLoaded', () => { decorateTeamBadges(); });
