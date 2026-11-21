(function () {
  'use strict';

  /* ------------------------------------------------------------
     DOM
  ------------------------------------------------------------ */
  const thread         = document.getElementById('thread');
  const composer       = document.getElementById('composer');
  const prompt         = document.getElementById('prompt');
  const quickReplies   = document.getElementById('quickReplies');

  const phaseBadge     = document.getElementById('phaseBadge');
  const prefsEl        = document.getElementById('prefs');
  const panelShortlist = document.getElementById('panelShortlist');
  const shortlistEl    = document.getElementById('shortlist');
  const findOthersBtn  = document.getElementById('findOthersBtn');
  const panelPending   = document.getElementById('panelPending');
  const pendingEl      = document.getElementById('pending');
  const panelConfirmed = document.getElementById('panelConfirmed');
  const confirmedEl    = document.getElementById('confirmed');
  const resetBtn       = document.getElementById('resetBtn');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let busy = false;
  let userNearBottom = true;
  let prevShortlistLen = 0;

  /* ------------------------------------------------------------
     API
  ------------------------------------------------------------ */
  async function api(url, opts) {
    const res = await fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
    }, opts || {}));

    let data = null;
    try { data = await res.json(); } catch (_) { /* ignore */ }

    if (!res.ok) {
      const msg = (data && (data.error || data.message)) || `HTTP ${res.status}`;
      const err = new Error(msg);
      err.status = res.status;
      err.data   = data;
      throw err;
    }
    return data;
  }
  const getJson  = (url)       => api(url, { method: 'GET' });
  const postJson = (url, body) => api(url, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });

  /* ------------------------------------------------------------
     THREAD
  ------------------------------------------------------------ */
  thread.addEventListener('scroll', () => {
    const gap = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    userNearBottom = gap < 80;
  }, { passive: true });

  function scrollToBottom(smooth) {
    if (!userNearBottom) return;
    const behavior = (!smooth || reduceMotion) ? 'auto' : 'smooth';
    thread.scrollTo({ top: thread.scrollHeight, behavior });
  }

  function addBubble(role, text) {
    const div = document.createElement('div');
    div.className = `bubble bubble-${role}`;
    div.textContent = text;
    thread.appendChild(div);
    scrollToBottom(true);
    return div;
  }

  function addThinking() {
    const el = document.createElement('div');
    el.className = 'bubble bubble-thinking';
    el.innerHTML = '<span class="thinking-dots" aria-label="агент печатает">' +
                   '<span></span><span></span><span></span></span>';
    thread.appendChild(el);
    scrollToBottom(true);
    return el;
  }

  function removeEl(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  /* ------------------------------------------------------------
     STATE RENDER
  ------------------------------------------------------------ */
  const PHASE_LABELS = {
    collect: 'сбор',
    search:  'поиск',
    book:    'выбор',
    done:    'готово',
  };
  const PLACEHOLDERS = {
    collect: 'Куда, когда и с кем едете?',
    search:  'Можно уточнить детали…',
    book:    'Выберите отель справа или спросите про него',
    done:    'Спасибо за бронь! Можно начать заново',
  };

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso + 'T00:00:00');
      return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
    } catch (_) { return iso; }
  }

  function fmtPrice(amount, currency) {
    if (amount == null) return '—';
    const sym = { RUB: '₽', USD: '$', EUR: '€', GBP: '£' }[currency] || (currency || '');
    try {
      return new Intl.NumberFormat('ru-RU').format(amount) + ' ' + sym;
    } catch (_) {
      return amount + ' ' + sym;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function renderPhase(state) {
    const p = state.phase || 'collect';
    phaseBadge.textContent = PHASE_LABELS[p] || p;
    phaseBadge.dataset.phase = p;
    if (!prompt.value) prompt.placeholder = PLACEHOLDERS[p] || PLACEHOLDERS.collect;
    // quick replies — только на старте
    quickReplies.hidden = (p !== 'collect');
    // в "готово" нельзя запросить ещё
    findOthersBtn.disabled = (p === 'done');
  }

  function renderPrefs(state) {
    const p = state.preferences || {};
    const rows = [
      { label: 'Направление', value: p.destination },
      { label: 'Даты', value: (p.check_in && p.check_out)
          ? fmtDate(p.check_in) + ' — ' + fmtDate(p.check_out)
          : (p.check_in ? 'с ' + fmtDate(p.check_in) : null) },
      { label: 'Бюджет', value: (p.max_price != null && p.currency)
          ? fmtPrice(p.max_price, p.currency) + ' / ночь' : null },
      { label: 'Номеров', value: p.rooms_amount != null ? String(p.rooms_amount) : null },
      { label: 'Дети', value: (p.children_ages && p.children_ages.length)
          ? p.children_ages.join(', ') + ' лет' : null },
    ];
    prefsEl.innerHTML = rows.map(it => {
      const empty = (it.value == null || it.value === '');
      return '<div class="pref' + (empty ? ' pref-empty' : '') + '">' +
             '<dt>' + it.label + '</dt>' +
             '<dd>' + (empty ? '—' : escapeHtml(it.value)) + '</dd>' +
             '</div>';
    }).join('');
  }

  function renderShortlist(state) {
    const list = state.shortlist || [];
    // скрываем после подтверждённой брони — чтобы не путать
    if (!list.length || state.confirmed_booking) {
      panelShortlist.hidden = true;
      return;
    }
    panelShortlist.hidden = false;

    shortlistEl.innerHTML = list.map(h => {
      const rating = h.rating != null ? Number(h.rating).toFixed(1) : '—';
      const amenities = (h.amenities && h.amenities.length)
        ? '<div class="hotel-amenities">' + escapeHtml(h.amenities.join(' · ')) + '</div>'
        : '';
      return '<div class="hotel-card">' +
        '<div class="hotel-card-head">' +
          '<h3 class="hotel-name">' + escapeHtml(h.name || h.id) + '</h3>' +
          '<span class="hotel-rating">★ ' + rating + '</span>' +
        '</div>' +
        '<div class="hotel-meta">' +
          '<span class="hotel-price">' + fmtPrice(h.price, h.currency) +
            ' <small>/ ночь</small></span>' +
          '<button class="hotel-select" type="button" data-hotel-id="' +
            escapeHtml(h.id) + '">Выбрать</button>' +
        '</div>' + amenities +
      '</div>';
    }).join('');

    shortlistEl.querySelectorAll('.hotel-select').forEach(btn => {
      btn.addEventListener('click', () => selectHotel(btn.dataset.hotelId));
    });
  }

  function renderPending(state) {
    const pb = state.pending_booking;
    if (!pb) { panelPending.hidden = true; return; }
    panelPending.hidden = false;

    const hotel = (state.shortlist || []).find(h => h.id === pb.hotel_id);
    const hotelLabel = hotel ? hotel.name : pb.hotel_id;
    const kidsRow = (pb.children_ages && pb.children_ages.length)
      ? '<div class="pending-row"><dt>Дети</dt><dd>' +
        pb.children_ages.join(', ') + ' лет</dd></div>'
      : '';

    pendingEl.innerHTML =
      '<div class="pending-row"><dt>Отель</dt><dd>' + escapeHtml(hotelLabel) + '</dd></div>' +
      '<div class="pending-row"><dt>Заезд</dt><dd>' + fmtDate(pb.check_in) + '</dd></div>' +
      '<div class="pending-row"><dt>Выезд</dt><dd>' + fmtDate(pb.check_out) + '</dd></div>' +
      '<div class="pending-row"><dt>Номеров</dt><dd>' + pb.rooms + '</dd></div>' +
      kidsRow +
      '<div class="pending-total"><span>Итого</span>' +
        '<span>' + fmtPrice(pb.total_price, pb.currency) + '</span></div>' +
      '<div class="pending-actions">' +
        '<button type="button" data-action="confirm">Подтвердить</button>' +
        '<button type="button" class="secondary" data-action="cancel">Отменить</button>' +
      '</div>';

    pendingEl.querySelector('[data-action="confirm"]')
      .addEventListener('click', confirmBooking);
    pendingEl.querySelector('[data-action="cancel"]')
      .addEventListener('click', cancelBooking);
  }

  function renderConfirmed(state) {
    const cb = state.confirmed_booking;
    if (!cb) { panelConfirmed.hidden = true; return; }
    panelConfirmed.hidden = false;

    const hotel = (state.shortlist || []).find(h => h.id === cb.hotel_id);
    const hotelLabel = hotel ? hotel.name : cb.hotel_id;

    confirmedEl.innerHTML =
      '<div class="confirmed-ref">' + escapeHtml(cb.booking_reference || '') + '</div>' +
      '<div class="pending-row"><dt>Отель</dt><dd>' + escapeHtml(hotelLabel) + '</dd></div>' +
      '<div class="pending-row"><dt>Даты</dt><dd>' +
        fmtDate(cb.check_in) + ' — ' + fmtDate(cb.check_out) + '</dd></div>' +
      '<div class="pending-row"><dt>Сумма</dt><dd>' +
        fmtPrice(cb.total_price, cb.currency) + '</dd></div>';
  }

  function renderState(state) {
    if (!state) return;
    renderPhase(state);
    renderPrefs(state);
    renderShortlist(state);
    renderPending(state);
    renderConfirmed(state);

    // подсказка в чате, когда варианты появились впервые
    const len = (state.shortlist || []).length;
    if (len > 0 && prevShortlistLen === 0 && !state.confirmed_booking) {
      addBubble('system', 'Нашёл ' + len + ' вариантов — панель ниже ↓');
    }
    prevShortlistLen = len;
  }

  /* ------------------------------------------------------------
     UI HELPERS
  ------------------------------------------------------------ */
  function setInputEnabled(on) {
    prompt.disabled = !on;
    const sendBtn = composer.querySelector('.send');
    if (sendBtn) sendBtn.disabled = !on;
    quickReplies.querySelectorAll('.chip').forEach(c => { c.disabled = !on; });
  }
  function setSideBusy(on) {
    const phase = (phaseBadge && phaseBadge.dataset.phase) || 'collect';
    findOthersBtn.disabled = on || phase === 'done';
    resetBtn.disabled = on;
    document.querySelectorAll('.hotel-select, .pending-actions button')
        .forEach(b => { b.disabled = on; });
  }
  function autosize() {
    prompt.style.height = 'auto';
    const h = Math.max(prompt.scrollHeight, 22);
    prompt.style.height = Math.min(h, 120) + 'px';
  }
  function handleApiError(e) {
    if (e.status === 401) { window.location.href = '/login'; return; }
    addBubble('system', 'Ошибка: ' + e.message);
  }
  async function loadChatHistory() {
    try {
      const data = await getJson('/api/history');
      const messages = (data && data.messages) || [];
      if (!messages.length) return false;
      for (const m of messages) {
        if (!m || !m.content) continue;
        addBubble(m.role === 'user' ? 'user' : 'assistant', m.content);
      }
      return true;
    } catch (e) {
      if (e.status === 401) { window.location.href = '/login'; return false; }
      console.error('loadChatHistory failed', e);
      return false;
    }
  }

  /* ------------------------------------------------------------
     ACTIONS
  ------------------------------------------------------------ */
  async function sendMessage(text) {
    if (busy) return;
    text = (text || '').trim();
    if (!text) return;

    busy = true;
    setInputEnabled(false);

    addBubble('user', text);
    prompt.value = '';
    autosize();

    const thinking = addThinking();
    try {
      const data = await postJson('/api/send-message', { message: text });
      removeEl(thinking);
      if (data.message) addBubble('assistant', data.message);
      if (data.state)   renderState(data.state);
    } catch (e) {
      removeEl(thinking);
      handleApiError(e);
    } finally {
      busy = false;
      setInputEnabled(true);
      prompt.focus();
    }
  }

  async function selectHotel(hotelId) {
    if (busy || !hotelId) return;
    busy = true;
    setSideBusy(true);
    try {
      const data = await postJson('/api/select-hotel', { hotel_id: hotelId });
      if (data.result && data.result.error) {
        addBubble('system', 'Не удалось выбрать отель: ' + data.result.error);
      } else {
        addBubble('system', 'Проверьте детали и подтвердите бронь.');
      }
      if (data.state) renderState(data.state);
    } catch (e) {
      handleApiError(e);
    } finally {
      busy = false;
      setSideBusy(false);
    }
  }

  async function confirmBooking() {
    if (busy) return;
    busy = true;
    setSideBusy(true);
    try {
      const data = await postJson('/api/booking/confirm');
      const bk = data.result && data.result.booking;
      if (bk) {
        addBubble('assistant',
          'Готово — бронь ' + bk.booking_reference + ' оформлена. Приятной поездки!');
      } else if (data.result && data.result.error) {
        addBubble('system', 'Не удалось подтвердить: ' + data.result.error);
      }
      if (data.state) renderState(data.state);
    } catch (e) {
      handleApiError(e);
    } finally {
      busy = false;
      setSideBusy(false);
    }
  }

  async function cancelBooking() {
    if (busy) return;
    busy = true;
    setSideBusy(true);
    try {
      const data = await postJson('/api/booking/cancel');
      addBubble('system', 'Бронь отменена — можно выбрать другой отель.');
      if (data.state) renderState(data.state);
    } catch (e) {
      handleApiError(e);
    } finally {
      busy = false;
      setSideBusy(false);
    }
  }

  async function findOthers() {
    if (busy) return;
    busy = true;
    setSideBusy(true);
    try {
      const data = await postJson('/api/find-others');
      if (data.candidates_found === 0) {
        addBubble('system', 'Больше подходящих вариантов нет — попробуйте изменить критерии.');
      } else if (data.candidates_found > 0) {
        addBubble('system', 'Нашёл ещё ' + data.candidates_found + ' вариантов.');
      }
      if (data.state) renderState(data.state);
    } catch (e) {
      handleApiError(e);
    } finally {
      busy = false;
      setSideBusy(false);
    }
  }

  async function resetAll() {
    if (busy) return;
    if (!window.confirm('Начать заново? Текущий диалог и бронь будут сброшены.')) return;

    busy = true;
    setSideBusy(true);
    setInputEnabled(false);
    try {
      const data = await postJson('/api/reset');
      thread.innerHTML = '';
      prevShortlistLen = 0;
      addBubble('assistant', 'Начинаем заново — расскажите о новой поездке.');
      if (data.state) renderState(data.state);
    } catch (e) {
      handleApiError(e);
    } finally {
      busy = false;
      setSideBusy(false);
      setInputEnabled(true);
      prompt.focus();
    }
  }

  /* ------------------------------------------------------------
     INIT
  ------------------------------------------------------------ */
  async function init() {
    // небольшой defensive-фикс: base.html выводит {{ user }} без __str__ у UserBase
    const un = document.querySelector('.user-name');
    if (un && /object at 0x/i.test(un.textContent)) {
      un.textContent = 'Профиль';
    }

    // composer
    prompt.addEventListener('input', autosize);
    prompt.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        composer.requestSubmit();
      }
    });
    composer.addEventListener('submit', (e) => {
      e.preventDefault();
      sendMessage(prompt.value);
    });
    autosize();

    // quick replies — заполняют поле, не отправляют
    quickReplies.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip');
      if (!chip || busy) return;
      prompt.value = chip.dataset.q || chip.textContent.trim();
      autosize();
      prompt.focus();
    });

    // side
    findOthersBtn.addEventListener('click', findOthers);
    resetBtn.addEventListener('click', resetAll);

    // initial state
    let state = null;
    try {
      state = await getJson('/api/state');
      renderState(state);
    } catch (e) {
      if (e.status === 401) { window.location.href = '/login'; return; }
      addBubble('system', 'Не удалось загрузить состояние: ' + e.message);
    }

    // NEW: поднимаем историю из БД. Если она непуста — приветствие не показываем.
    const hasHistory = await loadChatHistory();

    if (!hasHistory) {
      if (state && state.confirmed_booking) {
        addBubble('assistant',
          'Ваша бронь ' + state.confirmed_booking.booking_reference +
          ' активна. Хотите что-то ещё?');
      } else if (state && state.phase === 'collect' && !(state.preferences && state.preferences.destination)) {
        addBubble('assistant',
          'Здравствуйте! Опишите поездку — направление, даты, бюджет и с кем едете. ' +
          'Я подберу отели и помогу с бронью.');
      } else if (state) {
        addBubble('system', 'Продолжаем с того места, где остановились.');
      }
    }

    prompt.focus();
  }

  init().catch((e) => {
    console.error('init failed:', e);
    addBubble('system', 'Ошибка инициализации. Обновите страницу.');
  });
})();