# Clipfarm

Ünlü yayıncıların uzun yayınlarından (YouTube, Twitch, Kick) **en iyi anları otomatik bulan**, onları **dikey, altyazılı, başlıklı Shorts**'a çeviren ve **YouTube'a yükleyen** araç. Chrome eklentisiyle izlediğin sekmeden tek tıkla çalışır.

```
Chrome eklentisi ──► yerel Clipfarm sunucusu (127.0.0.1:8765)
                         │ 1. yt-dlp: yayının yalnızca sesini + sohbet kaydını indir
                         │ 2. ses yüksekliği + sohbet yoğunluğu tepeleri → aday anlar
                         │ 3. Whisper: adayları yazıya dök
                         │ 4. Claude: en iyi anları seç, kesim noktası, hook, başlık, açıklama, etiket
                         │ 5. yt-dlp: yalnızca seçilen kesitlerin videosunu indir
                         │ 6. ffmpeg: 9:16 düzen + kelime kelime altyazı + hook + kaynak etiketi
                         ▼
                    Panel (onayla / düzenle) ──► YouTube Data API ile yükleme
```

## Kurulum

Python 3.10+ ve ffmpeg gerekir.

```bash
# ffmpeg — Windows: winget install Gyan.FFmpeg | macOS: brew install ffmpeg | Linux: sudo apt install ffmpeg
git clone https://github.com/yilmazcansayin/Clipfarm.git
cd Clipfarm
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .
```

İlk altyazı işleminde Whisper modeli (small ≈ 500 MB) bir kez indirilir. Siteler değiştikçe indirici de güncellenir, bu yüzden ara sıra `pip install -U yt-dlp` çalıştırın.

### Claude (an seçimi ve başlıklar için önerilir)

