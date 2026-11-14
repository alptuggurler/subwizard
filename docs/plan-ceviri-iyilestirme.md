# Çeviri iyileştirme planı (2026-11-13)

Dışarıdan gelen 4 maddelik önerinin koda göre değerlendirmesi ve uygulama sırası.
Henüz kod yazılmadı. Her adım bitince: ilgili testler, tam test, commit, push, CLAUDE.md durumu.

## Önce: ölçüm (adım 0)

Önerilerdeki kazanç rakamları (%50–60, 80–150 ms) tahmin. Ücretli Gemini ile ilk oturumdan
sonra logdan ve `subtitles.sqlite`'tan şunlar sayılmalı:

- Toplam istek, önbellek isabeti, yedeğe düşüş (`Çeviri oturum özeti` satırı).
- "Yarım cümle birleştirildi" satırı sayısı: birleştirmenin kaç ek istek getirdiği.
- Önbellekte birebir olmayan ama çok benzer kaynak metinler (aynı satırın OCR varyasyonu).
- Gemini gecikmesi p50 / p95 (log satırlarındaki ms).

İstenirse `scripts/analyze_session.py` (log + sqlite → kısa rapor) yazılır. Aşağıdaki sıra bu
sayılara göre değişebilir.

## 1. Yakın eşleşmeli önbellek — YAPILACAK (değiştirilmiş hâliyle)

Değerlendirme:
- Aynı satır ekrandayken OCR varyasyonu zaten yeni istek doğurmuyor: durum makinesi
  `similarity >= 0.85` olan okumaları aynı satır sayıyor (`SubtitleStateMachine.feed`).
  Kazanç yalnızca aynı satırın **sonradan tekrar** gelip farklı okunmasında (tekrar oynanan
  görev, NPC lafları).
- %92 Levenshtein tehlikeli: "I can go." / "I can't go." ya da "5 minutes" / "6 minutes"
  bu eşiği geçer ve yanlış çeviri gösterilir.

Plan:
1. `translator/cache.py`: `loose_key(text, scope)` ekle. OCR'ın karıştırdığı karakterleri
   tek karaktere indirger (`I l 1 |` → `l`, `O 0` → `o`, `rn` → `m`), noktalama ve boşluk
   farklarını atar; rakamlar ve kelime sayısı korunur.
2. SQLite'ta ikinci tablo `aliases (loose_key PRIMARY KEY, key)`; bellekte de aynı eşleme.
   `put` ikisini de yazar.
3. `TranslationService.translate`: birebir anahtar → yoksa `loose_key` → yoksa motor.
   Yakın eşleşme logda ve sonuçta ayrı işaretlenir (`cached`, motor adı "(önbellek~)").
