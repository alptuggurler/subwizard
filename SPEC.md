# SubWizard — Live Game Subtitle Translator — Proje Şartnamesi (v5)

> Bu dosya Claude Code için hazırlanmıştır. Repo köküne `SPEC.md` olarak koy; `CLAUDE.md` içinden referans ver.
> Kod, değişken ve dosya adları İngilizce; kullanıcıya görünen metinler ve log mesajları Türkçe olabilir.

## 0. Özet

PS5'ten capture card ile PC'ye gelen oyun görüntüsündeki İngilizce altyazıları OCR ile okuyup LLM ile doğal Türkçeye çeviren ve görüntünün üstüne basan açık kaynak bir Python aracı. İlk hedef oyun GTA 6 (PS5, 19 Kasım 2026), ama araç oyundan bağımsız olmalı.

**Öncelik sırası:** 1) Oynanabilirlik (görüntü/ses gecikmesi) → 2) Altyazının zamanında görünmesi → 3) Çeviri kalitesi → 4) Maliyet → 5) Platform genişletilebilirliği.

### Görüntüleme modları
- **Mod A — PC penceresi:** PS5 → capture card → PC. Oyun SubWizard penceresinde oynanır, altyazı görüntünün üstüne basılır. Ses PC'den çıkar (bkz. 3.2). Kurulumu en basit mod, ama PC'nin görüntü gecikmesi oynanışa eklenir ve HDR/120Hz kaybolur.
- **Mod B — Loop-out + harici altyazı ekranı:** PS5 → capture card → loop-out → TV. Oyun TV'de gecikmesiz ve orijinal kalitede oynanır. PC yalnızca görüntüyü okur. Çeviri yerel ağdaki bir web sayfasında yayınlanır (bkz. 3.10). Bu sayfa TV'nin altındaki tablet/telefonda, ikinci monitörde ya da OBS'de açılabilir.
- Her iki mod aynı pipeline'ı kullanır; sadece çıktı hedefi değişir. İkisi aynı anda da açık olabilir.

**Hedef kitle:** Programcı olmayan oyuncular. Kurulum ve kullanım tamamen arayüzden yapılır; config dosyası düzenlemek veya terminal açmak gerekmez. CLI sadece geliştirici/ileri kullanıcı içindir.

