(function () {
  'use strict';

  /* ---------- элементы ---------- */
  const ta     = document.getElementById('prompt');
  const form   = document.getElementById('composer');
  const header = document.querySelector('.site-header');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const canTilt      = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  const EXAMPLES = [
    'тихое место у моря, где варят вкусный кофе',
    'дом с садом в старом городе, до 120 €',
    'вид на горы и никаких толп туристов',
    'маленький отель, где хозяин помнит по имени'
  ];

  /* общие ссылки для index.js */
  window.orbita = { ta, form, header, reduceMotion, canTilt, EXAMPLES };

  /* ---------- шапка ---------- */
  window.addEventListener('scroll', () => {
    header.classList.toggle('scrolled', window.scrollY > 8);
  }, { passive: true });

  /* ---------- «печатающийся» placeholder ---------- */
  let exIndex = 0, charIndex = 0, deleting = false;

  function paintPlaceholder() {
    if (ta.value.length === 0) {
      ta.placeholder = EXAMPLES[exIndex].slice(0, charIndex);
    }
  }

  function typeLoop() {
    const target = EXAMPLES[exIndex];

    if (deleting) {
      charIndex--;
      if (charIndex <= 0) {
        charIndex = 0;
        deleting = false;
        exIndex = (exIndex + 1) % EXAMPLES.length;
        paintPlaceholder();
        return setTimeout(typeLoop, 420);
      }
      paintPlaceholder();
      return setTimeout(typeLoop, 18);
    }

    charIndex++;
    paintPlaceholder();
    if (charIndex >= target.length) {
      deleting = true;
      return setTimeout(typeLoop, 2300);
    }
    return setTimeout(typeLoop, 42 + Math.random() * 45);
  }

  if (!reduceMotion) {
    setTimeout(typeLoop, 700);
  } else {
    ta.placeholder = EXAMPLES[0];
  }

  /* ---------- авто-высота textarea ---------- */
  function autosize() {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 132) + 'px';
  }
  ta.addEventListener('input', autosize);

  window.orbita.autosize = autosize;

  /* Enter отправляет, Shift+Enter — перенос строки */
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
})();