4. rapidfuzz eklenmez. Ölçüm hâlâ kaçırma gösterirse ikinci aşama: aynı profil ve benzer
   uzunluktaki son N kayıtta rapidfuzz, **yalnızca** fark karıştırılan karakterlerdeyse ve
   olumsuzluk kelimeleri (not, n't, never, no) ile rakamlar aynıysa kabul.
5. Testler: varyasyon isabet eder; "can/can't", rakam farkı, farklı profil isabet etmez.

## 2. Terim hafızası — KISMEN VAR, öneri olarak genişletilecek

Var olanlar: profil `glossary` (`profiles/*.yaml`), `keep` terimleri, yedek motorda
`prompt.protect/restore`, ihlal logu + `M.TR_GLOSSARY_MISSES`, profile göre ayrı önbellek.

Değerlendirme:
- "Yalnızca cümlede geçen terimleri prompt'a koy" bugün gereksiz: gta6 profilinde ~6 terim
  var, tamamı sistem prompt'unda birkaç token. Sözlük büyüyünce anlamlı.
- Otomatik ekleme yanlış terim sokar ("Hey", cümle başı isimler). Argo (strap, fed) büyük
  harften yakalanamaz. Otomatik **öneri** + kullanıcı onayı doğru yol.

Plan:
1. `pipeline/corpus.py`: `term_candidates(game, min_count=3)` — cümle ortasında büyük harfle
   başlayan kelime/ikililer, birden çok oturumda görülenler, sözlükte olmayanlar; konuşmacı
   adları ayrıca.
2. Oyun Profilleri sayfası: "Önerilen terimler" listesi, her biri için "olduğu gibi bırak /
   şöyle çevir / yoksay". Onaylanan kullanıcının profil dosyasına yazılır
   (`<veri>/profiles/<oyun>.yaml`), yoksayılan hatırlanır.
3. `translator/prompt.py`: sözlük `GLOSSARY_INLINE_MAX` (ör. 30) terimden büyükse sistem
   prompt'una koyma; `user_prompt` içinde yalnızca o satırda geçenleri ekle. Küçükse bugünkü
   gibi kalır.
4. Testler: aday çıkarımı, eşik, prompt'a yalnızca eşleşenlerin girmesi.

## 3. Google Cloud Translation — YEDEK olarak yapılacak, yönlendirme isteğe bağlı

Değerlendirme:
- Asıl değeri: bugünkü yedek (Google kazıyıcı) bu ağda 429 veriyor, MyMemory'nin günlük
  sınırı küçük. Resmi API güvenilir bir yedek olur.
- "≤ 3 kelime Google'a" yönlendirmesi riskli: "Get down!", "You fed?" gibi diyaloglar
  bağlamsız mekanik çevrilir. Kısa HUD yazıları zaten tekrar ettiği için çoğu önbellekten
  gelir. "Sıfır halüsinasyon" ve 80–150 ms ölçülmedi.
- Google Cloud faturalandırma hesabı ister; kullanıcı şu an Gemini ön ödemesine geçiyor.
  Kullanıcı Cloud anahtarı alana kadar bekleyebilir.

Plan:
1. `translator/google_cloud.py`: `GoogleCloudTranslator(BaseTranslator)`, Translation v2 REST
   (`https://translation.googleapis.com/language/translate/v2`, API anahtarı ile; ağır
   `google-cloud-translate` paketi yerine httpx). Uç nokta ve alanlar yazmadan önce resmi
   dokümandan doğrulanacak. `keep` terimleri `protect/restore` ile.
2. Anahtar `core/secrets.py` üzerinden, ayrı isim (Gemini anahtarıyla karışmasın).
3. `translator/deep.DeepTranslatorFallback`: anahtar varsa zincirin başına
   `("google_cloud", …)`.
4. `config.TranslatorConfig`: `route_short_to: none | google_cloud` (varsayılan none),
   `route_short_max_words: 3`. `TranslationService.translate` içinde yalnızca açıkken.
5. Fiyat `config._default_prices` (karakter bazlı; maliyet hesabı karakterle uyarlanmalı).
6. Çeviri sayfası: anahtar alanı; i18n tr/en; config.example.yaml.
7. Testler sahte HTTP ile; gerçek anahtarla deneme kullanıcının makinesinde.

## 4. Typewriter debounce — YAPILMAYACAK (yanlış teşhis), yalnızca ölçülecek

Değerlendirme:
- Harf harf uzayan satır zaten çözülü: satır en az iki okumada (`stable_repeats: 1`, 4 Hz'de
  ≥ 250 ms) görülmeden kesinleşmez; uzayan satır `extends` ile öncekinin yerine geçer;
  çeviri kuyruğu yalnızca en yenisini tutar.
- 120–150 ms debounce, OCR'ın 250 ms okuma aralığından kısa: hiçbir şeyi değiştirmez.
- `FragmentJoiner`'ın ek istekleri typewriter değil, 2–4 sn arayla gelen ayrı ekranlardan.
  Beklemek, her parçanın Türkçesini saniyelerce geciktirir. Ek istek satır başına
  ~$0.0001; saatlik maliyete etkisi cent altı.

Plan: adım 0'daki sayımda birleştirme istekleri tüm isteklerin büyük kısmıysa yeniden ele
alınır (ör. parça yalnızca kısa bir süre içinde devamı gelmezse tek başına çevrilir).

## 5. GameTranslator'dan alınan fikirler (2026-11-14)

Kaynak: gitlab.com/sefflex/GameTranslator (Swift + C#, **AGPL-3.0**). Kod kopyalanmaz, fikirler
kendimiz yazılır (kopya SubWizard'ı AGPL yapar). Hazır oyun yamaları (yama gruplarının
metinleri) ve satırları sunucuya gönderen "ortak havuz" alınmaz.

### 5a. Altyazı yığını + yumuşak geçiş — ÖNCELİKLİ (kullanıcı: "daha smooth otursun")
- Yeni satır gelince önceki satır üstte küçük (~%75) ve soluk (~%50) kalır; en fazla 3 satır;
  eskiler ~0,3 sn'de solarak kalkar. Yeni satır ~0,15 sn'de belirir.
- Alt kenar sabit bir çizgiye oturur (`VideoView._anchor` satır başına değil, yığın başına);
  yığın yukarı doğru büyür, satırdan satıra zıplamaz.
- Yer: yeni Qt'siz `pipeline/subtitle_stack.py` (zamanı dışarıdan alır, test edilebilir) +
  `ui/subtitle_paint.render_subtitle` (yığın çizimi) + `VideoView._draw_subtitle`,
  `ScreenOverlay` (geçiş süresince zamanlayıcıyla yeniden çizim; şu an yalnızca değişimde çiziyor).
  Web sayfası (`web/static/index.html`) aynı kuralları CSS geçişiyle uygular.
- Ayarlar: `overlay.stack_lines` (1–3, varsayılan 2), `overlay.fade_ms` (0 = kapalı).
- Varsayılan yazı boyutu %4,5 → %3,5.

### 5b. Uzunluğa göre ekranda kalma süresi — ÖNCELİKLİ
- `SubtitleBoard.cleared`: `min_display_ms` sabiti yerine
  `clamp(base + karakter × ms_per_char, min, max)` (ör. 2000 + 60 × karakter, en çok 8000).
- Ayar: `subtitle.ms_per_char` (0 = bugünkü davranış).

### 5c. Noktalamayla biten satırı beklemeden gönder — KÜÇÜK, HIZLI
- `SubtitleStateMachine.feed`: `stable_repeats` beklemesi, satır `.!?…` ile bitiyorsa atlanır
  (`translate_worker.sentence_open` tersi; tek kaynakta tutulmalı). Satır başına ~250 ms kazanç.
- Risk: typewriter satırı tam cümle bitişinde yakalanırsa sorun yok; ortada nokta varsa
  ("Mr. Smith") kısaltma listesi gerekir.

### 5d. OCR çöp temizliği
- `SubtitleStateMachine.clean` içinde, `strip_other_scripts`'ten sonra: harf + rakam + simge
  karışımı kelimeler atılır ("ou$3e"); gerçek kelimeye yapışmış çöpte baştaki ≥3 harf kalır;
  olağan karışımlar korunur (PS5, R2, 2nd, 100m, $500, 4K, 1980s, 10:30pm).
- Satır başına yapışan HUD sayısı/mesafesi ("23m CESARE: Here!", "9? We'll lose") atılır.
- Testler: tablo halinde korunan / atılan örnekler.

### 5e. Çok konuşmacılı blok ve diyalog tireleri
- Bir okumada iki konuşmacı ("JASON: ... LUCIA: ...") ya da tireli satırlar ayrı parçalar
  olarak çevrilir, ekranda alt alta gösterilir. Alt satıra sarılmış tek cümle birleştirilir.
- `subtitle.speaker_pattern` "OBJECTIVE:", "NOTE:", "HINT:" gibi başlıkları konuşmacı saymaz
  (yasak kelime listesi).
- Yer: `SubtitleStateMachine.split_speaker` → parça listesi; `SubtitleLine` birden çok
  (konuşmacı, metin) taşır; `TranslationRequest` / prompt parça başına satır.

### 5f. "Ekranı çevir" kısayolu
- Bir tuşla tüm kare bir kez okunur, yazılar yerlerine göre gruplanır (menü, görev paneli,
  bilgi kutusu), her grup çevrilip kendi kutusunun üstüne çizilir; birkaç sn sonra ya da
  tuşa yeniden basınca kalkar. Grup sınırı (ör. 30).
- Yer: yeni `ocr/screen_read.py` (gruplama, Qt'siz) + `MainWindow._actions` + çizim
  `VideoView`. Çeviri tek istekte, gruplar numaralı satırlar olarak.

### 5g. Yerel çeviri modeli (yedek, Windows dahil) — 3. maddeyle birlikte
- OPUS-MT / MarianMT EN→TR, ONNX Runtime ile (RapidOCR için zaten bağımlılık). Model ilk
  kullanımda indirilir; lisansı ve indirme adresi uygulamadan önce doğrulanır.
- Yedek zincirinde Google/MyMemory'den sonra, internetsiz de çalışır.
- Bu motorlar için (bağlamsız motorlar):
  - Terim koruma: jeton (`ZQX0`) yerine uydurma özel ad; çeviriden sonra Türkçe ek terime göre
    yeniden kurulur (ünlü uyumu, kaynaştırma, sertleşme: "Lucia'yı", "Vice City'de"). Ad
    çeviride kaybolursa korumasız çeviriye dönülür.
  - Türkçe düzeltmeler: kaynaktaki `! ? …` sonu korunur, noktalamadan önceki boşluk silinir.
  - ≤3 kelimelik satır önceki cümleyle birlikte çevrilip ayrılır (sen/siz kaymasın).
- Gemini'ye uygulanmaz (prompt'u ve bağlamı zaten var).

### Alınmayanlar
- Remote Play + TV uygulaması mimarisi: bizim yolumuz capture card; TV'de göstermek için web
  overlay (`/`) TV tarayıcısında açılabilir.
- Dakikalık istek bütçesi (`RequestBudget`): ücretli Gemini'ye geçilince gereksiz.
- README'deki gecikme rakamları (0 ms, Gemini ~150 ms) ölçüm değil; bizim ölçümümüz 500–900 ms.

## Uygulama sırası

| Sıra | İş | Neden |
|---|---|---|
| 0 | Ücretli oturum + ölçüm + ekran görüntüsü | Kazançlar tahmin; yerleşim sorunlarını görmek |
| 1 | 5a altyazı yığını + yumuşak geçiş, 5b ekranda kalma süresi | Kullanıcının en çok şikâyet ettiği şey |
| 2 | 5c noktalamada beklemeden gönder, 5d OCR çöp temizliği | Küçük, her satırda kazanç |
| 3 | Önbellek `loose_key` (1. madde) | Küçük, güvenli |
| 4 | 5e çok konuşmacılı blok | Orta boy; durum makinesi ve prompt değişir |
| 5 | Google Cloud yedeği (3) + yerel model (5g) | Yedek zinciri güvenilir olur |
| 6 | 5f ekranı çevir, terim önerileri (2) | Yeni özellikler; veri biriktikçe |
| — | Typewriter debounce | Yapılmayacak; ölçüm aksini gösterirse yeniden bakılır |
