// theme toggle
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('themeToggle');
  const body = document.body;
  // remember preference in localStorage
  const saved = localStorage.getItem('theme');
  if (saved === 'dark') body.classList.add('theme-dark');

  const updateButton = () => {
    toggle.textContent = body.classList.contains('theme-dark') ? 'Light' : 'Dark';
  };
  updateButton();

  toggle.addEventListener('click', () => {
    body.classList.toggle('theme-dark');
    localStorage.setItem('theme', body.classList.contains('theme-dark') ? 'dark' : 'light');
    updateButton();
    // swap logo if you have a light version
    const logo = document.querySelector('.logo-img');
    if (logo) {
      logo.src = body.classList.contains('theme-dark') ? '/static/logo-light.png' : '/static/logo-dark.png';
    }
  });

  // small fade-in for cards
  document.querySelectorAll('.card, .hero-content').forEach((el, i) => {
    el.style.opacity = 0;
    setTimeout(() => {
      el.style.transition = 'all .6s ease';
      el.style.opacity = 1;
      el.style.transform = 'translateY(0)';
    }, 100 * i);
  });
});
