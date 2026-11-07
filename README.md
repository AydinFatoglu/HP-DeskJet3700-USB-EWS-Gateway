# HP DeskJet 3700 USB EWS Gateway

Windows üzerinde USB ile bağlı bir HP DeskJet 3700 yazıcının Embedded Web Server (EWS) arayüzünü yalnızca yerel bilgisayarda açan WinUSB–HTTP köprüsüdür. Yazıcının `MI_02` LEDM/HTTP trafiğini `https://127.0.0.1:8765/` adresine taşır.

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