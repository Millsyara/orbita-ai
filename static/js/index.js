(function () {
  'use strict';

  const { ta, form, reduceMotion, canTilt, autosize, EXAMPLES } = window.orbita;

  /* ---------- элементы ---------- */
  const chips     = document.getElementById('chips');
  const results   = document.getElementById('results');
  const think     = document.getElementById('think');
  const cardsEl   = document.getElementById('cards');
  const label     = document.getElementById('resultsLabel');
  const moreBtn   = document.getElementById('moreBtn');

  /* ---------- данные ---------- */
  const HOTELS = [
    {
      name: 'Casa Nube', place: 'Тавира, Португалия', art: 1, tag: 'тишина',
      why: '«Дом на скале: слышно только прибой. Завтрак на террасе, до пляжа шесть минут пешком.»',
      price: 142, rating: 4.9
    },
    {
      name: 'Hotel Kōyō', place: 'Киото, Япония', art: 2, tag: 'культура',
      why: '«Маленький рёкан с внутренним садом. Хозяева говорят по-английски и подают чай в семь утра.»',
      price: 210, rating: 4.8
    },
    {
      name: 'Villa Serena', place: 'Лугано, Швейцария', art: 3, tag: 'вид',
      why: '«Озеро из каждого окна, кофемашина в номере и ни одного конференц-зала на весь дом.»',
      price: 265, rating: 4.7
    },
    {
      name: 'The Slow House', place: 'Тбилиси, Грузия', art: 4, tag: 'аутентичность',
      why: '«Винтовая лестница, инжир во дворе и кофе по-турецки до полудня. Без спешки.»',
      price: 78, rating: 4.9
    },
    {
      name: 'Mar Abierto', place: 'Кадис, Испания', art: 5, tag: 'море',
      why: '«Белый дом у Атлантики, гамаки на крыше и завтрак до двенадцати — без осуждения.»',
      price: 118, rating: 4.6
    },
    {
      name: 'Nordlys', place: 'Тромсё, Норвегия', art: 6, tag: 'впечатление',
      why: '«Панорамное окно в спальне: северное сияние можно смотреть, не вставая с кровати.»',
      price: 320, rating: 5.0
    }
  ];

  /* ---------- рендер карточек ---------- */
  let offset = 0;

  function pickHotels(n) {
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push(HOTELS[(offset + i) % HOTELS.length]);
    }
    offset = (offset + n) % HOTELS.length;
    return out;
  }

  function renderCards(list) {
    cardsEl.innerHTML = list.map((h) => `
      <article class="card">
        <div class="card-art art-${h.art}"><span class="tag">${h.tag}</span></div>
        <div class="card-body">
          <div class="card-top">
            <h3>${h.name}</h3>
            <span class="rating">★ ${h.rating.toFixed(1)}</span>
          </div>
          <p class="place">${h.place}</p>
          <p class="why">${h.why}</p>
          <div class="card-foot">
            <span class="price">${h.price} € <small>/ ночь</small></span>
            <button class="mini" type="button">Выбрать</button>
          </div>
        </div>
      </article>
    `).join('');

    Array.from(cardsEl.children).forEach((card, i) => {
      card.style.animationDelay = (i * 90) + 'ms';
      card.addEventListener('animationend', () => card.classList.add('ready'), { once: true });
    });

    label.textContent = `${list.length} варианта из 218 · подобрано агентом`;
  }

  /* ---------- «мысли» агента ---------- */
  function showThinking(query) {
    const short = query.length > 46 ? query.slice(0, 46).trim() + '…' : query;

    const steps = [
      `Слушаю: «${short}»`,
      'Отсеиваю 218 отелей по 12 параметрам',
      'Сверяю отзывы, цены и расстояние до воды'
    ];

    think.innerHTML = '';
    const gap = reduceMotion ? 0 : 500;

    steps.forEach((text, i) => {
      const row = document.createElement('div');
      row.className = 'think-row';
      row.style.animationDelay = (i * gap) + 'ms';
      row.innerHTML = `<span class="tick"></span><span>${text}</span>`;
      think.appendChild(row);

      setTimeout(() => {
        row.querySelector('.tick').classList.add('done');
      }, i * gap + gap * 0.85 + 200);
    });

    return gap * (steps.length - 1) + (reduceMotion ? 0 : 700);
  }

  /* ---------- запуск подбора ---------- */
  let busy = false;

  function run(query) {
    if (busy) return;
    busy = true;

    const q = (query || ta.value || ta.placeholder || EXAMPLES[0]).trim();

    results.hidden = false;
    cardsEl.innerHTML = '';
    label.textContent = 'Агент работает…';

    const wait = showThinking(q);

    setTimeout(() => {
      window.scrollTo({
        top: results.offsetTop - 96,
        behavior: reduceMotion ? 'auto' : 'smooth'
      });
    }, 60);

    setTimeout(() => {
      renderCards(pickHotels(3));
      busy = false;
    }, wait);
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    run(ta.value);
  });

  chips.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    ta.value = chip.textContent.trim();
    autosize();
    run(ta.value);
  });

  moreBtn.addEventListener('click', () => {
    if (busy) return;
    renderCards(pickHotels(3));
  });

  /* ---------- лёгкий 3D-наклон карточек ---------- */
  if (canTilt && !reduceMotion) {
    cardsEl.addEventListener('mousemove', (e) => {
      const card = e.target.closest('.card');
      if (!card || !card.classList.contains('ready')) return;

      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;

      card.style.transform =
        `perspective(1000px) rotateX(${(-py * 4).toFixed(2)}deg) ` +
        `rotateY(${(px * 4).toFixed(2)}deg) translateY(-4px)`;
    });

    cardsEl.addEventListener('mouseover', (e) => {
      const active = e.target.closest('.card');
      Array.from(cardsEl.children).forEach((c) => {
        if (c !== active) c.style.transform = '';
      });
    });

    cardsEl.addEventListener('mouseleave', () => {
      Array.from(cardsEl.children).forEach((c) => { c.style.transform = ''; });
    });
  }
})();