**Lisans:** GPL-3.0 (PySide6-Fluent-Widgets'ın ücretsiz sürümü GPL-3.0 olduğu için; bkz. 1. bölüm). Aşağıdaki "İlham alınan projeler" bölümündeki AGPL/GPL lisanslı projelerden **kod kopyalanmaz**, sadece fikir alınır.

**Kapsam dışı (şimdilik):** macOS/Linux implementasyonları, PC oyunu ekran yakalama modu (Faz 2). İlk ve tek hedef platform **Windows 10/11 (x64)**. Kod platform soyutlamasını korur, ama başka platform için test/paketleme yapılmaz.

## 1. Teknik Kararlar

| Konu | Karar |
|---|---|
| Python | 3.12, ortam yönetimi `uv` |
| Platform | Windows 10/11 (Faz 1). Platforma özel kod yalnızca `src/platform/windows/` altında |
| Video kaynağı | OpenCV `cv2.CAP_DSHOW`, fallback `cv2.CAP_MSMF`. Cihaz adları ve desteklenen modlar (çözünürlük/FPS/format) için `pygrabber` veya `ffmpeg -list_options` |
| Sistem metrikleri | `psutil` (CPU/RAM), GPU için opsiyonel `pynvml` (NVIDIA) |
| Ses | `sounddevice` (WASAPI) ile capture card ses girişini varsayılan çıkışa aktarma |
| OCR | `rapidocr-onnxruntime` (varsayılan), Windows OCR (`winrt-Windows.Media.Ocr`, hızlı ve hafif alternatif), `pytesseract` (opsiyonel) |
| Çeviri | `google-genai` SDK, model **config'den** okunur (varsayılan aşağıda) |
| Fallback çeviri | `deep-translator` (GoogleTranslator) |
| Arayüz | **PySide6 (Qt 6)** + **PySide6-Fluent-Widgets** (Windows 11 Fluent görünümü, Mica efekti, koyu/açık tema). Lisans GPL-3.0; proje lisansı buna göre GPL-3.0. Ticari kapalı kaynak düşünülürse Fluent-Widgets çıkarılıp düz PySide6 + QSS tema kullanılır |
| Video render | Qt `QOpenGLWidget` (veya `QWidget` + `QPainter`); kareler numpy → `QImage` (kopyasız) |
| Altyazı çizimi | `QPainter` + `QPainterPath` (kontur/gölge), Qt font motoru. Türkçe karakter desteği yerleşik; Pillow gerekmez |
| Paketleme | Nuitka (standalone) veya PyInstaller → Inno Setup ile `SubWizard-Setup-x.y.z.exe`; ayrıca taşınabilir `.zip` |
| Hotkey | `pynput` |
| Web overlay | `aiohttp` (HTTP + WebSocket), tek statik HTML sayfası |
| TTS (opsiyonel) | `edge-tts` veya Windows SAPI (`pyttsx3`) |
| Config | `config.yaml` + `.env` (`GEMINI_API_KEY`), `pydantic-settings` ile doğrulama |
| Test | `pytest`; örnek kare/video fixture'ları `tests/fixtures/` |

## 2. Mimari ve Veri Akışı

```
[Source thread] --latest frame--> [Render loop (main thread, 60fps)]
                      |                         ^
                      v                         | subtitle patch (RGBA)
             [OCR worker, 3-4 Hz] --text--> [Subtitle state machine] --stable line--> [Translator worker]
                                                                                           |
                                                       [Overlay renderer] <--translation---+
```

**Kurallar:**
- Thread'ler arası kare paylaşımı **"latest-only"**: `maxsize=1` kuyruk veya kilitli tek slot. Eski kare asla birikmez.
- Qt ana thread'i (UI + video render) hiçbir zaman OCR, ağ çağrısı veya disk işlemi beklemez. Worker'lar `QThread`/`threading` ile çalışır, UI'ya **sadece Qt sinyalleriyle** veri gönderir.
- Altyazı metni **yalnızca çeviri değiştiğinde** bir `QPixmap`'e çizilip önbelleklenir; her karede sadece bu pixmap video üstüne basılır.
- Video karesi her `paintEvent`'te en son kareyle çizilir. Ekran yenilemesi için `QTimer` yerine kaynak thread'inden gelen "yeni kare" sinyali kullanılır.
- Tüm bileşenler soyut taban sınıflardan türer: `BaseSource`, `BaseOCR`, `BaseTranslator`, `BaseAudioPassthrough`.

## 3. Bileşenler

### 3.1 Kaynaklar (`src/sources/`)
- `CaptureCardSource`:
  - Cihaz listeleme (`--list-devices`), cihaz index'i veya adıyla seçim.
  - Özellik ayarlama sırası önemli: önce `FOURCC=MJPG`, sonra `WIDTH=1920`, `HEIGHT=1080`, `FPS=60`, `BUFFERSIZE=1`. Ayardan sonra gerçek değerleri geri okuyup logla; istenenle uyuşmuyorsa uyar.
  - Bağlantı koparsa (kare okunamazsa) otomatik yeniden bağlanma, exponential backoff.
- `VideoFileSource`: **Geliştirme ve test için zorunlu.** Kayıtlı bir `.mp4`'ü gerçek zamanlı hızda oynatır (`--source file --path clip.mp4`). Böylece konsol açmadan tüm pipeline test edilir.
- `ScreenSource` (Faz 2, şimdilik sadece arayüz stub'ı).

### 3.2 Ses aktarımı (`src/audio/`)
Oyun PC penceresinden oynanacaksa ses de PC'den çıkmalı; capture card sesi ayrı bir giriş cihazıdır.
- Mod `audio.mode` config'den seçilir:
  - `passthrough`: `sounddevice` ile capture card ses girişi → varsayılan çıkış, düşük gecikmeli blok boyutu (WASAPI).
  - `off`: Kullanıcı Windows'ta "Bu cihazı dinle" kullanıyor ya da loop-out ile TV'den dinliyor.
- `audio.delay_ms`: Video pipeline gecikmesine göre ses/görüntü senkronu için ince ayar (-200…+500 ms; ses ancak geciktirilebilir, negatif değer görüntüyü bekletmez, sadece uyarı verir).
- Ayarlanabilir: Giriş cihazı, çıkış cihazı, örnekleme hızı (vars. 48 kHz), kanal (stereo), blok boyutu / gecikme profili (`low` / `balanced` / `safe`), seviye (dB, -∞…+6), sessize alma.
- Ses akışı tamponu taşarsa/boşalırsa (underrun/overrun) sayılır ve tanılama ekranında gösterilir; sık oluyorsa otomatik olarak bir üst gecikme profiline geçmesi önerilir.

### 3.2.1 Tanılama ve canlı metrikler (`src/subwizard/core/metrics.py`)
Kurulumu doğru yapmak ve hata ayıklamak için tüm aşamalar ölçülür. Metrikler tek bir `MetricsHub`'da toplanır (kayan pencere, son 2 sn ve 60 sn ortalamaları), UI'ya saniyede 4 kez sinyal olarak gider.

**Video metrikleri**
- **Kaynak FPS** (capture card'dan gerçekten okunan kare/sn) ve **render FPS** (ekrana çizilen kare/sn) ayrı ayrı.
- **İstenen ve gerçekleşen mod:** ör. "İstenen 1920×1080@60 MJPG → Gerçekleşen 1920×1080@30 YUY2". Uyuşmazlıkta sarı uyarı + çözüm önerisi ("USB 3.0 porta tak", "MJPG seç").
- **Kare zamanlaması:** Kareler arası süre (ms) grafiği, ortalama/en kötü, jitter. **Düşen/tekrarlanan kare** sayacı (zaman damgasına ve kare hash'ine göre).
- **Kare yaşı:** Karenin okunmasından ekrana çizilmesine kadar geçen süre (ms).
- Cihazın desteklediği tüm modların listesi; kullanıcı buradan mod seçebilir.

**Ses metrikleri**
- **Seviye göstergesi (VU / peak meter)** sol-sağ kanal, dBFS; clipping (0 dB) kırmızı uyarı. "Ses gelmiyor" durumu (3 sn boyunca -60 dB altı) için uyarı.
- Gerçekleşen örnekleme hızı, blok boyutu, tahmini ses gecikmesi (ms), underrun/overrun sayacı.
- **A/V senkron testi:** Senkron ayar ekranında görüntüdeki ani parlaklık değişimi (ör. sahne geçişi / patlama) ile sesteki ani yükselme (transient) yakalanıp aradaki fark ölçülür ve `audio.delay_ms` için **öneri** sunulur. Manuel kaydırıcı her zaman var; değişiklik anında duyulur.

**Pipeline metrikleri**
- OCR: tetiklenme sayısı/sn, atlanan (değişim yok) oran, süre (ms), güven skoru.
- Çeviri: istek süresi (ms, p50/p95), önbellek isabet oranı, motor (Gemini/yedek), hata sayısı, token ve tahmini maliyet.
- **Uçtan uca gecikme:** Altyazının ROI'de ilk görüldüğü andan Türkçenin ekrana basılmasına kadar (ms).

**Sistem**
- CPU ve RAM kullanımı (SubWizard süreci + toplam), opsiyonel GPU kullanımı.

**Gösterim yerleri**
- **Performans overlay'i (F9):** Video üstünde köşede, MSI Afterburner tarzı yarı saydam kutu. Seviyeler: `kapalı` → `kompakt` (FPS, gecikme) → `detaylı` (tüm metrikler + OCR ham metni + durum makinesi). F9 sırayla geçer.
- **Ana Sayfa durum çubuğu:** Kaynak FPS, uçtan uca gecikme, ses seviyesi mini göstergesi, maliyet.
- **Tanılama sayfası:** Tüm metrikler + son 60 sn grafikleri (FPS, kare aralığı, ses seviyesi, OCR/çeviri süresi). "Raporu kopyala" butonu (metin olarak; GitHub issue'ya yapıştırmak için) ve "Metrikleri CSV'ye kaydet".
- Renk kodu: yeşil (hedefte), sarı (sınırda), kırmızı (sorun). Eşikler config'de.

### 3.3 ROI seçici ve ön işleme (`src/ocr/`)
- `--calibrate` veya `F2`: Donmuş kare üzerinde fareyle dikdörtgen çiz, Enter ile onayla, Esc ile iptal et. Koordinatlar **oransal** (0-1) olarak `config.yaml`'a yazılır; böylece çözünürlük değişse de geçerli kalır.
- **Hazır ROI şablonları** (`F2` menüsünde veya config'de): `bottom_center` (standart altyazı), `bottom_wide` (geniş alt şerit), `lower_half` (ekranın alt yarısı), `top` (görev/objektif metinleri), `custom`.
- **Çoklu ROI:** En fazla 4 bölge (ör. altyazı + üstteki görev metni). Her bölgenin adı, OCR sıklığı ve overlay konumu ayrıdır.
- **Hariç bölgeler:** ROI içinde OCR'a girmemesi gereken alanlar (ör. mini harita kenarı, HUD sayaçları) dikdörtgen maskeyle çıkarılır.
- `roi.padding_pct`: Kesik kelimeleri önlemek için seçilen alan her yönde biraz genişletilir (vars. %4).
- Ön işleme **motor başına** yapılandırılabilir olmalı:
  - RapidOCR: Varsayılan hafif işlem (grayscale + gerekirse 2x upscale). Eşikleme kapalı.
  - Tesseract: grayscale + kontrast germe + Otsu/adaptif eşik + 2x upscale.
  - `preprocess.white_text_mask` (opsiyonel): Parlak/beyaz metni HSV ile ayıran maske. Oyun altyazıları genelde beyaz ve gölgelidir.
- **Değişim algılama:** OCR'dan önce ROI'nin küçültülmüş gri versiyonunda önceki tick'e göre ortalama mutlak fark hesaplanır. Eşiğin altındaysa OCR atlanır ve son sonuç tekrar kullanılır. CPU'yu ciddi oranda düşürür.

### 3.4 OCR worker
- 3-4 Hz (config: `ocr.hz`). Kendi thread'inde çalışır.
- Çıktı: satırlar, birleştirilmiş metin, ortalama güven skoru.
- `ocr.min_confidence` altındaki satırlar atılır.
- Normalizasyon: Fazla boşlukları sil, satırları birleştir, sık OCR hatalarını düzelt (`|`→`I`, `0`↔`O` gibi yalnızca bağlama göre güvenli olanlar).

### 3.5 Altyazı durum makinesi (`src/pipeline/subtitle_state.py`)
Projenin kalbi. Şartnamenin ilk versiyonundaki "benzerlik %85" kuralını yerini alır:

```
EMPTY --metin görüldü--> CANDIDATE --aynı metin (≥0.85 benzerlik) N kez daha (vars. 1)--> STABLE --> çeviriye gönder
STABLE --farklı metin--> CANDIDATE
STABLE/CANDIDATE --ROI boş, K tick (vars. 2)--> EMPTY --> overlay min_display_ms dolunca temizlenir
```
- Aynı STABLE satır ikinci kez çeviriye **gönderilmez**.
- Altyazı ekranda büyüyerek yazılıyorsa (yeni metin eskinin uzantısıysa) eski istek iptal edilir, uzun hali çevrilir.
- **Yoksayılan ifadeler listesi** (`ignore_phrases`, regex destekli): Sürekli ekranda kalan ve çevrilmemesi gereken metinler (ör. "Press ⓧ to skip", buton ipuçları).
- Gürültü filtresi: 3 karakterden kısa, harf oranı %50'nin altında veya sadece sembol olan metinler atılır.
- Konuşmacı adı (`NAME:` deseni) varsa ayrılır, çevrilmez, overlay'de renkli/kalın gösterilir.

### 3.6 Çeviri (`src/translator/`)
- `GeminiTranslator`:
  - Model: `translator.model` config'den. **Varsayılan `gemini-3.5-flash-lite`** (en hızlı/ucuz), kalite yetmezse `gemini-3.6-flash`. Model ID'lerini kodlamadan önce güncel Gemini dokümanından doğrula.
  - Thinking/reasoning seviyesi **en düşük**. Bu iş için düşünmeye gerek yok ve gecikmeyi artırır. Parametre adını SDK dokümanından kontrol et.
  - Yeni modellerde `temperature/top_p/top_k` deprecated. Gönderme.
  - Güvenlik filtreleri: Argo/şiddet içeren oyun diyalogları için kategoriler en gevşek seviyeye çekilir. Yanıt yine de engellenirse veya boş dönerse → fallback çevirmen.
  - **Bağlam:** Son N satırın (vars. 5, `translator.context_lines`) kaynak ve çeviri geçmişi prompt'a eklenir. Hitap, isim ve ton tutarlılığı için.
  - **Birden fazla API key:** `GEMINI_API_KEYS` virgülle ayrılmış liste olabilir. 429/kota hatasında sıradaki key'e geçilir, hepsi doluysa fallback çevirmene düşülür.
  - Timeout (vars. 2.5 sn) + 1 retry. Timeout'ta fallback.
  - Streaming opsiyonel (`translator.stream: true`). İlk tokenlar gelince overlay güncellenir.
- System prompt (config'den override edilebilir):
  > Sen profesyonel bir oyun yerelleştirme çevirmenisin. Sana İngilizce bir oyun altyazısı ve önceki birkaç satırın bağlamı verilecek. Yalnızca son satırı doğal, akıcı, konuşma diline uygun Türkçeye çevir. Argo, küfür ve esprileri sansürlemeden, Türkçede karşılığı olan en doğal ifadeyle aktar. Özel isimleri (karakter, mekân, marka) çevirme. Açıklama, tırnak veya ek metin ekleme; yalnızca çeviriyi döndür.
- **Oyun profilleri** (`profiles/<oyun>.yaml`): Oyuna özel ROI, ignore listesi, prompt eki ve sözlük tek dosyada tutulur. `--profile gta6` ile seçilir. Profil içeriği:
  - `game_info`: Oyunun kısa tanımı, ana karakterler, mekân, ton. Prompt'a bağlam olarak eklenir. Örnek: "GTA 6, Vice City/Leonida. Ana karakterler Jason ve Lucia. Suç dünyası, sokak dili."
  - `glossary`: Terim sözlüğü (kaynak → hedef / `keep`). Karakter ve mekân adları `keep`, oyuna özgü argo için tercih edilen karşılıklar. Prompt'a "bu terimleri böyle çevir" olarak eklenir. Uygulama ayrıca çıktıda sözlük ihlali olup olmadığını loglar.
  - `prompt_extra`: Oyuna özel ek talimat.
- **Görsel çeviri modu (deneysel, `translator.mode: vision`):** OCR adımı atlanır. Kırpılmış ROI görüntüsü doğrudan Gemini'ye gönderilir, model hem okur hem çevirir. Prompt'ta konuşmacı adı ve HUD metninin yok sayılması istenir. Stilize fontlarda ve karışık arka planlarda daha iyi sonuç verebilir, ama istek başına maliyet ve gecikme daha yüksektir. Sadece değişim algılama tetiklendiğinde çağrılır. Normal modla A/B karşılaştırması için debug'da gecikme ve maliyet gösterilir.
- Önbellek: Normalize edilmiş kaynak metin üzerinden LRU (vars. 2000 kayıt) + oturumlar arası kalıcı disk önbelleği (`cache/translations.sqlite`).
- `DeepTranslatorFallback`: API key yoksa, ağ yoksa veya Gemini hata verirse. Overlay'de küçük bir "(yedek)" işareti gösterilir.
- Maliyet takibi: İstek sayısı ve token kullanımı loglanır. Debug overlay'de ve web overlay'in ayar panelinde canlı tahmini maliyet ($/saat) gösterilir; oturum sonunda özet basılır.

### 3.7 Overlay (`src/overlay/`)
- Font: Varsayılan `Segoe UI Variable` → `Segoe UI` → `Arial`. Kullanıcı arayüzden herhangi bir sistem fontunu seçebilir. Türkçe karakter testi (`ğüşıöçİ`) zorunlu.
- `SubtitleRenderer`: Word wrap (genişliğin max %80'i), yarı saydam yuvarlak köşeli arka plan, ince kontur/gölge, config'den font boyutu.
- Konum seçenekleri: `bottom` (orijinal altyazının hemen üstü), `top` veya `replace`. `replace` modunda OCR'ın döndürdüğü metin kutuları kullanılır: Orijinal yazının gerçek boyutu ve konumu ölçülür, çevresinin ortalama renginden bir arka plan oluşturulup (`overlay.auto_bg: true`) İngilizce metin kapatılır, Türkçe metin aynı yere basılır.
- Görünüm ayarları: Font boyutu, alt boşluk, arka plan opaklığı, metin/arka plan rengi, konuşmacı adı rengi.
- Pencere: Ana uygulama içindeki oynatıcı sekmesi veya ayrı oyun penceresi; `F11` çerçevesiz tam ekran yapar. Tam ekranda imleç 2 sn hareketsiz kalınca gizlenir.
- Performans/debug overlay'i (`F9`): Bkz. 3.2.1 — kapalı / kompakt / detaylı.

### 3.8 Hotkeyler (`src/utils/hotkeys.py`)
| Tuş | İşlev |
|---|---|
| F2 | ROI kalibrasyonu |
| F8 | Çeviriyi duraklat/devam |
| F9 | Performans overlay'i: kapalı → kompakt → detaylı |
| F10 | Altyazı overlay'ini gizle/göster |
| F11 | Tam ekran |
| F12 | Son 10 satırın geçmiş panelini aç/kapat (kaçırılan altyazılar için) |
| F7 | Mevcut satırı yeniden çevir (cache'i atlayarak, kötü çeviri görürsen) |
| F6 | PC sesini kapat/aç (Mod B'de ses ikiye katlanmasın) |
| F5 | Son çeviriyi panoya kopyala |

Tuşlar Ayarlar > Kısayollar ekranından "tuşa bas ve kaydet" yöntemiyle değiştirilir; çakışma uyarısı verilir. Global kısayollar `pynput` ile dinlenir (SubWizard odakta değilken de çalışır), callback'ler Qt sinyaliyle ana thread'e aktarılır.

### 3.12 Kullanıcı Arayüzü (UI/UX)
Açık kaynak projenin vitrini burası. README'deki ekran görüntüleri ve GIF'ler buradan çıkacak.

**Genel ilkeler**
- Windows 11 Fluent tasarım dili: Sol navigasyon çubuğu, Mica arka plan, yuvarlak köşeler, Segoe Fluent ikonları. Sistem temasını (koyu/açık) takip eder, varsayılan koyu.
- **Arayüz dili:** Türkçe ve İngilizce, ilk açılışta sistem diline göre seçilir. Tüm metinler `i18n/` altında (Qt `.ts` veya basit JSON); kodda sabit metin yok. Yeni dil eklemek için README'de rehber.
- Her ayar değişikliği anında uygulanır ve kaydedilir ("Kaydet" butonu yok). Riskli ayarlarda "Varsayılana dön" butonu bulunur.
- Hata mesajları teknik değil, çözüm önerili olur. Örnek: "Capture card bulunamadı. Kablonun USB 3.0 porta takılı olduğundan ve başka bir programın (OBS vb.) cihazı kullanmadığından emin ol."
- Uzun işlemler (model indirme, cihaz tarama) ilerleme göstergesiyle yapılır; UI donmaz.

**İlk açılış sihirbazı (Kurulum Sihirbazı — "Wizard" ismine yakışır)**
1. Hoş geldin + dil seçimi
2. Görüntüleme modu seçimi: "PC'de oyna" (Mod A) / "TV'de oyna, çeviriyi başka ekranda göster" (Mod B). Her biri kısa açıklama ve şema görseliyle.
3. Capture card seçimi: Cihaz listesi + **canlı önizleme**. Gerçekleşen çözünürlük, format ve **ölçülen FPS** gösterilir; 60'ın altındaysa sebep ve çözüm önerisi.
4. Ses: Mod A'da capture card ses girişi seçimi, **canlı VU göstergesi**, seviye ayarı ve test ("Sesi duyuyor musun?"). Ardından A/V senkron adımı: kaydırıcı + "Otomatik ölç". Mod B'de atlanır.
5. Çeviri motoru: Gemini API key alanı + "Anahtar nasıl alınır?" bağlantısı + **"Test et"** butonu (örnek bir cümleyi çevirip sonucu ve gecikmeyi gösterir). "Anahtarsız devam et (ücretsiz, daha düşük kalite)" seçeneği.
6. Oyun profili seçimi (GTA 6, Genel, ...) → ROI şablonu otomatik gelir.
7. Altyazı alanı kalibrasyonu: Canlı önizleme üstünde sürükle-bırak, şablon butonları, "Altyazı görünene kadar bekle ve dondur" butonu.
8. Özet + "Başla". Mod B'deyse QR kod ve URL burada gösterilir.
Sihirbaz sonradan Ayarlar'dan tekrar açılabilir.

**Ana pencere — sol navigasyon**
- **Ana Sayfa / Oynatıcı:** Büyük Başlat/Durdur butonu, mod göstergesi, canlı önizleme (Mod A'da oyun burada veya ayrı pencerede), durum kartları: Capture card (yeşil/turuncu/gri), Çeviri motoru, Web overlay bağlı cihaz sayısı. Altta durum çubuğu: FPS, uçtan uca gecikme, oturumdaki çeviri sayısı, tahmini maliyet.
- **Altyazı Geçmişi:** Oturumdaki tüm satırlar (zaman, İngilizce, Türkçe, motor). Arama, kopyalama, satırı yeniden çevirme, "Kötü çeviri" işaretleme (sonraki prompt'lara örnek olarak eklenebilir). Dışa aktarma: `.txt` / `.srt`.
- **Alan & OCR:** ROI düzenleyici (çoklu bölge, hariç bölgeler, şablonlar), OCR motoru seçimi, ön işleme seçenekleri + **canlı önizleme**: ham kırpım, işlenmiş görüntü ve OCR sonucu yan yana.
- **Çeviri:** Motor seçimi, model listesi, API key yönetimi (çoklu key, test butonu), bağlam satır sayısı, prompt düzenleyici (varsayılana dön butonlu), önbellek boyutu ve "önbelleği temizle".
- **Görünüm:** Font, boyut, renkler, arka plan opaklığı, konum (alt/üst/replace) — **canlı önizleme kartı** örnek bir Türkçe altyazıyla anında güncellenir. Hazır stiller: "Netflix", "Oyun içi", "Yüksek kontrast".
- **Oyun Profilleri:** Profil listesi; oluştur, kopyala, içe/dışa aktar (`.yaml`). Oyun bilgisi, sözlük (tablo editörü), ignore listesi.
- **Web Overlay:** Aç/kapat, port, QR kod, bağlı cihazlar, sayfa görünüm ayarları, "OBS için URL'yi kopyala".
- **Cihaz & Ses:** Capture card seçimi ve mod listesi, canlı önizleme, kaynak FPS / gerçekleşen mod kartı. Ses giriş/çıkış seçimi, VU göstergesi, seviye, gecikme profili, A/V senkron kaydırıcısı + "Otomatik ölç" butonu, "Sesi test et".
- **Tanılama:** 3.2.1'deki tüm metrikler ve grafikler, rapor kopyalama.
- **Kısayollar**, **Hakkında** (sürüm, güncelleme kontrolü, GitHub, log klasörünü aç, "Hata raporu için logları paketle" butonu).

**Diğer**
- Sistem tepsisi ikonu: Başlat/Durdur, overlay gizle/göster, çıkış. Kapatma butonu tercihe göre tepsiye küçültür.
- Güncelleme kontrolü: GitHub Releases API'si ile açılışta (kapatılabilir). Otomatik indirme yok, sadece bildirim.
- API key'ler düz metin config yerine **Windows Credential Manager**'da (`keyring` paketi) saklanır.
- Erişilebilirlik: Klavyeyle tam gezinme, yüksek kontrast teması, altyazı fontu için büyük boyut seçenekleri.
- UI mantığı ile çekirdek pipeline ayrı: `core/` paketi Qt'ye bağımlı değildir (test edilebilirlik ve ileride başka arayüz için). UI, çekirdeğe ince bir `AppController` üzerinden bağlanır.

### 3.9 Log ve geçmiş
- `logs/session-YYYYMMDD-HHMM.log`: Teknik log.
- `logs/subtitles-YYYYMMDD-HHMM.srt` benzeri metin: Zaman damgası + İngilizce + Türkçe. Oturumdan sonra okunabilir.

### 3.10 Web overlay (Mod B ve yayıncılar için)
- `aiohttp` ile yerel sunucu (vars. `0.0.0.0:8787`).
  - `/` → Tam ekran altyazı sayfası: Telefon, tablet veya ikinci monitör için. Siyah arka plan, büyük font, son 2-3 satır geçmişi.
  - `/obs` → Şeffaf arka planlı sayfa, OBS Browser Source için.
  - `/ws` → WebSocket. Her yeni çeviri `{id, speaker, source, translation, ts, engine}` JSON'u olarak yayınlanır, altyazı temizlenince `clear` olayı gider.
- Sayfa görünümü URL parametreleriyle ayarlanır (`?font=48&lines=3&source=1`).
- Başlangıçta yerel IP ve URL konsola yazılır, ayrıca QR kodu basılır (`qrcode` paketi); telefondan okutup açılır.
- Güvenlik: Varsayılan olarak sadece yerel ağ. Opsiyonel basit token (`?t=...`).

### 3.11 Sesli okuma (TTS, opsiyonel)
- `tts.mode`: `off` (vars.) | `translation` (Türkçe çeviriyi seslendir) | `source` (orijinali oku, çeviri yapma).
- Sıra birikirse eski satırlar atlanır ya da konuşma hızı artırılır (`tts.catch_up: skip|speedup`).
- TTS ayrı bir worker'da çalışır, pipeline'ı bekletmez.

## 4. Dizin Yapısı

```text
subwizard/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── pyproject.toml
├── .env.example
├── config.example.yaml
├── run.py
├── src/subwizard/
│   ├── __init__.py
│   ├── app.py                    # bileşenleri bağlar, main loop
│   ├── cli.py                    # argparse (geliştirici/ileri kullanıcı)
│   ├── controller.py             # AppController: core <-> UI köprüsü (Qt sinyalleri)
│   ├── ui/        main_window.py, wizard/, pages/ (home, history, roi, translate, appearance, profiles, web, hotkeys, about), widgets/ (video_view.py, roi_editor.py, subtitle_preview.py, status_cards.py), tray.py, theme.py
│   ├── i18n/      tr.json, en.json
│   ├── config.py                 # pydantic-settings
│   ├── sources/   base.py, capture_card.py, video_file.py, screen.py (stub)
│   ├── audio/     base.py, passthrough.py, meter.py, av_sync.py
│   ├── core/      metrics.py
│   ├── ocr/       base.py, preprocess.py, change_detect.py, rapid.py, tesseract.py, worker.py
│   ├── pipeline/  subtitle_state.py, text_utils.py
│   ├── translator/ base.py, gemini.py, deep_fallback.py, cache.py, worker.py
│   ├── overlay/   subtitle_painter.py (QPainter), history.py
│   ├── web/       server.py, static/index.html, static/obs.html
│   ├── tts/       base.py, edge.py, sapi.py
│   ├── profiles.py
│   ├── platform/  __init__.py (detect), windows/devices.py
│   └── utils/     hotkeys.py, timing.py, logging.py
├── profiles/
│   ├── default.yaml
│   └── gta6.yaml
└── tests/
    ├── fixtures/  (örnek altyazı kareleri .png, kısa klip .mp4)
    ├── test_subtitle_state.py
    ├── test_text_utils.py
    ├── test_cache.py
    ├── test_renderer_turkish.py
    └── test_ocr_fixtures.py
```

## 5. CLI

```
uv run run.py                          # arayüzü açar (normal kullanım)
uv run run.py --headless               # arayüzsüz, sadece web overlay (ileri kullanıcı)
uv run run.py --list-devices           # video + ses cihazlarını listele
uv run run.py --source capture --device 1 --audio-device "USB Capture"
uv run run.py --source file --path tests/fixtures/clip.mp4
uv run run.py --calibrate
uv run run.py --translator deep        # API'siz çalış
uv run run.py --profile gta6
uv run run.py --display window|web|both  # Mod A / Mod B / ikisi
uv run run.py --debug
```

## 6. Kilometre Taşları (sırayla, her biri bitince commit)

Her adımın sonunda **Kabul** maddeleri sağlanmadan sonrakine geçme.

**M0 — İskelet + UI kabuğu**
- pyproject, config yükleme/doğrulama, logging, CLI iskeleti, ABC'ler.
- Ayrıca: Fluent ana pencere, sol navigasyon, boş sayfalar, tema, i18n altyapısı (TR/EN), tepsi ikonu.
- Kabul: `uv run run.py` ana pencereyi açar; dil ve tema değiştirilince anında uygulanır; `pytest` yeşil (core testleri Qt olmadan koşar).

**M1 — Görüntü + ses aktarımı**
- `CaptureCardSource`, `VideoFileSource`, cihaz listeleme, Qt video widget'ı, F11, ses passthrough. Ana Sayfa'daki Başlat/Durdur ve durum kartları çalışır.
- Ayrıca: `MetricsHub`, F9 performans overlay'i (kompakt + detaylı), Cihaz & Ses sayfası (mod listesi, VU göstergesi, seviye, gecikme profili, A/V kaydırıcısı), Tanılama sayfasının ilk hali (sayılar; grafikler M5'e kalabilir).
- Kabul: Capture card'dan 1080p60 görüntü pencerede akar; kaynak ve render FPS ≥ 58 olarak görünür. İstenen/gerçekleşen mod doğru raporlanır (kasıtlı olarak YUY2 seçildiğinde FPS düşüşü ve uyarı görünür). VU göstergesi oyun sesiyle hareket eder; kablo çekilince "ses gelmiyor" uyarısı çıkar. A/V kaydırıcısı değişikliği anında duyulur. Video dosyası modu aynı pencerede çalışır.

**M2 — ROI + OCR**
- ROI seçici, ön işleme, değişim algılama, RapidOCR worker, debug overlay'de ham metin.
- Kabul: Fixture karelerde OCR doğruluğu testle ölçülür. OCR çalışırken render FPS düşmez. Altyazı değişmeyince OCR atlandığı loglarda görülür.

**M3 — Durum makinesi + çeviri**
- `subtitle_state`, Gemini çevirmen, cache, fallback, maliyet logu.
- Kabul: Durum makinesinin birim testleri (görünme, aynı satır tekrarı, büyüyen metin, kaybolma) yeşil. Aynı satır asla iki kez API'ye gitmez. API key olmadan deep-translator ile uçtan uca çalışır.

**M4 — Overlay + hotkeyler + geçmiş**
- Renderer (yama önbellekli), konum modları, hotkeyler, geçmiş paneli, altyazı log dosyası.
- Kabul: Türkçe karakter render testi geçer. Altyazının ekranda belirmesinden Türkçenin görünmesine kadar geçen süre debug'da ölçülür; hedef < 1.5 sn (p50).

**M4.5 — Web overlay (Mod B)**
- aiohttp sunucusu, `/`, `/obs`, `/ws`, QR kodu, `--display` seçeneği, F6 PC ses kapatma.
- Kabul: Telefonda açılan sayfada çeviri, PC overlay'iyle aynı anda (fark < 100 ms) görünür. Bağlantı koparsa sayfa kendiliğinden yeniden bağlanır.

**M4.8 — Kurulum sihirbazı + ayar sayfaları**
- Sihirbazın 8 adımı, Alan & OCR / Çeviri / Görünüm sayfaları (canlı önizlemeli), Altyazı Geçmişi sayfası, keyring ile API key saklama.
- Kabul: Hiç config dosyası olmayan temiz bir kullanıcı, terminal açmadan sihirbazı bitirip çeviri görebilir. Sihirbazdaki "Test et" butonu gerçek bir çeviri ve gecikme gösterir.

**M5 — Sağlamlaştırma + dağıtım**
- Cihaz kopma/yeniden bağlanma, ağ kesintisi, anlaşılır hata mesajları, log paketleme.
- Paketleme: Nuitka/PyInstaller build scripti, Inno Setup kurulum dosyası, taşınabilir zip, GitHub Actions ile tag'e basınca otomatik release.
- README (TR + EN): Ekran görüntüleri/GIF, kurulum, capture card bağlantı şeması (Mod A ve B), sorun giderme, katkı rehberi (`CONTRIBUTING.md`), dil ekleme rehberi.
- Kabul: Python kurulu olmayan temiz bir Windows makinesinde setup.exe ile kurulup çalışır.

**M6 — Profiller ve kalite**
- Oyun profilleri, glossary, ignore listesi, çoklu ROI, hariç bölgeler, çoklu API key rotasyonu, F7 yeniden çevirme, `replace` + `auto_bg` modu.
- Kabul: `--profile gta6` ile profilin prompt'a girdiği debug logunda görünür. Glossary'deki `keep` terimleri çeviride korunur (birim testi, mock çevirmen ile).

**M7 — Deneysel**
- Görsel çeviri modu (vision), Windows OCR motoru, TTS.
- Kabul: Aynı fixture klibi üzerinde `ocr+text` ve `vision` modları için gecikme, maliyet ve doğruluk karşılaştırma raporu (`scripts/benchmark.py`).

**Faz 2 (ayrı iş):** `ScreenSource` + click-through şeffaf pencere (`WS_EX_LAYERED | WS_EX_TRANSPARENT`, yalnızca borderless/windowed oyunlarda çalışır), macOS/Linux kaynak adaptörleri.

## 7. Claude Code için Çalışma Kuralları
- UI işlerinde her sayfa bittiğinde ekran görüntüsü al (`scripts/screenshot_pages.py`, Qt `grab()`) ve `docs/screenshots/` altına kaydet; görsel gerilemeleri buradan takip ederiz.
- Her kilometre taşından önce kısa bir plan yaz, onay beklemeden uygula; bitince kabul maddelerini tek tek doğrula ve raporla.
- Donanım gerektiren kodu (capture card, ses) `VideoFileSource` ve mock'larla test edilebilir tut. Donanım testlerini `@pytest.mark.hardware` ile işaretle, varsayılan test koşusundan hariç tut.
- Gemini SDK ve model parametreleri hızlı değişiyor. Ezberden yazma, güncel `google-genai` dokümanını kontrol et.
- Yeni bağımlılık eklemeden önce gerekçesini belirt.
- Sihirli sayı yok; eşikler ve süreler `config.yaml`'da.
- Gecikme ölçümü her aşamada `utils/timing.py` ile yapılır; performansla ilgili iddialar ölçümle desteklenir.

## 8. İlham Alınan Projeler (sadece fikir; kod kopyalanmaz)
| Proje | Lisans | Aldığımız fikirler |
|---|---|---|
| [sefflex/GameTranslator](https://gitlab.com/sefflex/GameTranslator) | AGPL-3.0 | Görüntü TV'de kalır, çeviri ayrı ekranda (Mod B); ROI şablonları; `replace` modu; OBS overlay; PC ses kapatma; oyuna özel argo sözlüğü; panoya kopyalama |
| [thanhkeke97/RSTGameTranslation](https://github.com/thanhkeke97/RSTGameTranslation) | GPL-3.0 | Çoklu alan; hariç bölgeler; ignore listesi; oyun profilleri + oyun bilgisi bağlamı; çoklu API key; yeniden çevir kısayolu; geçmiş penceresi; arka plan rengiyle orijinali silme |
| [ramjke/Translumo](https://github.com/ramjke/Translumo) | Apache-2.0 | Windows OCR'ın hızlı varsayılan olması; OCR alanını küçük tutma |
