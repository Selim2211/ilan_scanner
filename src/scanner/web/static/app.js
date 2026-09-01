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
  const sweepResultModal = document.getElementById("sweep-result-modal");
  const sweepResultIcon = document.getElementById("sweep-result-icon");
  const sweepResultStats = document.getElementById("sweep-result-stats");
  const sweepResultClose = document.getElementById("sweep-result-close");
  const sweepResultReload = document.getElementById("sweep-result-reload");
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
     Tur sirasinda canli serit ilerlemeyi gosterir; bitince kendi kutusunda
     ozet verir - toast bu buyuklukte bir islem icin yeterince goze
     carpmiyordu, kullanici kacirabiliyordu. Ayri bir poller kurulmaz,
     /api/durum yanitina biniyor. */
  function sweepRow(label, value, highlight) {
    return '<div class="sweep-result-row' + (highlight ? " highlight" : "") + '">' +
      "<span>" + label + "</span><b>" + value + "</b></div>";
  }

  function showSweepResult(s) {
    if (!sweepResultModal || !sweepResultStats) return;
    const rows = [sweepRow("Kontrol edilen", s.checked)];
    if (s.closed) rows.push(sweepRow("Kapanmış, listeden düşürüldü", s.closed, true));
    if (s.flagged) rows.push(sweepRow("Sorunlu işaretlendi", s.flagged, true));
    if (s.api_checked) rows.push(sweepRow("Kaynağın API'siyle sorulan", s.api_checked));
    if (s.unverified) rows.push(sweepRow("Doğrulanamadı", s.unverified));
    sweepResultStats.innerHTML = rows.join("");
    if (sweepResultIcon) {
      const kapandi = s.closed > 0;
      sweepResultIcon.textContent = kapandi ? "!" : "✓";
      sweepResultIcon.classList.toggle("has-closed", kapandi);
    }
    sweepResultModal.hidden = false;
  }

  function closeSweepResult() {
    if (sweepResultModal) sweepResultModal.hidden = true;
  }

  if (sweepResultClose) sweepResultClose.addEventListener("click", closeSweepResult);
  if (sweepResultReload) {
    sweepResultReload.addEventListener("click", function () {
      window.location.reload();
    });
  }
  if (sweepResultModal) {
    sweepResultModal.addEventListener("click", function (e) {
      if (e.target === sweepResultModal) closeSweepResult();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !sweepResultModal.hidden) closeSweepResult();
    });
  }

  function renderSweep(sweep) {
    if (!sweep) return;
    const running = sweep.running;
    if (sweepBtn) {
      sweepBtn.disabled = running;
      sweepBtn.textContent = running ? "kontrol ediliyor…" : "Tümünü kontrol et";
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
    // Kullanici turu Durdur'a basip iptal ettiyse sonuc kutusu yerine sessiz
    // kalinir - kendi durdurdugu bir islem icin ayri bir "bitti" bildirimine
    // gerek yok.
    if (sweepWasRunning && !running && sweep.phase !== "cancelled") {
      showSweepResult(sweep);
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
    // Adres cubugunun sorgu dizesi GONDERILIR: sunucu listeyle ayni filtreleri
    // kurar, boylece "ekranda ne goruyorsan o kontrol edilir" garantisi olusur.
    try {
      const res = await fetch("/api/temizlik", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: window.location.search }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok && data.message && toast && toastText) {
        toastText.textContent = data.message;
        toast.classList.add("show");
      }
      if (data.sweep) renderSweep(data.sweep);
    } catch (err) {
      sweepBtn.disabled = false;
      sweepBtn.textContent = "Tümünü kontrol et";
    }
    await poll();
  }

  if (sweepBtn) {
    sweepBtn.addEventListener("click", function () {
      askConfirm("Ekranda gördüğünüz " + (sweepBtn.dataset.total || "0") + " ilan tek tek " +
                 "kontrol edilecek; kapanmış olanlar listeden düşecek ve bir daha geri " +
                 "gelmeyecek. İlan sayısına göre birkaç dakika sürebilir; sayfada kalabilir, " +
                 "istediğiniz an durdurabilirsiniz.",
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

  /* ---- ilan ozeti: "Özet" dugmesine tiklaninca acilan tek popover ----
     20+ kart icin ayri ayri kutu yerine SAYFADA TEK dugum (#ozet-modal) var;
     hangi dugmeye tiklanirsa icerigi onunla degistirilip yanina konumlanir.
     Veri /api/ozet'ten gelir (yabanci dildeki aciklama orada Ingilizce'ye
     cevrilip cache'lenir).

     Eskiden karta gelince (hover) ~0.9 sn sonra kendiliginden aciliyordu;
     kullanici "tak diye, tiklayinca acilsin, arka plani kilitlemesin" istedi.
     Artik TIKLAMA ile geciklmesizce acilir, arka plan tiklanabilir kalir -
     kapanmasi icin disina tiklamak ya da Esc yeterli. */
  const ozetModal = document.getElementById("ozet-modal");

  if (ozetModal) {
    const ozetBaslik = ozetModal.querySelector(".ozet-modal-baslik");
    const ozetOzet = ozetModal.querySelector(".ozet-modal-ozet");
    const ozetGereksinimler = ozetModal.querySelector(".ozet-modal-gereksinimler");
    const ozetDil = ozetModal.querySelector(".ozet-modal-dil");
    const ozetSpinner = ozetModal.querySelector(".ozet-modal-spinner");
    const ozetKapatBtn = ozetModal.querySelector(".ozet-modal-kapat");

    let aktifFingerprint = null;
    let istekSirasi = 0;                    // gec gelen eski cevap yeniyi ezmesin
    const onbellek = new Map();              // fingerprint -> /api/ozet govdesi

    function modalKapat() {
      ozetModal.hidden = true;
      aktifFingerprint = null;
      istekSirasi += 1;                     // yoldaki eski istek gelince atilsin
    }

    function dolgula(data) {
      ozetBaslik.textContent = data.title || "";
      ozetOzet.textContent = data.summary || "(açıklama yok)";
      ozetGereksinimler.innerHTML = "";
      // "ai" = yapay zekanin cikardigi GERCEK olmazsa olmazlar (alt alta, alti
      // cizili). "kelime" = puanlama sozlugu eslesmeleri (yan yana rozet).
      const yapayZeka = data.kaynak === "ai";
      ozetGereksinimler.dataset.kaynak = data.kaynak || "kelime";
      (data.requirements || []).forEach(function (madde) {
        const oge = document.createElement("span");
        oge.className = yapayZeka ? "hit must" : "hit zero";
        oge.textContent = madde;
        ozetGereksinimler.appendChild(oge);
      });
      ozetDil.textContent = yapayZeka
        ? "yapay zeka özeti"
        : (data.translated ? (data.lang + " → en çevrildi") : "");
    }

    async function ozetGetir(fp) {
      if (onbellek.has(fp)) return onbellek.get(fp);
      try {
        const res = await fetch("/api/ozet?fingerprint=" + encodeURIComponent(fp),
                                { cache: "no-store" });
        const data = await res.json().catch(function () { return {}; });
        if (!res.ok || !data.ok) return null;
        onbellek.set(fp, data);
        return data;
      } catch (err) {
        return null;
      }
    }

    async function tikla(buton) {
      const fp = buton.dataset.fingerprint;
      if (aktifFingerprint === fp && !ozetModal.hidden) { modalKapat(); return; }  // ayni dugme: kapat

      istekSirasi += 1;
      const sira = istekSirasi;
      aktifFingerprint = fp;

      // TAK DIYE ac: veri onbellekte olmasa bile pencere HEMEN gorunur, icinde
      // donen halka doner - "tiklayinca beklemeden gelsin" tam bu. Her zaman
      // EKRANIN ORTASINDA acilir (CSS) - butona ankrajli konum alt siradaki
      // kartlarda ekran disina tasiyordu.
      const varCache = onbellek.has(fp);
      ozetBaslik.textContent = "";
      ozetOzet.textContent = "";
      ozetGereksinimler.innerHTML = "";
      ozetDil.textContent = "";
      if (ozetSpinner) ozetSpinner.hidden = varCache;
      ozetModal.hidden = false;

      const data = await ozetGetir(fp);
      if (sira !== istekSirasi) return;        // beklerken baska dugmeye tiklandi/kapatildi
      if (ozetSpinner) ozetSpinner.hidden = true;
      if (!data) { modalKapat(); return; }     // ozet alinamadi
      dolgula(data);
    }

    document.querySelectorAll(".ozet-btn").forEach(function (buton) {
      buton.addEventListener("click", function (e) {
        e.stopPropagation();                   // document'teki disina-tiklama kapatmasin
        tikla(buton);
      });
    });

    if (ozetKapatBtn) ozetKapatBtn.addEventListener("click", modalKapat);
    document.addEventListener("click", function (e) {
      if (!ozetModal.hidden && !ozetModal.contains(e.target)) modalKapat();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !ozetModal.hidden) modalKapat();
    });
  }

  // --- cok secimli kutu (ulke filtresi) --------------------------------
  // Isaretleyiciler sunucudan gercek checkbox olarak geliyor; burasi yalnizca
  // panel/arama/ozet katmani. JS calismazsa kutular yine formla gonderilir.
  function initMultiselect(root) {
    const toggle = root.querySelector(".ms-toggle");
    const panel = root.querySelector(".ms-panel");
    const label = root.querySelector(".ms-label");
    const search = root.querySelector(".ms-search");
    const list = root.querySelector(".ms-list");
    const noResult = root.querySelector("[data-ms-noresult]");
    if (!toggle || !panel || !label || !list) return;
    const items = Array.prototype.slice.call(list.querySelectorAll(".ms-item"));
    const bos = root.dataset.empty || "Hepsi";
    // Listede karsiligi olmayan secimler kutunun DISINDA gizli alan olarak durur
    // (sablona bak); ozet sayisi onlari da saymali.
    const alanAdi = items.length ? items[0].querySelector("input").name : "";
    const gizli = alanAdi && root.parentNode
      ? Array.prototype.slice.call(root.parentNode.querySelectorAll(
          'input[type="hidden"][name="' + alanAdi + '"]'))
      : [];

    function secililer() {
      return items.filter(function (it) { return it.querySelector("input").checked; });
    }

    function ozetle() {
      const secim = secililer();
      const toplam = secim.length + gizli.length;
      if (!toplam) {
        label.textContent = bos;
        root.classList.remove("has-value");
        return;
      }
      const ilk = secim.length
        ? secim[0].querySelector(".ms-name").textContent.trim()
        : gizli[0].value;
      label.textContent = toplam > 1 ? ilk + " +" + (toplam - 1) : ilk;
      root.classList.add("has-value");
    }

    function ac(open) {
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      root.classList.toggle("open", open);
      if (open && search) { search.value = ""; suz(""); search.focus(); }
    }

    function suz(q) {
      const anahtar = q.trim().toLowerCase();
      let gorunen = 0;
      items.forEach(function (it) {
        const uygun = !anahtar || (it.dataset.search || "").indexOf(anahtar) !== -1;
        it.hidden = !uygun;
        if (uygun) gorunen++;
      });
      // Bolge basligi, altindaki ulkelerin hepsi elenince gizlenir
      list.querySelectorAll(".ms-group").forEach(function (bas) {
        let node = bas.nextElementSibling;
        let acik = false;
        while (node && !node.classList.contains("ms-group")) {
          if (node.classList.contains("ms-item") && !node.hidden) { acik = true; break; }
          node = node.nextElementSibling;
        }
        bas.hidden = !acik;
      });
      if (noResult) noResult.hidden = gorunen !== 0;
    }

    toggle.addEventListener("click", function () { ac(panel.hidden); });
    if (search) {
      search.addEventListener("input", function () { suz(search.value); });
      search.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        e.preventDefault();                     // arama kutusu formu gondermesin
        const ilk = items.filter(function (it) { return !it.hidden; })[0];
        if (ilk) {
          const kutu = ilk.querySelector("input");
          kutu.checked = !kutu.checked;
          ozetle();
        }
      });
    }
    list.addEventListener("change", ozetle);
    const clear = root.querySelector(".ms-clear");
    if (clear) {
      clear.addEventListener("click", function () {
        items.forEach(function (it) { it.querySelector("input").checked = false; });
        ozetle();
      });
    }
    const close = root.querySelector(".ms-close");
    if (close) close.addEventListener("click", function () { ac(false); });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !root.contains(e.target)) ac(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) { ac(false); toggle.focus(); }
    });
    ozetle();
  }

  document.querySelectorAll("[data-multiselect]").forEach(initMultiselect);

  poll();
  setInterval(function () { poll(); }, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });
})();
