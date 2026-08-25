# SAP Proje Radarı — Kurulum ve Kullanım

Bu program, internetteki iş ve proje panolarından **SAP/ABAP projelerini** toplar, uzaktan
çalışılabilen ve proje bazlı olanları en üste çıkarır. Bilgisayar açıkken kendi kendine
çalışır, yeni ilan düştükçe listeyi günceller.

---

## Kurulum

1. `SAPProjeRadari-Setup.exe` dosyasına çift tıklayın.
2. Windows "Bilinmeyen yayımcı" uyarısı verirse: **Daha fazla bilgi → Yine de çalıştır**.
   (Program imzalı değil; içeriği bize ait.)
3. Kurulum sihirbazında iki kutu var, ikisini de işaretlemenizi öneririz:
   - **Masaüstüne kısayol ekle**
   - **Windows açılışında otomatik başlat** — bilgisayar her açıldığında arka planda çalışır.
4. **Kur → Bitir.** Program açılır ve ilan toplamaya başlar.

---

## Kullanım

Program açıldığında küçük bir pencere gelir:

| Düğme | Ne yapar |
|---|---|
| **Başlat / Durdur** | Taramayı açar veya durdurur |
| **Paneli Aç** | İlan listesini tarayıcıda açar (asıl çalışma ekranı) |
| **Şimdi Tara** | Sıradaki taramayı beklemeden hemen çalıştırır |
| **Excel'e Aktar** | Listeyi Excel dosyası olarak kaydeder |

Üstteki sayılar: kaç aktif ilan var, kaçı uzaktan, kaçı proje bazlı, son 24 saatte kaç yeni ilan geldi.

**Pencereyi kapatmak programı kapatmaz** — saatin yanındaki tepsi ikonuna iner, taramaya devam
eder. Tamamen kapatmak için tepsi ikonuna sağ tıklayıp **Çıkış** deyin.

---

## Panel (asıl çalışma ekranı)

"Paneli Aç" ile açılan sayfada:

- **Üst kartlar:** uzaktan / hibrit / proje bazlı / son 24 saat / kontrol ediliyor / kapandı.
  Tıklayınca o filtre uygulanır.
- **Hızlı filtreler:** "Uzaktan + proje bazlı", "Son 3 gün", "Yeni düşenler", "Takip listem",
  "Bütçesi belli", "AB ihaleleri".
- **Her ilan kartında:** uygunluk skoru (0–100), çalışma şekli, bütçe, ilan tarihi, kaynak.
- **Satır düğmeleri:** Takip (ilgilendiklerimiz), Başvurdum, Gizle.
- Yeni ilan geldiğinde alttan **"N yeni ilan geldi"** şeridi çıkar.

### Sıralama ve tarih aralığı

"Filtreler" panelini açınca:

- **Sırala:** skora göre, çalışma şekli önceliğine göre (uzaktan + proje bazlı en üstte),
  bütçeye göre (yüksekten ya da düşükten), ilan tarihine göre (yeniden eskiye ya da tersi),
  firmaya göre. Bütçesi yazmayan ilanlar bütçe sıralamasında hep sona gider.
- **Tarih aralığı:** "başlangıç" ve "bitiş" kutularına iki tarih yazınca sadece o aralıktaki
  ilanlar listelenir. Yanındaki seçim, aralığın **ilan tarihine** mi yoksa ilanın **sisteme
  düştüğü tarihe** mi bakacağını belirler. Aralık girdiğinizde "Yayın tarihi" (son 7 gün gibi)
  seçimi devre dışı kalır.

---

## Ayarlar sekmesi

Üstteki **Ayarlar** bağlantısı, eskiden dosya düzenlemek gereken her şeyi tek ekrana getirir.

| Bölüm | Ne yapabilirsiniz |
|---|---|
| **Ne arıyoruz** | Aranan konuyu değiştirirsiniz — kelimeler, sorgular, puanlar |
| **Kaynaklar** | Hangi iş panosunun taranacağını açıp kapatabilirsiniz |
| **API anahtarları** | Bir sitenin anahtarını değiştirebilir ya da silebilirsiniz |
| **Tarama** | Ne sıklıkla taranacağını ayarlayabilir, otomatik taramayı durdurabilirsiniz |
| **Yeni kaynak ekle** | Listede olmayan bir iş sitesini kendiniz ekleyebilirsiniz |

### Aranan konuyu değiştirmek

Program bugün SAP/ABAP arıyor. Yarın başka bir konuya çevirmek isterseniz **Ne arıyoruz**
bölümü yeterlidir — hiçbir dosya açmanız gerekmez:

1. **Arama sorguları** — sitelerde ne arattığımız. Örneğin `SAP ABAP remote` yerine
   `ERP yazılım geliştirme`, `ERP danışman` yazarsınız (her satıra bir tane).
