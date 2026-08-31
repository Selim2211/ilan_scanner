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
    // Filtre GONDERILMEZ: sunucu ekrandaki filtreden bagimsiz olarak butun
    // aktif ilanlari tarar - filtre disinda kalan kapanmis ilanlar da yakalansin.
    try {
      const res = await fetch("/api/temizlik", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
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
      askConfirm((sweepBtn.dataset.total || "0") + " ilanın linki tek tek açılacak, kapanmış " +
                 "olanlar listeden düşecek. Ekrandaki filtre dikkate alınmaz. İlan sayısına göre " +
                 "birkaç dakika sürebilir; sayfada kalabilir, istediğiniz an durdurabilirsiniz.",
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

  /* ---- ilan ozeti: karta gelince ekranin ortasinda acilan tek modal ----
     20+ kart icin ayri ayri kutu yerine SAYFADA TEK modal (#ozet-modal) var;
     hangi karta gelinirse icerigi onunla degistirilir. Veri /api/ozet'ten
     gelir (yabanci dildeki aciklama orada Ingilizce'ye cevrilip cache'lenir).

     Zamanlama iki noktada bilerek geciktirilmis:
       - ACILIS 250ms: liste uzerinde hizli scroll/gecis yaparken her kartta
         modal flaslamasin diye. Kullanici gercekten bir kartta DURURSA acilir.
       - KAPANIS 200ms: bir karttan digerine gecerken modal kapanip yeniden
         acilmasin diye - o sure icinde baska bir karta girilirse icerik
         degistirilir, kapat/ac dongusu olmaz. Modal'in kendisine (fareyle
         icerige dogru giderken) girilirse de kapanma iptal edilir. */
  const ozetModal = document.getElementById("ozet-modal");

  if (ozetModal) {
    const ozetBaslik = ozetModal.querySelector(".ozet-modal-baslik");
    const ozetOzet = ozetModal.querySelector(".ozet-modal-ozet");
    const ozetGereksinimler = ozetModal.querySelector(".ozet-modal-gereksinimler");
    const ozetDil = ozetModal.querySelector(".ozet-modal-dil");
    const ozetKapatBtn = ozetModal.querySelector(".ozet-modal-kapat");

    /* ACILIS 900ms: imlecin yaninda bir halka DOLAR, dolmadan pencere acilmaz.
       Kasitsiz acilmayi engelleyen sey bu: liste uzerinde gezinen fare bir
       kartta ~1 sn durmadikca ozet gelmez. Onbellekte hazir olsa bile beklenir -
       "tak diye acilma" sikayeti tam olarak buydu. */
    const ACILIS_GECIKME = 900;
    const KAPANIS_GECIKME = 200;
    let acilisZamanlayici = null;
    let kapanisZamanlayici = null;
    let aktifFingerprint = null;
    let istekSirasi = 0;                    // gec gelen eski cevap yeniyi ezmesin
    const onbellek = new Map();              // fingerprint -> /api/ozet govdesi

    /* Imlecin yaninda dolan halka. Tek dugum: her kart icin ayri eleman
       tutmak yerine body'ye bir kez eklenip fareyle tasiniyor. */
    let halka = null;

    function halkaGoster(x, y) {
      if (!halka) {
        halka = document.createElement("div");
        halka.className = "ozet-halka";
        halka.setAttribute("aria-hidden", "true");
        document.body.appendChild(halka);
      }
      halka.style.setProperty("--sure", ACILIS_GECIKME + "ms");
      halkaTasi(x, y);
      // Animasyon her seferinde bastan baslasin: sinifi kaldirip reflow tetikle.
      halka.classList.remove("doluyor");
      void halka.offsetWidth;
      halka.classList.add("doluyor");
    }

    function halkaTasi(x, y) {
      if (halka) { halka.style.left = x + "px"; halka.style.top = y + "px"; }
    }

    function halkaYukleniyor() {
      if (halka) { halka.classList.remove("doluyor"); halka.classList.add("bekliyor"); }
    }

    function halkaGizle() {
      if (halka) halka.classList.remove("doluyor", "bekliyor");
    }

    function kapanisIptal() {
      if (kapanisZamanlayici) { clearTimeout(kapanisZamanlayici); kapanisZamanlayici = null; }
    }

    function modalKapat() {
      kapanisIptal();
      if (acilisZamanlayici) { clearTimeout(acilisZamanlayici); acilisZamanlayici = null; }
      ozetModal.hidden = true;
      aktifFingerprint = null;
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

    /* Icerik HAZIR OLMADAN pencere acilmaz: kartin kosesinde yuvarlak doner,
       yanit gelince pencere dolu halde acilir. Onbellekten gelirse yuvarlak
       neredeyse hic gorunmez (~10 ms), yapay zeka uretirse ~1.5 sn doner. */
    async function icerikGetir(fp, sira) {
      if (onbellek.has(fp)) return onbellek.get(fp);

      // Halka doldu ama ozet henuz yok (yapay zeka ~1.5 sn suruyor): halka
      // BELIRSIZ donmeye gecer, ekranda olu bekleme olmaz.
      halkaYukleniyor();
      try {
        const res = await fetch("/api/ozet?fingerprint=" + encodeURIComponent(fp),
                                { cache: "no-store" });
        const data = await res.json().catch(function () { return {}; });
        if (!res.ok || !data.ok) return null;
        onbellek.set(fp, data);
        return data;
      } catch (err) {
        return null;
      } finally {
        halkaGizle();
      }
    }

    async function modalAc(fp) {
      kapanisIptal();
      istekSirasi += 1;
      const sira = istekSirasi;

      const data = await icerikGetir(fp, sira);
      // Beklerken kullanici baska karta gecti ya da listeden ayrildi: bu yaniti at.
      if (sira !== istekSirasi) return;
      if (!data) return;                       // ozet alinamadi - pencere hic acilmaz

      aktifFingerprint = fp;
      dolgula(data);
      ozetModal.hidden = false;
    }

    document.querySelectorAll(".item[data-fingerprint]").forEach(function (kart) {
      const fp = kart.dataset.fingerprint;

      kart.addEventListener("pointerenter", function (e) {
        kapanisIptal();
        if (aktifFingerprint === fp) return;    // zaten bu kart acik
        // Dokunmatikte halka anlamsiz (imlec yok) ve :hover takili kalir.
        if (e.pointerType === "touch") return;

        if (acilisZamanlayici) clearTimeout(acilisZamanlayici);
        halkaGoster(e.clientX, e.clientY);
        acilisZamanlayici = setTimeout(function () {
          acilisZamanlayici = null;
          modalAc(fp);
        }, ACILIS_GECIKME);
      });

      // Halka fareyi takip etsin; kart icinde gezerken yerinde kalmasin.
      kart.addEventListener("pointermove", function (e) {
        if (acilisZamanlayici) halkaTasi(e.clientX, e.clientY);
      });

      kart.addEventListener("pointerleave", function () {
        // Halka dolmadan cikildi: ozet ACILMAZ - kasitsiz acilmanin onlendigi yer.
        if (acilisZamanlayici) { clearTimeout(acilisZamanlayici); acilisZamanlayici = null; }
        halkaGizle();
        // Yanit yoldaysa gelince atilsin: sirayi ilerlet.
        if (ozetModal.hidden) istekSirasi += 1;
        kapanisIptal();
        kapanisZamanlayici = setTimeout(modalKapat, KAPANIS_GECIKME);
      });
    });

    // Modal'in uzerindeyken (icerige dogru giderken) kapanma tetiklenmesin.
    ozetModal.addEventListener("pointerenter", kapanisIptal);
    ozetModal.addEventListener("pointerleave", function () {
      kapanisZamanlayici = setTimeout(modalKapat, KAPANIS_GECIKME);
    });

    // Dokunmatikte pointerleave hic gelmeyebilir - bu ucu kapanmanin tek garantisi.
    if (ozetKapatBtn) ozetKapatBtn.addEventListener("click", modalKapat);
    ozetModal.addEventListener("click", function (e) {
      if (e.target === ozetModal) modalKapat();   // backdrop'a tiklama
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
