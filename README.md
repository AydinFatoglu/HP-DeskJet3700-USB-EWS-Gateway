# HP DeskJet 3700 USB EWS Gateway

Windows üzerinde USB ile bağlı bir HP DeskJet 3700 yazıcının Embedded Web Server (EWS) arayüzünü yalnızca yerel bilgisayarda açan WinUSB–HTTP köprüsüdür. Yazıcının `MI_02` LEDM/HTTP trafiğini `https://127.0.0.1:8765/` adresine taşır.

## Hangi sorunu çözüyor?

HP DeskJet 3700'ün EWS arayüzünde sunulan güvenli bağlantı/HTTPS ayarı etkinleştirildiğinde yazıcının ağ üzerinden sunduğu yönetim arayüzü bazı durumlarda kullanılamaz hale gelebilir. Tarayıcı artık gerekli sayfalara ulaşamaz; ayarı geri alacak kontroller de erişilemeyen EWS alanlarının içinde kaldığı için kullanıcı bir kurtarma çıkmazına girer.

Sorunun temelinde HP'nin bu ayarı kullanıcıya açmasına rağmen, ayar ağ EWS erişimini bozduğunda kullanılabilecek güvenilir bir geri dönüş yolu sunmaması vardır. Ayar etkinleştirildikten sonra erişilemeyen bölümler oluşabilir ve aynı arayüz üzerinden ayarı yeniden düzeltmek mümkün olmaz.

Bu arıza durumunda normal fabrika sıfırlaması ayarı temizlemez. Yazıcının üzerindeki tuş kombinasyonuyla gerçekleştirilen fabrika sıfırlaması da bu ayarı geri almaz ve bu problem için kesinlikle çözüm değildir. Yazıcı sıfırlanmış gibi görünse bile EWS'yi erişilemez bırakan güvenli bağlantı durumu korunur.

Bu proje, yazıcının sorunlu ağ HTTPS yolunu kullanmaz. Bilgisayara USB ile bağlı `MI_02` LEDM arayüzünden EWS'ye erişir ve içeriği yalnızca localhost üzerinde çalışan yerel bir HTTPS adresine taşır. Böylece ağ üzerinden artık açılamayan EWS alanlarına yeniden ulaşılabilir ve kilitlenmeye neden olan ayar yazıcının kendi yönetim arayüzünden geri alınabilir.

Bu çözüm yalnızca HTTPS ayarı nedeniyle oluşan kilitlenmeyle sınırlı değildir. Yazıcı herhangi bir nedenle Wi‑Fi'a bağlanamadığında, DHCP'den IP alamadığında veya HP'nin mobil kurulum uygulaması ya da bilgisayardaki kurulum/destek yazılımı yazıcıya ulaşıp konfigürasyonları gönderemediğinde ağ EWS'sine erişmek de mümkün olmaz. Gateway, Wi‑Fi bağlantısına ve yazıcının IP sahibi olmasına ihtiyaç duymadan USB üzerinden EWS'yi açar. Kullanıcı buradan erişilebilen ağ ve cihaz ayarlarını değiştirebilir; tarayıcının gönderdiği yapılandırma istekleri USB üzerinden yazıcıya iletildiği için ayarlar yazıcıya kaydedilebilir.

Araç Wi‑Fi bağlantısını kendiliğinden kurmaz veya kullanıcı adına bir ağ seçmez. HP uygulamalarının keşif ve kurulum yolu çalışmadığında, gerekli konfigürasyonun yazıcının kendi EWS arayüzünden gönderilip kaydedilebilmesi için alternatif ve doğrudan bir USB yönetim kanalı sağlar.
Bu, HP tarafından sağlanan resmi bir araç değil; HP'nin bıraktığı kurtarma boşluğunu USB üzerinden aşmak için geliştirilmiş yerel bir geri kazanım çözümüdür.

> **Uyarı:** Tarayıcıdaki ayar işlemleri USB üzerinden gerçek yazıcıya iletilir. Uygulama yalnızca `127.0.0.1` üzerinde kullanılmalı, LAN veya internete açılmamalıdır.

## Gereksinimler

- Windows 10 veya 11
- Python 3.9+
- USB ile bağlı HP DeskJet 3700 serisi yazıcı
- `VID_03F0&PID_0653&MI_02` arayüzünde WinUSB sürücüsü
- Yerel TLS sertifikası için `cryptography` veya PATH üzerinde `openssl.exe`

## Kurulum

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

Paket kurmadan çalıştırmak için:

```powershell
python -m pip install -r requirements.txt
python .\hp_deskjet3700_usb_ews_gateway.py
```

## Kullanım

```powershell
hp-usb-ews-gateway
```

Başarılı başlangıçta konsoldaki `OPEN IN FIREFOX` adresini açın. İlk çalışmada kendinden imzalı yerel sertifika için tarayıcı uyarısı normaldir. Sertifika ve özel anahtar varsayılan olarak `%USERPROFILE%\HP_USB_EWS_Gateway` altında oluşturulur; yazıcıya yüklenmez.

Yazıcı sayfaları mutlak ağ URL'leri içeriyorsa yalnızca yeniden yazma amacıyla adresleri açıkça verin:

```powershell
hp-usb-ews-gateway --printer-ip 192.168.1.100 --printer-hostname HP-PRINTER
```

Seçenekler:

```text
--port PORT                 Yerel port; varsayılan 8765
--cert-dir DIRECTORY        Yerel sertifika/anahtar dizini
--http                      Tanılama için düz HTTP
--printer-ip ADDRESS        Mutlak EWS URL'leri için yazıcı IP'si
--printer-hostname NAME     Mutlak EWS URL'leri için yazıcı adı
```

`--http`, HTTPS kaynak kökeni bekleyen bazı EWS ayarlarında çalışmayabilir.

## WinUSB denetimi

```powershell
Get-PnpDevice -PresentOnly | Where-Object InstanceId -like '*VID_03F0&PID_0653&MI_02*'
```

Yalnızca `MI_02` arayüzüne WinUSB atanmalıdır. Diğer yazıcı arayüzlerinin sürücülerini değiştirmek normal yazdırmayı bozabilir.

## Güvenlik

- Sunucu adresi kodda `127.0.0.1` olarak sabittir.
- İstek/yanıt boyutları sınırlıdır ve USB erişimi sıralıdır.
- Özel anahtarlar, sertifikalar ve günlükler `.gitignore` kapsamındadır.
- Konsola yazılan USB sembolik yolu benzersiz cihaz bilgisi içerebilir; hata raporlarında maskeleyin.
- Araç EWS yazma isteklerini engellemez; yazıcı ayarlarını bilinçli değiştirin.

Tasarım, veri akışı, kısıtlar ve sorun giderme için [teknik dokümana](docs/TECHNICAL.md) bakın.

## Kapsam

Bu sürüm `VID_03F0&PID_0653&MI_02` ve `FF/04/01` arayüz tanımına göre HP DeskJet 3700 ailesi için hazırlanmıştır. Başka model ve arayüzler desteklenmez.