
# Ajanda: Kişi Tabanlı Randevu Takip Uygulaması

Python ve Flask ile yazılmış, web tarayıcısında çalışan bir randevu ve ajanda uygulaması.
Kişileri kaydedip onlara randevu verebilir, randevuları liste ve takvim üzerinden yönetebilir,
yaklaşan randevular için kişilere otomatik e-posta gönderebilirsiniz.

## Özellikler

- **Kullanıcı sistemi:** Kayıt olma ve giriş yapma. Her kullanıcı yalnızca kendi verilerini görür. Şifreler hash'lenerek saklanır.
- **Kişiler:** Kişi ekleme, düzenleme ve silme. Ad, telefon, e-posta ve not tutulur. Silinen kişinin randevuları korunur, kişisiz kalır.
- **Randevular:** Ekleme, düzenleme, silme. Başlık, tarih, saat, not ve kişi bilgisi tutulur. Aynı saatte başka randevu varsa uyarı verilir.
- **Kişiye göre görünüm:** Kişinin yaklaşan ve geçmiş randevuları ayrı listelenir. Geçmiş randevular için geldi, gelmedi ve iptal özeti gösterilir.
- **Randevu durumu:** Bekliyor, Geldi, Gelmedi, İptal.
- **Tekrarlayan randevular:** Her gün, her hafta, her ay. İsteğe bağlı bitiş tarihi.
- **Takvim görünümü:** Aylık takvim. Güne tıklayınca yeni randevu, randevuya tıklayınca düzenleme, silme ve durum penceresi açılır.
- **Arama:** Kişi ve randevular içinde arama. Türkçe karakter ve büyük/küçük harf farkı yok sayılır.
- **Hatırlatma:** Tarayıcı bildirimi ve kişiye otomatik e-posta (tam zamanında, 10 dk, 30 dk, 1 saat, 2 saat, 1 gün önce).
- **Kişi eklenince bilgilendirme:** E-posta ile gönderilir. SMS desteği de vardır, ayarlanırsa çalışır.

## Kurulum ve çalıştırma

Python 3.9 veya üstü gerekir.

```bash
pip install -r requirements.txt
python app.py
```

Ardından tarayıcıda `http://127.0.0.1:5000` adresini açın ve hesap oluşturun.
Veritabanı (`ajanda2.db`) ilk çalıştırmada otomatik oluşturulur.

## E-posta ayarı (isteğe bağlı)

E-posta ayarlanmazsa uygulama yine çalışır, e-posta metinleri gönderilmek yerine terminale yazılır.
Gerçek e-posta için Gmail'de 2 Adımlı Doğrulama'yı açıp
<https://myaccount.google.com/apppasswords> adresinden bir **uygulama şifresi** oluşturun.
Sonra uygulamayı başlatmadan önce (PowerShell):

```powershell
$env:SMTP_USER="sizin@gmail.com"
$env:SMTP_PASS="16 haneli uygulama şifresi"
python app.py
```

Başka bir sağlayıcı için `SMTP_HOST` (varsayılan `smtp.gmail.com`) ve `SMTP_PORT` (varsayılan `587`) de ayarlanabilir.

Hatırlatma e-postaları arka planda her dakika kontrol edilerek gönderilir, bu yüzden **uygulama açıkken** çalışır.
Bir kişiye e-posta gitmesi için kişinin e-postası, randevunun saati ve hatırlatma seçeneği dolu olmalıdır.

## SMS ayarı (isteğe bağlı)

SMS göndermek bir servis hesabı gerektirir. Netgsm (`NETGSM_USER`, `NETGSM_PASS`, `NETGSM_HEADER`)
ya da Twilio (`TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM`) ortam değişkenleri tanımlanırsa
kişi eklerken SMS de gönderilir. Tanımlı değilse SMS adımı atlanır.

## Proje yapısı

```
ajanda/
├── app.py               # Flask uygulaması: yollar, veritabanı, e-posta ve hatırlatıcı
├── requirements.txt
└── templates/           # Sayfa şablonları (Jinja)
    ├── base.html        # Ortak düzen, menü, yeni randevu penceresi
    ├── index.html       # Randevu listesi ve günlük özet
    ├── takvim.html      # Aylık takvim
    ├── kisiler.html     # Kişi listesi ve kişi ekleme
    ├── kisi.html        # Kişi sayfası: yaklaşan ve geçmiş randevular
    ├── kisi_duzenle.html
    ├── duzenle.html     # Randevu düzenleme
    ├── ara.html         # Arama sonuçları
    └── giris.html       # Giriş ve kayıt
```

## Güvenlik notları

- `SECRET_KEY` ortam değişkenini kendi değerinizle ayarlayın. Varsayılan değer yalnızca geliştirme içindir.
- Şifreleri, uygulama şifrelerini ve API anahtarlarını koda yazmayın, GitHub'a yüklemeyin.
- Uygulama geliştirme sunucusuyla çalışır ve formlarda CSRF koruması yoktur.
  İnternette yayınlanacaksa bir üretim sunucusu (örn. gunicorn) ve CSRF koruması eklenmelidir.

## Kullanılan teknolojiler

Python, Flask, SQLite, Jinja2, HTML ve CSS, JavaScript (bildirimler ve açılır pencereler).
