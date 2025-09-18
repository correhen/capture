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
      setTimeout(() => window.location.reload(), 800);
    } else {
      msg.textContent = data.message || 'Niet correct';
      msg.className = 'message text-sm text-red-700';
    }
  });
});
