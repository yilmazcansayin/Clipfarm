# Clipfarm

İki işi olan bir Chrome eklentisi ve onun yanında çalışan küçük bir yardımcı uygulama:

1. **Video indirici:** Herhangi bir sitedeki videonun üstüne gelince **⬇ İndir** düğmesi çıkar. Tüm videoyu (en iyi kalite, 1080p, 720p, 480p ya da sadece ses) veya **yalnızca istediğin aralığı** indirirsin. Örneğin 1 saatlik videonun 1 dakikalık kısmı. Büyük görsellerde de indirme düğmesi çıkar.
2. **Shorts üretici:** Ünlü yayıncıların uzun yayınlarında (YouTube, Twitch, Kick) en iyi anları otomatik bulur, dikey, altyazılı ve başlıklı Shorts'a çevirir, istersen YouTube'a yükler.

### Neden yardımcı uygulama var?

YouTube, Twitch ve Kick videoları tek dosya değil, parça parça akış (HLS/DASH) olarak gelir; YouTube ayrıca adresleri şifreler. Bir Chrome eklentisi bunları tek başına güvenilir biçimde birleştiremez. Internet Download Manager ve Video DownloadHelper da bu yüzden bilgisayara bir yardımcı program kurar. Clipfarm'ın yardımcısı `yt-dlp` (1800'den fazla siteyi destekler) ve `ffmpeg` kullanır. Aralık indirirken yalnızca o kısım indirilir; 1 saatlik videonun tamamını indirmez.

Yardımcı kapalıyken, doğrudan dosya olarak oynatılan videolar (`.mp4`, `.webm`) yine Chrome'un kendi indiricisiyle indirilir.

```
Chrome eklentisi ──► yerel Clipfarm yardımcısı (127.0.0.1:38765)
  ⬇ İndir düğmesi      ├─ İndirici: yt-dlp + ffmpeg → İndirilenler/Clipfarm
  Yakalanan akışlar    └─ Shorts: ses/sohbet tepeleri → Whisper → Claude seçimi → 9:16 video → YouTube
```

## Kurulum

Python 3.10+ ve ffmpeg gerekir.

**En kolay yol:** Repoyu indirin. Windows'ta `baslat.bat`, macOS'ta `baslat.command` dosyasına çift tıklayın. İlk çalıştırmada kurulum yapılır, sonra yardımcı uygulama açılır. İndirmeler ve Shorts için bu pencere açık kalmalı.

Elle kurulum:

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

Chrome Web Mağazası YouTube indirme eklentilerine izin vermediği için eklenti mağazadan değil bu şekilde yüklenir. Eklenti yalnızca bilgisayarınızdaki yardımcıyla (127.0.0.1) konuşur, hiçbir yere veri göndermez.

## Kullanım

```bash
clipfarm serve        # yardımcıyı başlatın ve açık bırakın (baslat.bat / baslat.command aynısını yapar)
```

### Video indirme

1. Herhangi bir sayfada videonun üstüne gelin; sağ üstte **⬇ İndir** düğmesi çıkar.
2. **Tümünü indir:** kaliteyi seçip tüm videoyu indirir. "Sadece ses" mp3 kaydeder.
3. **Klip al:** başlangıç ve bitişi yazın (`1:02:30` biçiminde) ya da videoyu istediğiniz yere getirip **⏱ şu an**'a basın. **Son 30 sn**, **Son 1 dk** ve **Buradan 1 dk** kısayolları da var. **Klibi indir** yalnızca o aralığı indirir.
4. **Shorts yap:** seçili aralıktan (en fazla 3 dk) dikey, altyazılı Shorts üretir.

Dosyalar `İndirilenler/Clipfarm` klasörüne kaydedilir (`CLIPFARM_DOWNLOADS` ile değiştirilebilir). Eklenti simgesine tıklayınca indirmelerin ilerlemesi ve **Göster** düğmesi görünür. Sayfa videoyu tanınmayan bir oynatıcıyla açıyorsa, eklenti oynatma sırasında geçen video akışlarını (m3u8, mpd, mp4) yakalar. Simgedeki sayı bunları gösterir; liste popup'ta durur ve indirme bu adreslerle tekrar denenir.

YouTube'da yaş kısıtlı veya üyelere özel videolar için yt-dlp'nin tarayıcı çerezlerini kullanması gerekebilir: `clipfarm get <adres> --cookies-from-browser chrome`.

### Shorts

Chrome'da bir yayın kaydı (YouTube videosu/canlı yayın kaydı, Twitch VOD, Kick VOD) açın. Eklenti simgesine tıklayıp **En iyi anlardan Shorts üret**'e basın. Yayındaki en iyi 3–8 an bulunur ve Shorts'a çevrilir. 5 saatlik bir yayın birkaç dakikada analiz edilir, çünkü yalnızca ses ve seçilen kesitler indirilir. Belirli bir anı Shorts yapmak için videonun üstündeki panelde aralık seçip **Shorts yap**'a basın.

Klipler panelde (`http://127.0.0.1:38765`) görünür. Her klip için videoyu izleyin; hook, başlık, açıklama ve etiketleri düzenleyin. Hook değiştiyse **Yeniden üret**, beğendiyseniz **Yükle**'ye basın. Yeni iş oluştururken "Hazır olunca YouTube'a otomatik yükle" kutusunu işaretlerseniz onay beklemeden yüklenir (varsayılan görünürlük: Özel).

Varsayılan görünüm (`--look viral`) örnek klip kanallarındaki gibidir: 3:4 kırpılmış görüntü, ortada konuşuldukça harf harf yazılıp iki satıra kadar biriken neon parıltılı altyazı (önemli kelimeler renkli, küfürler yıldızlı), üstte harf harf yazılan hook, canlı renk ve sesin patladığı kelimede kısa renk / siyah-beyaz / beyaz flaş. Altyazı her klibin kendi sesinden `medium` Whisper modeliyle çıkarılır. Eski görünüm için `--look classic`, 9:16 için `--aspect 9:16`, flaşsız için `--no-effects`, alta kanal adı için `--watermark @kanal`.

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
