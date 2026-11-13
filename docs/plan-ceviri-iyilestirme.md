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

## Uygulama sırası

| Sıra | İş | Neden |
|---|---|---|
| 0 | Ücretli oturum + ölçüm | Kazançlar tahmin; neyin önemli olduğunu sayılar gösterir |
| 1 | Önbellek `loose_key` | Küçük, güvenli, her oturumda kazanç |
| 2 | Google Cloud yedeği | Bugünkü yedek zinciri güvenilmez; kullanıcı Cloud anahtarı alınca |
| 3 | Terim önerileri + koşullu prompt enjeksiyonu | Corpus verisi birikince anlamlı |
| — | Typewriter debounce | Yapılmayacak; ölçüm aksini gösterirse yeniden bakılır |