[console.anthropic.com](https://console.anthropic.com) üzerinden bir API anahtarı alın ve sunucuyu başlatmadan önce ortam değişkeni olarak verin:

```bash
# Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_API_KEY=sk-ant-...
```

Anahtar yoksa araç yine çalışır; anları yalnızca ses/sohbet skoruna göre seçer, başlıklar basit kalır. Varsayılan model `claude-opus-5-5`; `CLIPFARM_MODEL` ile değiştirilebilir. Bir yayın için tek bir istek atılır (aday sayısına göre birkaç sent).

### YouTube hesabını bağlama (bir kez)

1. [Google Cloud Console](https://console.cloud.google.com) → yeni proje → **YouTube Data API v3**'ü etkinleştirin.
2. **OAuth izin ekranı** → "External" → kendi Google hesabınızı test kullanıcısı olarak ekleyin.
3. **Kimlik bilgileri** → **OAuth istemci kimliği** → uygulama türü **Masaüstü uygulaması** → JSON'u indirin.
4. Dosyayı `~/.clipfarm/client_secret.json` olarak kaydedin (Windows: `C:\Users\<ad>\.clipfarm\client_secret.json`).
5. `clipfarm youtube-login` → açılan Chrome penceresinde kanalınızı seçip izin verin.

Notlar: YouTube API'si günde 100 yüklemeye izin veriyor. OAuth uygulaması "Testing" durumundayken Google girişi 7 günde bir yeniler; kalıcı olması için izin ekranında uygulamayı "In production" yapın (kendi kullanımınız için doğrulama gerekmez, girişte bir uyarı görürsünüz).

### Chrome eklentisi

1. Chrome'da `chrome://extensions` → sağ üstten **Geliştirici modu**'nu açın.
2. **Paketlenmemiş öğe yükle** → repodaki `extension/` klasörünü seçin.
3. Eklentiyi araç çubuğuna sabitleyin.

## Kullanım

```bash
clipfarm serve        # sunucuyu başlatın ve açık bırakın; panel: http://127.0.0.1:8765
```

Chrome'da bir yayın kaydı (YouTube videosu/canlı yayın kaydı, Twitch VOD, Kick VOD) veya klip açın, eklentiye tıklayın:

- **Klip üret (otomatik):** yayındaki en iyi 3–12 anı bulur ve Shorts'a çevirir. 5 saatlik bir yayın birkaç dakikada analiz edilir; yalnızca ses ve seçilen kesitler indirilir.
- **Başlangıç işaretle → Bitiş + gönder:** istediğiniz aralığı elle seçin.
- **Son 30 sn:** videoda o an izlediğiniz yerin son 30 saniyesini klip yapar.

Klipler panelde görünür. Her klip için videoyu izleyin; hook, başlık, açıklama ve etiketleri düzenleyin. Hook değiştiyse **Yeniden üret**, beğendiyseniz **Yükle**'ye basın. Yeni iş oluştururken "Hazır olunca YouTube'a otomatik yükle" kutusunu işaretlerseniz onay beklemeden yüklenir (varsayılan görünürlük: Özel).

Canlı yayın sürerken analiz yapılmaz; yayın bitince kaydın (VOD) adresini kullanın.

### Düzenler

| Düzen | Ne zaman |
|---|---|
| `blur` (varsayılan) | Video ortada, arkada bulanık kopyası. Her içerikte güvenli. |
| `crop` | Ortadan (veya `--crop-x` ile istenen yerden) kırp; IRL / tek kişi yayınları. |
| `split` | Üstte yayıncının kamerası, altta oyun. `--cam x,y,w,h` kamera alanının 0–1 oranları; örneğin sağ üst köşe için `0.72,0.02,0.27,0.30`. Her yayıncının kamera yeri sabittir, bir kez bulmanız yeterli. |

### Komut satırı

```bash
clipfarm auto "https://www.twitch.tv/videos/2299..." --count 5            # otomatik, panelsiz
clipfarm auto "https://www.youtube.com/watch?v=..." -n 8 --upload --privacy unlisted
clipfarm make "https://kick.com/xqc/clips/clip_01..." --hook "HE WAS NOT READY 😳" --credit "kick.com/xqc"
clipfarm make kayit.mp4 --start 1:02:10 --end 1:02:55 --layout split --cam 0.72,0.02,0.27,0.30
clipfarm upload output/klip_short.mp4 --title "Kai Cenat loses it #shorts" --privacy public
clipfarm get "https://www.youtube.com/watch?v=..." --start 12:00 --end 13:00
```

`make` çıktıları `output/` klasörüne yazılır: `<ad>_short.mp4` (yüklenecek video), `<ad>.words.json` (altyazı kelimeleri; düzeltip `--words` ile tekrar kullanın), `<ad>_short.ass` (altyazı dosyası; `--ass` ile olduğu gibi kullanın). Otomatik işler ve panel dosyaları `~/.clipfarm/jobs/` altında tutulur.

Eklenti kurmadan hızlı işaretleme için `chrome/` klasöründe bir yer imi (bookmarklet) de var: `python chrome/build_bookmarklet.py` çalıştırıp çıkan satırı yer imi adresi olarak kaydedin. İki tıkla başlangıç ve bitişi işaretler, `clipfarm make` komutunu panoya kopyalar.

## Telif ve YouTube kuralları (önemli)

- Başkasının yayınını izinsiz yüklemek telif ihlalidir: Content ID eşleşmesi, telif ihtarı (3 ihtarda kanal kapanır) veya gelirin hak sahibine gitmesi olabilir. **Klip kullanımına izin veren ya da clipper programı olan yayıncılarla çalışın** (Vyro, Whop, Content Rewards kampanyaları, yayıncıların Discord'ları) ve izni yazılı saklayın.
- YouTube Ekim 2026'dan itibaren Shorts akışında az düzenlenmiş yeniden yüklemelerin erişimini düşürüyor. Claude'un yazdığı hook ve bağlam bunun için var; yine de yayından önce gözden geçirip kendi bakış açınızı ekleyin.
- Kaynak yayıncının kanalı videoya (`--credit`, otomatik) ve açıklamaya eklenir; silmeyin.
- Kumar / slot yayınlarından klip almayın; yayında çalan müzik ayrı bir Content ID riskidir.

## Geliştirme

```bash
pip install -e ".[dev]"
pytest
```
