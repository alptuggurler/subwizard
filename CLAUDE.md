# SubWizard

Şartname: [SPEC.md](SPEC.md) — her işten önce ilgili bölümü oku. Kilometre taşları sırayla
ilerler (SPEC 6); her biri bitince kabul maddeleri doğrulanır ve commit atılır.

## Durum
- M0–M7 ve Faz 2 (PC oyunu, `source.kind: screen`) kodu tamam; geliştirme macOS'ta, testler orada geçer.
- Windows'ta PS5 + capture card + Gemini ile gerçek oturumlar yapıldı. Yeni özellikler çoğunlukla yalnızca
  macOS'ta test edilir; Windows'ta denenmeyeni commit mesajında ve raporda açıkça belirt.
- Sürüm etiketi (v0.1.0) Windows testleri bitince atılır.

## Komutlar
```
uv sync
uv run run.py                               # arayüz
uv run pytest                               # testler (hardware işaretliler hariç)
uv run python scripts/make_test_clip.py --out clip.mp4       # altyazılı sentetik 1080p60 klip
uv run run.py --source file --path clip.mp4                  # klibi oynat
uv run python scripts/screenshot_pages.py --clip clip.mp4 --offline-translation  # ekran görüntüleri + ölçüm
uv run pytest -m network                                     # gerçek Google Çeviri (internet)
uv run python scripts/make_ocr_fixtures.py                   # tests/fixtures/ocr/ (macOS fontları)
uv run python scripts/i18n_add.py strings.json               # tr/en'e birlikte metin ekle
uv run python scripts/benchmark.py --translator none           # M7: ocr+text vs vision raporu (vision anahtar ister)
```

## Kurallar (SPEC 7'ye ek, bu repoya özgü)
- Hedef platform Windows 10/11; geliştirme makinesi macOS olabilir. Windows'a özel kod yalnızca
  `src/subwizard/platform/windows/` altında, Windows'a özel bağımlılıklar
  `sys_platform == 'win32'` işaretiyle eklenir. macOS'ta doğrulanamayan şeyi (DirectShow, WASAPI,
  Mica, Credential Manager) "doğrulandı" diye raporlama.
- Qt yalnızca `ui/`, `controller.py` ve `app.py` içinde import edilir; geri kalan her şey Qt'siz
  kalır (`tests/test_core_boundaries.py` bunu zorlar — yeni çekirdek modülünü oradaki listeye ekle).
- Kullanıcıya görünen her metin `src/subwizard/i18n/{tr,en}.json` içinde; kodda `t("anahtar")`.
  İki dosyanın anahtarları aynı olmalı (test zorlar).
- Yeni ayar = `config.py` modeli + `config.example.yaml` (test ikisinin eşleşmesini zorlar).
  Ayarı UI doğrudan değiştirmez; `AppController` üzerinden değiştirir, o da anında kaydeder.
- Worker thread'leri UI'ya yalnızca `AppController` sinyalleriyle ulaşır; kare paylaşımı `LatestSlot`.
  Metrik adları `core/metrics.py` içindeki `M` sınıfında; overlay, Tanılama ve rapor aynı
  `ui/metrics_view.metric_rows()` çıktısını kullanır.
- Kullanıcıya gösterilecek hatalar `UserError` (i18n anahtarı + parametre) olarak fırlatılır.
- API anahtarları yalnızca `core/secrets.py` üzerinden (env/.env/keyring); config'e ve loglara yazılmaz
  (`secrets.mask`). Testlerde keyring, kullanıcı veri klasörü ve Google Çeviri `conftest` ile sahtedir.
- Video bileşeni bilerek düz `QWidget` (OpenGL değil); değiştirmeden önce FPS'i ölç.
- Tam ekran, ekran boyutunda çerçevesiz penceredir (`showFullScreen` macOS'ta çerçevesiz pencerede çalışmıyor).
- Kısayol eylemleri `MainWindow._actions` içinde; tuşlar `config.hotkeys.keys`, global dinleyici
  `utils/hotkeys.GlobalHotkeys` (pynput iş parçacığı → Qt sinyali). Uygulama penceresi öndeyken
  global geri çağrı yok sayılır (QShortcut zaten tetiklenir).
- Web sayfası metinleri de i18n'den gelir (`web.page.*`, sunucu HTML'e gömer). qfluentwidgets
  `SpinBox` koyu temada değeri siyah çiziyor; yerine `LineEdit` + `QIntValidator` kullan.
- Sihirbaza gömülen sayfa bölümleri sayfada liste olarak tanımlı (`video_section`,
  `audio_section`, `advanced_section`, `settings_section`); sayfaya satır eklersen listeye de ekle.
- Yer tutucu sayfalar `ui/pages/__init__.py` içinde tanımlı; içerik kazanan sayfa kendi modülüne taşınır.
- macOS'ta UI testleri "offscreen" Qt eklentisiyle çöker (qframelesswindow); `tests/conftest.py`
  bunu ele alır, testlerde pencere `show()` edilmez.
