document.addEventListener('DOMContentLoaded', () => {
  // theme toggle already exists — keep it
  // show flash messages as small toasts?
  document.querySelectorAll('.addcart-form').forEach(f => {
    f.addEventListener('submit', (e) => {
      // let the form submit normally (server will flash and redirect)
      // but you can show a small quick UI feedback:
      const btn = f.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Adding...';
      }
    });
  });
});
