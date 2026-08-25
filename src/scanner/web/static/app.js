/* Panelin canli katmani: tarama durumu, geri sayim, yeni ilan bildirimi.
   Sunucu 10 sn'de bir yoklanir; tarama sirasinda liste kendiliginden tazelenmez,
   bunun yerine "N yeni ilan" seridi cikar - kullanici okurken sayfa altindan kaymasin. */
(function () {
  "use strict";

  const live = document.getElementById("live");
  const liveText = document.getElementById("live-text");
  const badge = document.getElementById("unread-badge");
  const toast = document.getElementById("toast");
  const toastText = document.getElementById("toast-text");
  const scanBtn = document.getElementById("scan-now");
  const sweepBtn = document.getElementById("sweep-now");
  const sweepCancelBtn = document.getElementById("sweep-cancel");
  const baseline = document.body.dataset.newestSeen || "";

  const POLL_MS = 10000;
  const SWEEP_POLL_MS = 2000;   // tur sirasinda ilerleme akici gorunsun
  let ticking = null;
  let sweepTimer = null;
  let sweepWasRunning = false;

  function plural(n, word) { return n + " " + word; }

  function phaseLabel(scan) {
    if (!scan.enabled) return "otomatik tarama kapalı";
    if (scan.phase === "quick") return "yeni ilan taraması…";
    if (scan.phase === "scanning") return "tam tarama: tüm kaynaklar…";
    if (scan.phase === "verifying") return "ilan linkleri doğrulanıyor…";
    return null;
  }

  function countdown(seconds) {
    if (seconds === null || seconds === undefined) return "";
    if (seconds <= 0) return "birazdan";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m > 0 ? m + " dk " + String(s).padStart(2, "0") + " sn" : s + " sn";
  }

  function renderLive(scan) {
    if (!live || !liveText) return;
    live.dataset.phase = scan.enabled ? scan.phase : "off";
    const label = phaseLabel(scan);
    if (label) {
      liveText.innerHTML = "<b>" + label + "</b>";
      if (ticking) { clearInterval(ticking); ticking = null; }
      return;
    }
    let left = scan.seconds_to_next;
    const paint = function () {
      const kind = scan.next_kind === "full" ? "tam tarama" : "yeni ilan taraması";
      const parts = [kind + " <b>" + countdown(left) + "</b>"];
      if (scan.last_new) parts.push(plural(scan.last_new, "yeni"));
      liveText.innerHTML = parts.join(" · ");
      if (left !== null && left !== undefined) left = Math.max(0, left - 1);
    };
    paint();
    if (ticking) clearInterval(ticking);
    ticking = setInterval(paint, 1000);
  }

  function renderToast(count) {
    if (!toast || !toastText) return;
    if (count > 0) {
      toastText.textContent = count + " yeni ilan geldi";
      toast.classList.add("show");
    } else {
      toast.classList.remove("show");
    }
  }

  /* ---- temizlik turu (Listeyi kontrol et) ----
     Tur sirasinda canli serit ilerlemeyi gosterir; bitince toast ozet verir.
     Ayri bir poller kurulmaz, /api/durum yanitina biniyor. */
  function sweepSummary(s) {
    const parts = [];
    if (s.closed) parts.push(s.closed + " kapandı");
    if (s.flagged) parts.push(s.flagged + " sorunlu");
    // linki açılmayan ama kaynağın API'sinden sorulabilen ilanlar (Jooble)
    if (s.api_checked) parts.push(s.api_checked + " API ile soruldu");
    if (s.unverified > 0) parts.push(s.unverified + " doğrulanamadı");
    if (!parts.length) return "Kontrol bitti: kapanan ilan yok";
    return "Kontrol bitti · " + parts.join(" · ");
  }

  function renderSweep(sweep) {
    if (!sweep) return;
    const running = sweep.running;
    if (sweepBtn) {
      sweepBtn.disabled = running;
      sweepBtn.textContent = running ? "kontrol ediliyor…" : "Listeyi kontrol et";
    }
    if (sweepCancelBtn) sweepCancelBtn.hidden = !running;

    if (running) {
      if (live && liveText) {
        live.dataset.phase = "verifying";
        liveText.innerHTML = "<b>linkler kontrol ediliyor: " + sweep.checked + " / " +
          sweep.total + "</b>" + (sweep.closed ? " · " + sweep.closed + " kapandı" : "");
        if (ticking) { clearInterval(ticking); ticking = null; }
      }
      if (!sweepTimer) sweepTimer = setInterval(poll, SWEEP_POLL_MS);
    } else if (sweepTimer) {
      clearInterval(sweepTimer);
      sweepTimer = null;
    }

    // Yalnizca calisirken -> bitti gecisinde ozet goster; her yoklamada degil.
    if (sweepWasRunning && !running && toast && toastText) {
      toastText.textContent = sweepSummary(sweep);
      toast.classList.add("show");
    }
    sweepWasRunning = running;
  }

  function renderBadge(unread) {
    if (!badge) return;
    badge.textContent = unread;
    badge.hidden = unread === 0;
  }

  async function poll() {
    try {
      const url = "/api/durum" + (baseline ? "?since_ts=" + encodeURIComponent(baseline) : "");
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      renderLive(data.scan);
      renderBadge(data.unread);
      // Temizlik turu sirasinda canli serit ve toast onun; sonra normale doner.
      if (!(data.sweep && data.sweep.running)) renderToast(data.new_since);
      renderSweep(data.sweep);
      document.querySelectorAll("[data-stat]").forEach(function (el) {
        const key = el.dataset.stat;
        if (data.stats[key] !== undefined) el.textContent = data.stats[key];
      });
    } catch (err) {
      /* panel kapaliysa ya da ag koptuysa sessiz gec */
    }
  }

  if (scanBtn) {
    scanBtn.addEventListener("click", async function () {
      scanBtn.disabled = true;
      scanBtn.dataset.label = scanBtn.textContent;
      scanBtn.textContent = "başlatılıyor…";
      try {
        await fetch("/api/tara", { method: "POST" });
        await poll();
      } finally {
        setTimeout(function () {
          scanBtn.disabled = false;
          scanBtn.textContent = scanBtn.dataset.label || "Şimdi tara";
        }, 2500);
      }
    });
  }

  async function startSweep() {
    sweepBtn.disabled = true;
    sweepBtn.textContent = "başlatılıyor…";
    // Filtre sunucuda kurulur: ekrandaki liste hangi parametrelerle geldiyse
    // kontrol de onlarla kossun diye adres cubugu oldugu gibi gonderilir.
    const params = {};
    new URLSearchParams(sweepBtn.dataset.query || "").forEach(function (value, key) {
      params[key] = value;
    });
    try {
      const res = await fetch("/api/temizlik", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok && data.message && toast && toastText) {
        toastText.textContent = data.message;
        toast.classList.add("show");
      }
      if (data.sweep) renderSweep(data.sweep);
    } catch (err) {
      sweepBtn.disabled = false;
      sweepBtn.textContent = "Listeyi kontrol et";
    }
    await poll();
  }

  if (sweepBtn) {
    sweepBtn.addEventListener("click", function () {
      askConfirm("Bu filtredeki " + (sweepBtn.dataset.total || "0") + " ilanın linki tek tek " +
                 "açılacak, kapanmış olanlar listeden düşecek. Sürebilir; sayfada kalabilirsiniz.",
                 "Evet, kontrol et", startSweep);
    });
  }

  if (sweepCancelBtn) {
    sweepCancelBtn.addEventListener("click", async function () {
      sweepCancelBtn.disabled = true;
      try {
        await fetch("/api/temizlik/iptal", { method: "POST" });
        await poll();
      } finally {
        sweepCancelBtn.disabled = false;
      }
    });
  }

  /* ---- liste gorunumu: detayli liste <-> izgara ----
     Sunucuya gitmez, adres cubugunu kirletmez: tek sinif degisimi + localStorage.
     Ilk uygulama <head>'deki kisa script'te yapiliyor (goz kirpmasin diye),
     burasi yalnizca degistirme isini yapar. */
  const viewBtn = document.getElementById("view-toggle");
  if (viewBtn) {
    const kokDurum = function () {
      const izgara = document.documentElement.dataset.view === "grid";
      viewBtn.setAttribute("aria-pressed", izgara ? "true" : "false");
      viewBtn.title = izgara ? "Detaylı listeye dön" : "Izgara görünümü: aynı anda daha çok ilan";
    };
    kokDurum();
    viewBtn.addEventListener("click", function () {
      const izgara = document.documentElement.dataset.view === "grid";
      if (izgara) {
        delete document.documentElement.dataset.view;
      } else {
        document.documentElement.dataset.view = "grid";
      }
      try { localStorage.setItem("radar-gorunum", izgara ? "list" : "grid"); } catch (e) { /* yok say */ }
      kokDurum();
    });
  }

  const reloadBtn = document.getElementById("toast-reload");
  if (reloadBtn) reloadBtn.addEventListener("click", function () { location.reload(); });

  const dismissBtn = document.getElementById("toast-dismiss");
  if (dismissBtn) dismissBtn.addEventListener("click", function () { toast.classList.remove("show"); });

  /* ---- tema anahtari ----
     Secim localStorage'da durur; sayfa <head>'deki kisa script ile boyanmadan
     once uygulanir, burasi yalnizca degistirme isini yapar. */
  const themeBtn = document.getElementById("theme-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      const kok = document.documentElement;
      // secim yoksa isletim sisteminin tercihinin TERSINE gec
      const sistemKoyu = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const suan = kok.dataset.theme || (sistemKoyu ? "dark" : "light");
      const yeni = suan === "dark" ? "light" : "dark";

      const uygula = function () {
        kok.dataset.theme = yeni;
        try { localStorage.setItem("radar-tema", yeni); } catch (e) { /* kapali olabilir */ }
      };

      themeBtn.classList.remove("pulse");
      void themeBtn.offsetWidth;          // animasyon yeniden kossun
      themeBtn.classList.add("pulse");

      // Capraz gecis: renkler var() jetonlarindan geldigi icin CSS transition
      // calismiyor (eski renkte donuyor). View Transitions tum sayfayi bir kerede
      // dogru renge gecirip ustune animasyonu kendisi koyar.
      if (typeof document.startViewTransition === "function") {
        document.startViewTransition(uygula);
      } else {
        uygula();
      }
    });
  }

  /* ---- sag ust hesap menusu ---- */
  const avatarBtn = document.getElementById("avatar-btn");
  const accountMenu = document.getElementById("account-menu");
  if (avatarBtn && accountMenu) {
    const setMenu = function (acik) {
      accountMenu.hidden = !acik;
      avatarBtn.setAttribute("aria-expanded", acik ? "true" : "false");
    };
    avatarBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      e.preventDefault();
      setMenu(accountMenu.hidden);       // acikken tekrar basinca kapanir
    });
    // menu disina tiklama, ESC ve sayfadan ayrilma menuyu kapatir
    document.addEventListener("click", function (e) {
      if (!accountMenu.hidden && !accountMenu.contains(e.target) && e.target !== avatarBtn) {
        setMenu(false);
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setMenu(false);
    });
    window.addEventListener("blur", function () { setMenu(false); });
  }

  /* ---- onay kutusu ----
     data-confirm tasiyan her form, gonderilmeden once buradan gecer.
     Silme gibi geri alinamaz islemler yanlislikla tetiklenmesin. */
  const modal = document.getElementById("confirm-modal");
  const modalText = document.getElementById("confirm-text");
  const modalYes = document.getElementById("confirm-yes");
  const modalNo = document.getElementById("confirm-no");
  let pendingAction = null;

  function closeConfirm() {
    if (!modal) return;
    modal.hidden = true;
    pendingAction = null;
  }

  /* Form disi islemler de ayni modali kullansin (ornek: Listeyi kontrol et).
     Modal yoksa tarayicinin kendi onayina duser. */
  function askConfirm(soru, label, onYes) {
    if (!modal || !modalYes || !modalNo) {
      if (window.confirm(soru)) onYes();
      return;
    }
    pendingAction = onYes;
    modalText.textContent = soru;
    modalYes.textContent = label || "Evet, sil";
    modal.hidden = false;
    modalNo.focus();
  }

  if (modal && modalYes && modalNo) {
    document.addEventListener("submit", function (e) {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      const soru = form.dataset.confirm;
      if (!soru || form.dataset.confirmed === "1") return;
      e.preventDefault();
      askConfirm(soru, form.dataset.confirmLabel, function () {
        form.dataset.confirmed = "1";
        form.submit();
      });
    }, true);

    modalYes.addEventListener("click", function () {
      const action = pendingAction;
      closeConfirm();
      if (action) action();
    });
    modalNo.addEventListener("click", closeConfirm);
    modal.addEventListener("click", function (e) {
      if (e.target === modal) closeConfirm();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeConfirm();
    });
  }

  poll();
  setInterval(function () { poll(); }, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });
})();
