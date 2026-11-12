# Windows'ta doğrulama listeleri

Geliştirme macOS'ta yapıldı; aşağıdakiler gerçek bir Windows PC, capture card ve PS5 ister.
Her kilometre taşı için ne denenmesi gerektiği burada. Sonuçları (ve Tanılama → Destek
paketi'ni) bir issue'ya ekle.

## M1'i Windows'ta doğrulama

M1 macOS'ta video dosyasıyla test edildi; aşağıdakiler gerçek capture card ister:

1. `uv run run.py --list-devices` — capture card adıyla ve desteklediği modlarla listeleniyor mu?
2. Cihaz & Ses sayfasında capture card'ı ve `1920×1080@60 MJPG` modunu seç, Ana Sayfa'da **Başlat**.
3. **F9** ile performans göstergesini aç: kaynak ve çizim FPS ≥ 58 mi?
4. Modu `1920×1080@60 YUY2` yap: FPS düşüşü ve sarı uyarı görünüyor mu?
5. Ses girişi olarak capture card'ı seç: ses geliyor mu, seviye göstergesi oynuyor mu?
6. HDMI kablosunu çek: 3 sn sonra "Ses gelmiyor" uyarısı ve görüntüde "Yeniden bağlanıyor…" çıkıyor mu;
   kablo takılınca kendiliğinden düzeliyor mu?
7. Ses / görüntü senkronu kaydırıcısını oynat: değişiklik anında duyuluyor mu?
8. **F11** tam ekran: görev çubuğunu kaplıyor mu, imleç 2 sn sonra gizleniyor mu, Esc ile çıkılıyor mu?

Sorun olursa Tanılama sayfasındaki **Raporu kopyala** çıktısını paylaş.

## M2'yi Windows'ta doğrulama (altyazılı bir oyun sahnesinde)

1. **F2** (ya da `uv run run.py --calibrate`): donmuş karede altyazının üzerine dikdörtgen çiz, Enter.
2. Alan & OCR sayfası: "OCR önizleme" altyazıyı doğru okuyor mu, süre kaç ms?
3. **F9** iki kez (ayrıntılı): çizim FPS hâlâ ≥ 58 mi, "Değişmediği için atlanan" oranı altyazı
   sabitken yükseliyor mu? Alan videonun üzerinde mavi çerçeveyle görünüyor mu?
4. Renkli (ör. sarı) altyazılı bir oyunda yeni satırlar kaçıyor mu? Kaçıyorsa Tanılama raporunu paylaş
   (değişim algılama varsayılan olarak yalnızca parlak piksellere bakıyor).

## M3'ü Windows'ta doğrulama (çeviri)

1. Anahtarsız: Ana Sayfa'da Başlat; altyazı görününce ~1 sn içinde altına Türkçesi "(yedek)"
   işaretiyle geliyor mu? Çeviri sayfasında **Test et** sonuç ve süre gösteriyor mu?
2. Gemini: [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresinden anahtar al,
   Çeviri sayfasına yapıştır, **Kaydet**, **Test et**: çeviri doğal mı, kaç ms?
3. F9 ayrıntılı: "Altyazı → çeviri" süresi ve "İstek / önbellek" sayıları; aynı satır tekrar
   görününce istek sayısı artmamalı.
4. `uv run pytest -m network` — gerçek Google Çeviri testi (internet gerekir).

## M4'ü Windows'ta doğrulama (overlay, kısayollar, geçmiş)

1. Görünüm sayfası: önizlemede "ğüşıöçİĞÜŞÖÇ" kutucuksuz mu (Segoe UI)? Üç hazır stil ve
   "Orijinalin yerine" konumu oyunda okunaklı mı; İngilizce tamamen örtülüyor mu?
2. Oyun penceresi öndeyken (SubWizard arkada) F5 / F7 / F8 / F10 / F12 çalışıyor mu
   (global kısayol, pynput)? Kısayollar sayfasında bir tuşu değiştir, yenisi hemen çalışmalı.
3. Altyazı Geçmişi: satırlar geliyor mu, arama, .srt dışa aktarma; `logs/subtitles-*.srt`
   oturum boyunca doluyor mu?
4. F9 ayrıntılı: "Altyazı → çeviri" p50 < 1,5 sn (Gemini anahtarıyla).

## M4.5'i Windows'ta doğrulama (web overlay, Mod B)

1. Web Overlay sayfasında modu "İkisi de" yap; telefonla QR kodu okut (aynı Wi-Fi). Altyazı
   PC'de ve telefonda aynı anda mı beliriyor? İlk açılışta Windows Güvenlik Duvarı izin
   sorarsa "Özel ağlar"a izin ver.
2. OBS: "OBS için URL'yi kopyala" → Tarayıcı Kaynağı; arka plan şeffaf mı?
3. Uygulamayı kapatıp aç: telefondaki sayfa yenilemeden yeniden bağlanıyor mu?
4. `uv run run.py --headless` — pencere açılmadan konsolda URL + QR çıkıyor mu?

## M4.8'i Windows'ta doğrulama (kurulum sihirbazı)

1. Ayar dosyası olmadan ilk açılış (`%APPDATA%\SubWizard\config.yaml`'ı geçici olarak taşı):
   sihirbaz kendiliğinden açılıyor mu?
2. Capture card adımında önizleme ve ölçülen FPS; ses adımında VU göstergesi hareket ediyor mu?
3. Çeviri adımında anahtar yapıştır, **Test et**: çeviri ve ms görünüyor mu?
4. Altyazı alanı adımında "Altyazıda dondur" oyunda altyazı çıkınca donduruyor mu?
5. Başla: Ana Sayfa'da çeviri görünüyor mu? Ayarlar → "Sihirbazı aç" yeniden açıyor mu?

## M5'i Windows'ta doğrulama (sağlamlaştırma, kurulum)

1. GitHub Actions'ın ürettiği `SubWizard-*-setup.exe`'yi Python kurulu olmayan bir Windows'a kur:
   açılıyor mu, ilk açılışta sihirbaz geliyor mu?
2. Cihaz & Ses → **Sesi test et**: seçili çıkıştan bip geliyor mu?
3. Cihaz & Ses → **Otomatik ölç**: konsolda (ya da telefonda, capture card'a bağlı ekranda) bir
   A/V senkron test videosu (beyaz flaş + bip; YouTube'da "AV sync test") oynat, düğmeye bas.
   10 sn sonra kaç ms önerdi, kaç olay eşleşti? Sonra flaş ile bip birlikte mi görünüp duyuluyor?
   "Ölçülemedi" diyorsa Tanılama → Destek paketi'ni paylaş (logda "A/V otomatik ölçüm" satırı).
4. Tanılama: grafikler (FPS, kare aralığı, ses, OCR) son 60 sn'yi çiziyor mu, CSV dışa aktarma açılıyor mu?

## M6–M7'yi Windows'ta doğrulama (profiller, deneysel modlar)

1. `uv run run.py --profile gta6` (ya da Oyun Profilleri → GTA 6 → Kullan): debug logda profil
   prompt'u görünüyor mu, "Vice City" gibi `keep` terimleri çeviride korunuyor mu?
2. Alan & OCR → "OCR motoru: Windows OCR": logda "Windows OCR: en-US" (ya da kurulu dil) var mı,
   OCR önizleme okuyor mu, süre RapidOCR'a göre kaç ms? "açılamadı" yazıyorsa Windows Ayarlar →
   Saat ve dil → Dil → İngilizce (ABD) dil paketini ekle.
3. Çeviri → "Sesli okuma: Çeviriyi oku": Türkçe ses okuyor mu? Türkçe ses yoksa logda uyarı olur;
   Windows Ayarlar → Konuşma → Ses ekle → Türkçe. "İngilizceyi oku" seçeneği çeviriyi durduruyor mu?
4. Gemini anahtarıyla: Çeviri → "Okuma yöntemi: Görsel mod" altyazıyı okuyup çeviriyor mu?
   `uv run python scripts/benchmark.py` raporunu (docs/benchmark.md) paylaş.
