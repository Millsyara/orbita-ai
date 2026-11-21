(function () {
  'use strict';

  /* ---------- показать / скрыть пароль ---------- */
  document.querySelectorAll('[data-toggle-password]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = btn.parentElement.querySelector('input');
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.classList.toggle('is-visible', show);
      btn.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
      input.focus({ preventScroll: true });
    });
  });

  /* ---------- проверка совпадения паролей (на странице регистрации) ---------- */
  var pw = document.getElementById('password');
  var cpw = document.getElementById('confirm_password');
  if (pw && cpw) {
    var syncConfirm = function () {
      if (cpw.value && pw.value !== cpw.value) {
        cpw.setCustomValidity('Пароли не совпадают');
      } else {
        cpw.setCustomValidity('');
      }
    };
    pw.addEventListener('input', syncConfirm);
    cpw.addEventListener('input', syncConfirm);

    // если браузер не поддержал setCustomValidity — на всякий случай
    if (typeof cpw.setCustomValidity !== 'function') {
      cpw.addEventListener('input', function () {
        cpw.classList.toggle('is-invalid', !!cpw.value && pw.value !== cpw.value);
      });
    }
  }

  /* ---------- защита от двойного сабмита ---------- */
  document.querySelectorAll('form.auth-form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('.btn-primary');
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.dataset.originalText = btn.textContent;
      btn.textContent = 'Подождите…';
    });
  });
})();