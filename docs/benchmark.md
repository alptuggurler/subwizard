# OCR+metin ve görsel mod karşılaştırması (M7)

2026-11-12 10:36 · 9 fixture kare (`tests/fixtures/ocr`). Doğruluk: okunan İngilizce ile beklenen satırın benzerliği (0–1). Süre: okuma + çeviri (vision'da tek çağrı).

| Mod | Ort. doğruluk | Medyan ms | En kötü ms | Satır başı maliyet | Not |
|---|---|---|---|---|---|
| ocr+text | 1.000 | 28 | 83 | $0.000000 | OCR only (--translator none) |
| vision | — | — | — | — | not run: no Gemini API key |

## ocr+text

| Kare | Doğruluk | ms | Okunan | Çeviri |
|---|---|---|---|---|
| dialogue_outline.jpg | 1.000 | 28 | Jason: We need to get out of here, now. |  |
| two_lines.jpg | 1.000 | 83 | I told you, the money's in the trunk. Just keep driving and don't look back. |  |
| bright_sky.jpg | 1.000 | 26 | Where the hell did you park the car? |  |
| small_font.jpg | 1.000 | 28 | You hear that? Somebody called the cops. |  |
| yellow_speaker.jpg | 1.000 | 31 | LUCIA: Not without the money. |  |
| numbers_punct.jpg | 1.000 | 32 | Meet me at 7:30 on 42nd Street. Don't be late! |  |
| serif.jpg | 1.000 | 30 | It's not about the cash, it's about respect. |  |
| box_background.jpg | 1.000 | 25 | Press the gas, we're losing them! |  |
| empty.jpg | 1.000 | 24 |  |  |
