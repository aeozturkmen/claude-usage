# Claude Usage Dashboard: ikinci macOS profiline kurulum

Bu rehber dashboard'u bu Mac'teki ikinci profile (`batmanbegins`) kurar. Sonuçta iki profil **aynı veritabanına yazar ve aynı adresi kullanır**:

- Adres: **http://localhost:9050** (hangi profilde olursan ol aynı adres)
- Dashboard iki profilin Claude Code + Cowork kullanımını birlikte gösterir. Başlıkta `Profiles: batmanbegins + ozturkmen` yazar.
- Terminal komutları (`cli.py today`, `week`, `stats`) iki profilde de ortak veriyi okur.

---

## 1. Nasıl çalışıyor

```
/Users/Shared/ClaudeUsage/          (iki profilin de yazabildiği ortak klasör, grup: staff)
├── app/                            kod: iki profil aynı sürümü çalıştırır
│   ├── cli.py, dashboard.py, scanner.py, pricing.py
│   ├── KURULUM.md                  bu rehber
│   └── deploy/install.sh, status.sh, uninstall.sh
├── usage.db                        ortak veritabanı
└── .usage-sync/                    profiller arası küçük sinyal dosyaları
```

- Her profilin kendi arka plan ajanı (LaunchAgent `com.claudeusage.dashboard`) vardır. macOS bir profilin diğerinin `~/.claude` klasörünü okumasına izin vermez. Bu yüzden her profil **kendi** transcript'lerini kendisi tarar ve ortak veritabanına yazar.
- 9050 portunu ilk açan profil **SUNUCU** olur. Diğer profil **YEDEK** modda bekler.
- Sayfayı her açtığında (ve Rescan'e bastığında) sunucu, yedek profile "sen de tara" sinyali gönderir ve cevabını en fazla 15 sn bekler. Böylece iki profilin verisi sayfa yüklenirken güncel gelir.
- Yedek profil ayrıca 15 dakikada bir kendiliğinden tarar.
- Sunucu profilin oturumu kapanırsa yedek profil portu **30 sn içinde** devralır. Adres değişmez.
- `app/` içindeki kod güncellenirse iki ajan da **60 sn içinde** kendini yeni kodla yeniden başlatır. Fiyat tablosu iki profilde hep aynı kalır.

> Not: İki profil aynı anda açık değilse (Hızlı Kullanıcı Değiştirme kapalıysa) sadece açık olan profil çalışır. Kapalı profilin yeni kullanımı zaten olmaz; eski verisi veritabanında durur.

---

## 2. Ön koşullar

- Python 3.8+. macOS'un `/usr/bin/python3`'ü yeterli. Yoksa Terminal'de `xcode-select --install` çalıştır.
- `sudo` gerekmez.
- Sıra önemli: **önce `ozturkmen`, sonra `batmanbegins`.** İlk kurulum `ozturkmen`'in mevcut geçmişini (eski oturumlar dahil) ortak veritabanının başlangıcı yapar.

Zip ve bu rehber iki profilden de erişilebilen ortak klasörde duruyor: `/Users/Shared/ClaudeUsage/`.

---

## 3. Kurulum

### Adım 1: `ozturkmen` profilinde (bir kez)

Mevcut dashboard (`~/.claude/usage.db`, port 9050) ortak yapıya taşınır. Eski DB ve eski ajan ayarı silinmez, `~/.claude/claude-usage-backup/` altına yedeklenir.

```bash
bash ~/Vibe-Projects/"Claude Code Scripts"/claude-usage/deploy/install.sh
```

Çıktının sonunda `✔ Ajan çalışıyor`, `✔ Dashboard açık` ve `Rol: SUNUCU` görmelisin. http://localhost:9050 eskisi gibi açılır, aynı veriyi gösterir.

### Adım 2: `batmanbegins` profilinde

`batmanbegins` olarak oturum aç, Terminal'i aç ve iki yoldan birini seç.

**Yol A: Zip ile**

1. Zip'i aç:

```bash
cd ~/Downloads && unzip -o /Users/Shared/ClaudeUsage/claude-usage-kurulum.zip
```

2. Kurulum script'ini çalıştır:

```bash
bash ~/Downloads/claude-usage-kurulum/deploy/install.sh
```

**Yol B: Zip'siz (daha kısa)**

Adım 1'den sonra kod zaten ortak klasörde olduğu için zip'e gerek yok:

```bash
bash /Users/Shared/ClaudeUsage/app/deploy/install.sh
```

İki yol da aynı sonucu verir. Zip bir kerelik bir pakettir. En güncel kod her zaman `/Users/Shared/ClaudeUsage/app/` içindedir.

### Beklenen çıktı

Script sonunda durumu kendisi gösterir:

```
Claude Usage Dashboard: batmanbegins profili
  ✔ Ajan çalışıyor (pid 12345)
  ✔ Dashboard açık: http://localhost:9050
  • Rol: YEDEK (sayfayı diğer profil sunuyor; bu profil kendi verisini ortak DB'ye yazıyor)
  ✔ Ortak DB: /Users/Shared/ClaudeUsage/usage.db (27M)
  • Veri gelen profil: batmanbegins (3 dosya, son değişiklik 2026-11-10 01:20:00)
  • Veri gelen profil: ozturkmen (780 dosya, son değişiklik 2026-11-10 01:19:00)
  ✔ Terminal komutları (cli.py today/week) ortak DB'yi kullanıyor
```

`ozturkmen` o an oturum açık değilse rol `SUNUCU` görünür. İkisi de doğru.

### Doğrulama

1. Tarayıcıda **http://localhost:9050** aç.
2. Başlıkta `Updated: ... · Profiles: batmanbegins + ozturkmen` yazmalı.
   `batmanbegins` henüz hiç Claude Code oturumu açmadıysa sadece `ozturkmen` görünür. Bir oturum açıp sayfayı yenile.
3. İstersen iki profilde de durum kontrolü:

```bash
bash /Users/Shared/ClaudeUsage/app/deploy/status.sh
```

---

## 4. Script ne yapıyor

`deploy/install.sh` her profilde aynı adımları uygular. Tekrar çalıştırmak güvenlidir.

1. Python 3.8+ bulur (`/usr/bin/python3` öncelikli).
2. `/Users/Shared/ClaudeUsage` klasörünü oluşturur veya kontrol eder. İzinler: grup `staff`, grup yazabilir (775).
3. Zip'ten çalıştırıldıysa kodu `app/` içine kopyalar. Dosyalar tek tek ve atomik değişir. Çalışan ajan yarım dosya okumaz.
4. Bu profilin eski ajanını durdurur.
5. Veritabanı:
   - Ortak DB yoksa: bu profilin `~/.claude/usage.db` dosyası ortak DB olur.
   - Ortak DB varsa ve bu profilin eski bir `~/.claude/usage.db`'si de varsa: eski geçmiş ortak DB'ye eklenir (aynı kayıt iki kez yazılmaz).
   - Eski dosya silinmez. `~/.claude/claude-usage-backup/` altına taşınır.
6. `~/.claude/usage-db-path` dosyasını yazar. Böylece terminal komutları da ortak DB'yi kullanır.
7. `~/Library/LaunchAgents/com.claudeusage.dashboard.plist` dosyasını yazar (eskisi `~/.claude/claude-usage-backup/` altına yedeklenir) ve ajanı başlatır.
8. `status.sh` ile sonucu gösterir.

---

## 5. Günlük kullanım

| Ne | Nasıl |
|---|---|
| Dashboard | http://localhost:9050 |
| Bugün / son 7 gün / tüm zamanlar (terminal) | `python3 /Users/Shared/ClaudeUsage/app/cli.py today` (veya `week`, `stats`) |
| Durum kontrolü | `bash /Users/Shared/ClaudeUsage/app/deploy/status.sh` |
| Loglar | `~/Library/Logs/claudeusage-dashboard.log` ve `.err` (her profilin kendi logu) |

- Aylık bütçe kartına tıklayarak bütçe girilir. Bu değer tarayıcıya kaydedilir, yani her profilin tarayıcısında ayrı ayrı girilir.
- Rescan butonu sunucu profilin tüm transcript'lerini yeniden okur ve yedek profile de tam tarama yaptırır.

---

## 6. Kodu güncelleme (`ozturkmen`, geliştirme)

Geliştirme reposu: `~/Vibe-Projects/Claude Code Scripts/claude-usage`. Ajanlar artık repodan değil, `/Users/Shared/ClaudeUsage/app/` içinden çalışır. Değişiklikten sonra:

```bash
cd ~/Vibe-Projects/"Claude Code Scripts"/claude-usage && python3 -m unittest discover -s tests && bash deploy/install.sh
```

İki profilin ajanı yeni kodu 60 sn içinde kendiliğinden yükler. `batmanbegins` tarafında bir şey yapmana gerek yok.

---

## 7. Sorun giderme

| Belirti | Olası neden | Çözüm |
|---|---|---|
| `status.sh`: "Ajan çalışmıyor" | Kurulum yarıda kaldı veya Python hatası | `install.sh`'i tekrar çalıştır. Hata için: `tail -50 ~/Library/Logs/claudeusage-dashboard.err` |
| Başlıkta sadece bir profil var | Diğer profil kurulmadı veya Claude Code'u hiç kullanmadı | Diğer profilde `status.sh` çalıştır. Bir Claude Code oturumu açıp sayfayı yenile |
| `Permission denied` veya `readonly database` | Ortak klasör izinleri bozulmuş | Klasörün sahibi olan profilde: `chgrp -R staff /Users/Shared/ClaudeUsage && chmod -R g+w /Users/Shared/ClaudeUsage` |
| Sayfa açılırken 15 sn bekliyor | Yedek profil sinyale cevap vermiyor | Bir kez bekledikten sonra o profil otomatik atlanır. Diğer profilde `status.sh` ile ajanı kontrol et |
| http://localhost:9050 açılmıyor | Hiçbir profilde ajan çalışmıyor | Açık profilde `status.sh`, gerekirse `install.sh` |
| `python3` bulunamadı | Command Line Tools yüklü değil | `xcode-select --install`, sonra `install.sh` |
| 9050 portunu başka bir uygulama kullanıyor | Port çakışması | İki profilde de aynı portla kur: `CLAUDE_USAGE_PORT=9060 bash /Users/Shared/ClaudeUsage/app/deploy/install.sh` |

Ajanı elle yeniden başlatmak:

```bash
launchctl kickstart -k gui/$(id -u)/com.claudeusage.dashboard
```

---

## 8. Kaldırma ve geri alma

**Bu profilden kaldır** (ortak klasör ve veri durur):

```bash
bash /Users/Shared/ClaudeUsage/app/deploy/uninstall.sh
```

**Tamamen sil:** iki profilde de `uninstall.sh` çalıştır, sonra bir kez:

```bash
rm -rf /Users/Shared/ClaudeUsage
```

**`ozturkmen` için eski tek-profil kuruluma dön** (ortak DB'nin son hali yerel DB olur, iki profilin verisi dahil):

```bash
bash /Users/Shared/ClaudeUsage/app/deploy/uninstall.sh
cp /Users/Shared/ClaudeUsage/usage.db ~/.claude/usage.db
ls ~/.claude/claude-usage-backup/
```

Sonra listedeki `com.claudeusage.dashboard.plist.<tarih>` yedeğini geri koy ve ajanı başlat (`<tarih>` yerine listedeki değeri yaz):

```bash
cp ~/.claude/claude-usage-backup/com.claudeusage.dashboard.plist.<tarih> ~/Library/LaunchAgents/com.claudeusage.dashboard.plist
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.claudeusage.dashboard.plist
```

---

## 9. Teknik notlar

**Ayarlar (ortam değişkenleri)**

| Değişken | Varsayılan | Ne işe yarar |
|---|---|---|
| `CLAUDE_USAGE_DB` | `~/.claude/usage-db-path` içindeki yol, yoksa `~/.claude/usage.db` | Veritabanı yolu |
| `CLAUDE_USAGE_SHARED` | `/Users/Shared/ClaudeUsage` | `install.sh` için ortak klasör |
| `CLAUDE_USAGE_PORT` | `9050` | `install.sh` için port (iki profilde aynı olmalı) |
| `CLAUDE_USAGE_SCAN_INTERVAL` | `900` | Yedek profilin kendiliğinden tarama aralığı (sn) |
| `CLAUDE_USAGE_BUDGET` | yok | Aylık bütçe kartı için varsayılan değer |

**Profiller arası sinyal** (`/Users/Shared/ClaudeUsage/.usage-sync/`)

- `standby-<uid>.pid`: yedek profilin süreç numarası. Sunucu bununla yedek profilin hayatta olup olmadığını anlar.
- `scan.request`: sunucunun "tara" isteği (zaman damgası, `full` veya `incremental`).
- `standby-<uid>.done`: yedek profilin "taradım" cevabı.

Yedek profil 5 sn'de bir sadece bu küçük dosyayı okur. Arka planda başka iş yapmaz.

**Gizlilik:** Veritabanına transcript içeriği yazılmaz. Sadece token sayıları, model, proje adı, git branch ve oturum başlığı yazılır. Oturum başlığı ilk prompt'un ilk 120 karakteri olabilir. Bu bilgiler iki profilden de görünür. Dashboard sadece `localhost`'ta dinler, ağdan erişilemez.

**Farklı bir Mac:** Bu yapı aynı Mac'teki profiller içindir. Farklı Mac'ler arasında SQLite dosyasını iCloud veya Dropbox üzerinden paylaşmak önerilmez (bozulma riski). Zip başka bir Mac'e de kurulur, ama o Mac'in verisi ayrı kalır.
