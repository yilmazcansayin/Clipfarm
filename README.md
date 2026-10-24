# Clipfarm

Yayıncıların YouTube / Twitch / Kick yayınlarından kesit alıp **dikey (9:16), kelime kelime altyazılı, başlıklı** YouTube Shorts videosuna çeviren komut satırı aracı.

- **İndirme:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) (YouTube videoları ve canlı yayın kayıtları, Twitch VOD ve klipleri, Kick VOD ve klipleri). Saatlerce süren yayının tamamını değil, yalnızca istenen aralığı indirir.
- **Altyazı:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) ile otomatik, kelime zamanlamalı. Türkçe ve İngilizce otomatik algılanır; Türkçe büyük harf (i → İ) doğru yapılır.
- **Dikey düzen:** ffmpeg ile üç seçenek:
  - `blur`: video ortada, arkada bulanık kopyası (her içerik için güvenli varsayılan)
  - `crop`: ortadan (veya `--crop-x` ile istenen yerden) kırp, tam ekran
  - `split`: üstte yayıncının kamerası, altta oyun görüntüsü (oyun yayınları için en çok izlenen düzen)
- **Yazılar:** üstte dikkat çekici başlık (`--hook`), kaynak yayıncı etiketi (`--credit`).
- **Chrome yer imi:** videoyu izlerken iki tıkla başlangıç ve bitişi işaretle; komut panoya kopyalanır.

## Kurulum

Python 3.10+ ve ffmpeg gerekir.

```bash
# ffmpeg
#   Windows: winget install Gyan.FFmpeg
#   macOS:   brew install ffmpeg
#   Linux:   sudo apt install ffmpeg

git clone https://github.com/yilmazcansayin/Clipfarm.git
cd Clipfarm
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .
```

İlk altyazı çalıştırmasında Whisper modeli (small ≈ 500 MB) bir kez indirilir. yt-dlp'yi sık güncelleyin (`pip install -U yt-dlp`), çünkü siteler değiştikçe indirici de güncellenir.

## Kullanım

```bash
# Twitch / Kick klibi: tek komut
clipfarm make "https://kick.com/eray/clips/clip_01ABC..." --hook "BUNU KİMSE BEKLEMİYORDU 😳" --credit "kick.com/eray"

# Uzun yayından aralık kes (yalnızca o kısım indirilir)
clipfarm make "https://www.twitch.tv/videos/2299..." --start 1:02:10 --end 1:02:55 \
  --layout split --cam 0.72,0.02,0.27,0.30 --hook "Jahrein'in tepkisi 😂" --credit "twitch.tv/jahrein"

# Bilgisayardaki bir dosyadan
clipfarm make kayit.mp4 --start 30 --end 75 --layout crop --crop-x 0.4

# Sadece indir
clipfarm get "https://www.youtube.com/watch?v=..." --start 12:00 --end 13:00
```

Çıktılar `output/` klasörüne yazılır:

| Dosya | Ne işe yarar |
|---|---|
| `<ad>_short.mp4` | YouTube'a yüklenecek 1080x1920 video |
| `<ad>.words.json` | Whisper'ın çıkardığı kelimeler. Yanlış kelimeyi düzeltip `--words <dosya>` ile tekrar çalıştırın; yeniden yazıya dökme yapılmaz |
| `<ad>.ass` | Altyazı dosyası. Aegisub ile elle düzenleyip `--ass <dosya>` ile kullanabilirsiniz |

### split düzeninde kamera alanı

`--cam x,y,w,h` değerleri kaynak görüntünün oranlarıdır (0–1). Örneğin kamera sağ üst köşede, genişliğin %27'si ve yüksekliğin %30'u kadarsa: `--cam 0.72,0.02,0.27,0.30`. Her yayıncının kamera yeri sabit olduğu için bir kez bulup not etmeniz yeterli.

### Sık kullanılan seçenekler

| Seçenek | Açıklama |
|---|---|
| `--layout blur\|crop\|split` | dikey düzen |
| `--hook "..."` / `--hook-seconds 4` | üst başlık / kaç saniye görünsün |
| `--credit "twitch.tv/..."` | kaynak yayıncı etiketi |
| `--lang tr` | dili sabitle (boşsa otomatik) |
| `--model medium` | daha doğru ama daha yavaş altyazı |
| `--words-per-line 2` | ekranda aynı anda kaç kelime |
| `--highlight 00FF88` | konuşulan kelimenin rengi |
| `--cookies-from-browser chrome` | abonelere özel veya giriş isteyen videolar için Chrome oturumunu kullan |

## Chrome ile iş akışı

1. `python chrome/build_bookmarklet.py` çalıştırın; `chrome/bookmarklet.txt` oluşur.
2. Chrome'da yer imleri çubuğuna sağ tıklayın → **Sayfa ekle** → ad: `Clipfarm`, URL: `bookmarklet.txt` içindeki satırın tamamı.
3. YouTube, Twitch VOD veya Kick VOD izlerken kesitin başladığı anda yer imine tıklayın, bittiği anda bir kez daha tıklayın. `clipfarm make "..." --start .. --end ..` komutu panoya kopyalanır.
4. Twitch veya Kick **klip** sayfasında tek tık yeterlidir.
5. Komutu terminale yapıştırın, `--hook` ve `--credit` ekleyip çalıştırın.

## Telif ve YouTube kuralları (önemli)

- Başkasının yayınını izin almadan yüklemek telif ihlalidir; Content ID eşleşmesi, telif ihtarı (3 ihtarda kanal kapanır) veya gelirin hak sahibine gitmesi olabilir. **Klip kullanımına izin veren veya clipper programı olan yayıncılarla çalışın** ve izni yazılı olarak (Discord mesajı, e-posta, kampanya sözleşmesi) saklayın.
- YouTube Ekim 2026'dan itibaren Shorts akışında az düzenlenmiş yeniden yüklemelerin erişimini düşürüyor. Sadece kırpıp altyazı eklemek yetmeyebilir: kendi yorumunuz, bağlam veren başlık, birden çok anı birleştiren derleme veya sesli anlatım gibi **özgün katkı** ekleyin.
- Kaynağı her zaman belirtin (`--credit`) ve açıklamaya yayıncının kanal linkini koyun.
- Yayında çalan müzik ayrı bir Content ID riski taşır; müzikli kısımları kesin veya sesi kısın.

## Geliştirme

```bash
pip install -e ".[dev]"
pytest
```
