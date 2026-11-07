# Teknik dokümantasyon

## Amaç ve mimari

Gateway, HP DeskJet 3700 EWS içeriğine yazıcının ağ HTTPS hizmetini kullanmadan USB LEDM arayüzü üzerinden erişir. Bir yazıcı sürücüsü veya firmware aracı değildir; yerel tarayıcı ile WinUSB bulk uçları arasında HTTP taşır.

```text
Tarayıcı
  | HTTPS / 127.0.0.1:8765
  v
ThreadingHTTPServer + ProxyHandler
  | HTTP/1.1 baytları
  v
WinUsbBridge (RLock ile tek sıralı akış)
  | bulk OUT / bulk IN
  v
HP MI_02 — FF/04/01 LEDM/EWS
```

`device_paths()` kayıt defterini yalnızca olası interface class GUID'lerini bulmak için tarar. Canlı aygıt yolları Windows Configuration Manager `CM_Get_Device_Interface_ListW` ile ve yalnızca bağlı arayüzlerden alınır. Her aday salt okunur `GET /DevMgmt/ProductConfigDyn.xml` isteğiyle sınanır.

## İstek ve yanıt akışı

Yerel sunucu `GET`, `HEAD`, `OPTIONS`, `POST`, `PUT` ve `DELETE` yöntemlerini kabul eder. İstek gövdesi 8 MiB, yanıt 16 MiB ile sınırlıdır. USB tek sıralı byte stream olduğundan eşzamanlı tarayıcı istekleri global `RLock` üzerinden sıraya alınır.

Gateway koşullu cache/range üstbilgilerini yazıcıya göndermez; beklenmeyen `304` yanıtını tek seferlik cache-buster ile tekrarlar. Chunked framing kaldırılır. gzip, deflate ve Brotli içerik kodlaması çözülür ve tarayıcıya doğru `Content-Length` verilir. Kullanıcı IP veya hostname sağlarsa mutlak yazıcı URL'leri localhost kökenine çevrilir.

## TLS modeli

TLS yalnızca tarayıcı ile aynı bilgisayardaki gateway arasındadır. İlk çalışmada OpenSSL veya Python `cryptography` ile RSA-2048/SHA-256, bir yıllık, kendinden imzalı localhost sertifikası üretilir. SAN alanı `localhost` ve `127.0.0.1` içerir; minimum TLS sürümü 1.2'dir. Özel anahtar kullanıcı profilindeki `HP_USB_EWS_Gateway` dizininde kalır, yazıcıya gönderilmez.

## Güvenlik sınırı

Dinleme adresi komut satırından değiştirilemez ve `127.0.0.1` olarak sabittir. Gateway ek kimlik doğrulama sağlamaz; aynı makinedeki yerel süreçler porta erişebilir. EWS içindeki yazma işlemleri gerçek yazıcıya iletilir.

`Authorization`, `Cookie`, istek gövdeleri, TLS özel anahtarı ve USB aygıt tanımlayıcıları günlüğe alınmamalıdır. Mevcut uygulama üstbilgi veya gövde içeriklerini yazdırmaz; yalnızca yöntem, yol, durum ve boyut gibi tanılama bilgilerini gösterir. USB sembolik yolu konsolda görünebildiğinden paylaşılan loglarda maskelenmelidir.

## Sorun giderme

### Etkin WinUSB yolu bulunamadı

`Get-PnpDevice -PresentOnly` ile `MI_02` aygıtını doğrulayın. Eski registry DeviceClasses girdileri canlı aygıt sayılmaz ve seçim için kullanılmaz.

### Canlı yol var, probe başarısız

Başka gateway süreci USB arayüzünü kullanıyor olabilir. Önce onu kapatın, sonra USB kablosunu yeniden takın.

### Tarayıcı bağlanmıyor

Konsoldaki `[SELFTEST] TLS handshake OK` satırı doğrudan soket kontrolünün başarılı olduğunu gösterir. Self-test başarılıysa tarayıcı proxy ayarlarında localhost istisnasını kontrol edin.

### Sayfa bozuk veya eksik

Konsoldaki `coding:` alanını kontrol edin. `br` için Brotli paketi gerekir. Gateway cache'i engeller; yine de gerekirse yeni özel pencereyle deneyin.

### Yönlendirme yanlış adrese gidiyor

EWS içeriğinin kullandığı adresi `--printer-ip` veya `--printer-hostname` ile verin. Bu seçenekler yalnızca içerik yeniden yazımı içindir; gateway ağ üzerinden yazıcıya bağlanmaz.

## Sürüm kökeni

İlk repository sürümü çalışan `HP_DeskJet3700_USB_EWS_Gateway_v10_YENIDEN.py` temel alınarak hazırlanmıştır. Yayın temizliğinde cihaza özgü hostname ve varsayılan özel ağ IP'si kaldırılıp isteğe bağlı parametrelere dönüştürülmüş; USB/HTTP köprü mantığı korunmuştur.