2. **Zorunlu kelimeler** — en önemlisi. Bu kelimelerden **hiçbiri geçmeyen ilan listeye
   girmez**. `abap` / `sap` yerine `erp` yazarsanız program artık ERP ilanı toplar.
3. **Puan artıran kelimeler** — hangi kelime ilanı ne kadar yukarı çeksin (`erp: 8` gibi).
4. **Kaydet** deyince biriken bütün ilanlar yeni kurallara göre yeniden puanlanır; birkaç
   saniye sürer, internete çıkmaz.

> Zorunlu kelimeleri boş bırakamazsınız — bırakılırsa hiçbir ilan elenmez ve liste
> alakasız ilanlarla dolar. Program buna izin vermez.

### Yeni kaynak eklemek (kod yazmadan)

Çoğu iş sitesi ilanlarını bir **RSS akışı** ya da **JSON adresi** üzerinden verir.
Elinizde böyle bir adres varsa kaynağı kendiniz eklersiniz:

1. **Kaynağın adı** — listede görünecek isim.
2. **Tür** — RSS/Atom ya da JSON.
3. **Adres** — arama kelimesinin gireceği yere `{sorgu}`, sayfa numarasının gireceği yere
   `{sayfa}` yazın. Örnek: `https://site.com/jobs.rss?search={sorgu}`
4. **JSON seçtiyseniz:** ilanların hangi alanda durduğunu (`data` gibi) ve hangi alanın
   başlık, hangisinin link olduğunu yazarsınız. RSS'te bunlar standarttır, boş bırakın.
5. **"Bu tanımı dene"** — kaydetmeden sınar. Yanlış bir şey varsa ne olduğunu söyler,
   hatta ilanın içinde hangi alan adlarının bulunduğunu listeler; oradan doğrusunu
   yazarsınız.
6. Çalışıyorsa **"Kaynağı kaydet"**. Kaynak listeye girer ve sıradaki taramaya katılır.

Eklediğiniz kaynağı sonradan kapatabilir ya da **Sil** ile kaldırabilirsiniz. Programla
gelen kaynaklar silinemez, sadece kapatılır.

**"Veriyi kontrol et"** düğmesi her kaynağın altında durur: o siteye anında bağlanıp kaç ilan
geldiğini ve ilk birkaç başlığı gösterir. Bir şey ters giderse Türkçe açıklar ("Anahtar reddedildi",
"Kaynak erişimi engelledi", "Anahtar tanımlı değil"). Bu kontrol **listeye hiçbir şey eklemez**,
sadece dener.

> Anahtar değiştirdikten sonra "Anahtarları kaydet" deyip ardından "Veriyi kontrol et"e basın —
> kontrol her zaman **kayıtlı** ayarlarla çalışır.

Kaydettiğiniz her şey program klasöründeki `settings.local.yaml`, `keywords.local.yaml` ve `.env`
dosyalarına yazılır. Klasörü başka bir bilgisayara ya da sunucuya kopyalarsanız ayarlar da gider.

---

## Nasıl çalışıyor

- **8 dakikada bir** hızlı tarama: yeni düşen ilanları yakalar.
- **30 dakikada bir** tam tarama: bütün kaynaklar taranır, kapanan ilanlar tespit edilir.
- Bir ilan kaynağın listesinden düşerse hemen silinmez; önce linki açılıp kontrol edilir
  (panelde "KONTROL EDİLİYOR"). Gerçekten kapanmışsa listeden düşer.

**Kaynaklar:** Jooble, freelancermap, freelancer.com, Upwork, Reed.co.uk, Careerjet,
Arbeitnow, kariyer.net ve AB kamu ihale portalı TED.

---

## Sık sorulanlar

**Program bilgisayarı yavaşlatır mı?**
Hayır. Arada bir internetten liste çeker, arada uyur; işlemci ve bellek kullanımı çok düşüktür.

**İnternet giderse?**
Erişilemeyen kaynak atlanır, diğerleri çalışmaya devam eder. Bağlantı gelince kendiliğinden toparlar.

**Veriler nerede?**
`C:\Users\<kullanıcı>\AppData\Local\SAP Proje Radari` klasöründe. Ayarlar sekmesindeki
**Klasörü Aç** düğmesiyle ulaşabilirsiniz.

**Kaldırmak istersem?**
Ayarlar → Uygulamalar → SAP Proje Radarı → Kaldır. Toplanan ilan veritabanının da silinip
silinmeyeceğini sorar.

---

Sorun olursa: `AppData\Local\SAP Proje Radari\output\logs` klasöründeki günlük dosyası
ne olduğunu yazar.
