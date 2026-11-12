# Katkı rehberi

SubWizard'a katkı verdiğin için teşekkürler. Şartname [SPEC.md](SPEC.md), proje kuralları
[CLAUDE.md](CLAUDE.md) içinde; aşağısı kısa özet.

## Geliştirme ortamı

```
uv sync
uv run pytest                 # donanım ve ağ testleri hariç
uv run run.py                 # arayüz
uv run python scripts/make_test_clip.py --out clip.mp4
uv run run.py --source file --path clip.mp4    # capture card olmadan dene
```

Hedef platform Windows 10/11; geliştirme macOS/Linux'ta da yapılabilir. Capture card, WASAPI
ve global kısayol gibi Windows'a özel şeyler orada doğrulanamaz: PR açıklamasında neyi
nerede denediğini yaz.

## Kurallar

- **Çekirdek Qt'siz kalır.** Qt yalnızca `ui/`, `controller.py`, `app.py` içinde. Yeni bir
  çekirdek modülü ekleyince `tests/test_core_boundaries.py` listesine de ekle.
- **Kullanıcıya görünen her metin** `src/subwizard/i18n/{tr,en}.json` içinde; kodda `t("anahtar")`.
  Toplu ekleme: `uv run python scripts/i18n_add.py strings.json`.
- **Yeni ayar** = `config.py` modeli + `config.example.yaml` (test ikisini karşılaştırır).
  Sabit sayı yerine ayar ya da adlandırılmış sabit.
- **API anahtarları** yalnızca `core/secrets.py` üzerinden; config'e, loglara, destek paketine
  girmez.
- **Performans iddiası = ölçüm.** `scripts/screenshot_pages.py --clip clip.mp4
  --offline-translation` FPS ve gecikmeyi yazdırır.
- UI değişikliğinde `scripts/screenshot_pages.py` ile ekran görüntülerini yenile
  (`docs/screenshots/`).
- Donanım testleri `@pytest.mark.hardware`, internet isteyenler `@pytest.mark.network`.
- Yeni bağımlılık eklerken PR'da gerekçesini yaz.

## Hata bildirmek

Uygulamada **Tanılama → Destek paketi** bir .zip oluşturur (loglar, gizli bilgileri maskeli
ayarlar, performans raporu). Hata bildirimine onu ekle; API anahtarların pakete girmez.

## Yeni dil eklemek

1. `src/subwizard/i18n/en.json` dosyasını kopyala, dil koduyla adlandır (ör. `de.json`).
2. `"_meta.name"` değerini dilin kendi adıyla yaz (ör. `"Deutsch"`). Ayarlar'daki dil listesi
   dosyalardan otomatik oluşur.
3. Değerleri çevir; anahtarlara ve `{n}`, `{model}` gibi yer tutuculara dokunma.
4. `uv run pytest tests/test_i18n.py` — anahtarlar diğer dillerle birebir aynı olmalı.
5. Ayarlar'dan dili seçip sayfaları gez; uzun metinler taşıyorsa kısalt.

Bu, arayüz dilidir. Çeviri hedef dili ayrı bir ayardır: `translator.target_language`.
