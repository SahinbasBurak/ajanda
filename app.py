import base64
import calendar
import json
import os
import re
import smtplib
import threading
import time
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from itertools import groupby
from email.message import EmailMessage
from urllib import parse, request as urlrequest

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gelistirme-anahtari-bunu-degistir")
DB = "ajanda2.db"

GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
TEKRARLAR = ("yok", "gunluk", "haftalik", "aylik")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    with sqlite3.connect(DB) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS kullanici (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad TEXT NOT NULL UNIQUE, sifre TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS etkinlik (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kullanici_id INTEGER NOT NULL,
                baslik TEXT NOT NULL, tarih TEXT NOT NULL, saat TEXT,
                not_metni TEXT, tekrar TEXT NOT NULL DEFAULT 'yok',
                bitis TEXT, hatirlat INTEGER);
            CREATE TABLE IF NOT EXISTS kisi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kullanici_id INTEGER NOT NULL, ad TEXT NOT NULL,
                telefon TEXT, eposta TEXT, not_metni TEXT);
            CREATE TABLE IF NOT EXISTS gonderilen (
                etkinlik_id INTEGER NOT NULL, tarih TEXT NOT NULL,
                PRIMARY KEY (etkinlik_id, tarih));
            CREATE TABLE IF NOT EXISTS tamam (
                etkinlik_id INTEGER NOT NULL, tarih TEXT NOT NULL,
                PRIMARY KEY (etkinlik_id, tarih));
        """)
        if "kisi_id" not in [r[1] for r in db.execute("PRAGMA table_info(etkinlik)")]:
            db.execute("ALTER TABLE etkinlik ADD COLUMN kisi_id INTEGER")
        if "durum" not in [r[1] for r in db.execute("PRAGMA table_info(tamam)")]:
            db.execute("ALTER TABLE tamam ADD COLUMN durum TEXT NOT NULL DEFAULT 'geldi'")


def giris_gerekli(f):
    @wraps(f)
    def sar(*a, **k):
        if "uid" not in session:
            return redirect(url_for("giris"))
        return f(*a, **k)
    return sar


def olusumlar(e, bas, son):
    """Bir etkinliğin [bas, son] aralığındaki tüm tarihlerini üretir."""
    d0 = date.fromisoformat(e["tarih"])
    if e["bitis"]:
        son = min(son, date.fromisoformat(e["bitis"]))
    if e["tekrar"] == "yok":
        if bas <= d0 <= son:
            yield d0
    elif e["tekrar"] in ("gunluk", "haftalik"):
        adim = 1 if e["tekrar"] == "gunluk" else 7
        d = d0
        if d < bas:
            d += timedelta(days=-(-(bas - d).days // adim) * adim)
        while d <= son:
            yield d
            d += timedelta(days=adim)
    else:  # aylik: ayın kısa olduğu durumlarda son güne çekilir
        y, m = d0.year, d0.month
        while True:
            d = date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))
            if d > son:
                break
            if d >= bas:
                yield d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def listele(bas, son, kisi_id=None):
    db, uid = get_db(), session["uid"]
    sorgu, arg = "SELECT * FROM etkinlik WHERE kullanici_id=? AND tarih<=?", [uid, son.isoformat()]
    if kisi_id:
        sorgu += " AND kisi_id=?"
        arg.append(kisi_id)
    satirlar = db.execute(sorgu, arg).fetchall()
    adlar = {r["id"]: r["ad"] for r in db.execute(
        "SELECT id, ad FROM kisi WHERE kullanici_id=?", (uid,))}
    yapilan = {(r[0], r[1]): r[2] for r in db.execute(
        "SELECT t.etkinlik_id, t.tarih, t.durum FROM tamam t JOIN etkinlik e "
        "ON e.id=t.etkinlik_id WHERE e.kullanici_id=?", (uid,))}
    cikti = []
    for e in satirlar:
        for d in olusumlar(e, bas, son):
            cikti.append({**dict(e), "tarih": d, "kisi_ad": adlar.get(e["kisi_id"]),
                          "yapildi": (e["id"], d.isoformat()) in yapilan,
                          "durum": yapilan.get((e["id"], d.isoformat()))})
    cikti.sort(key=lambda k: (k["tarih"], k["saat"] or ""))
    return cikti


def gun_etiketi(d):
    fark = (d - date.today()).days
    ad = {0: "Bugün", 1: "Yarın", -1: "Dün"}.get(fark, GUNLER[d.weekday()])
    return ad, f"{d.day} {AYLAR[d.month - 1]} {d.year}"


RENKLER = ["#2f7d5b", "#b5483a", "#3a6ea5", "#8a5fb0", "#c0841a", "#2a8a8f", "#a8456b", "#5f6f2e"]
DURUMLAR = ("bekliyor", "geldi", "gelmedi", "iptal")
app.jinja_env.globals["durum_ad"] = {"geldi": "Geldi", "gelmedi": "Gelmedi", "iptal": "İptal"}
app.jinja_env.globals["kisi_renk"] = lambda i: RENKLER[(i or 0) % len(RENKLER)]
app.jinja_env.globals["bas_harf"] = lambda ad: "".join(p[0] for p in (ad or "?").split()[:2]).upper()


@app.context_processor
def ortak():
    if "uid" in session:
        return {"kisiler_hepsi": kisileri_al(), "bugun_iso": date.today().isoformat()}
    return {}


# ---------- Kimlik ----------
def kimlik(kayit):
    hata = None
    if request.method == "POST":
        ad = request.form.get("ad", "").strip().lower()
        sifre = request.form.get("sifre", "")
        db = get_db()
        if kayit:
            if len(ad) < 3 or len(sifre) < 6:
                hata = "Kullanıcı adı en az 3, şifre en az 6 karakter olmalı."
            else:
                try:
                    c = db.execute("INSERT INTO kullanici (ad, sifre) VALUES (?, ?)",
                                   (ad, generate_password_hash(sifre)))
                    db.commit()
                    session["uid"] = c.lastrowid
                    return redirect(url_for("index"))
                except sqlite3.IntegrityError:
                    hata = "Bu kullanıcı adı alınmış."
        else:
            u = db.execute("SELECT * FROM kullanici WHERE ad=?", (ad,)).fetchone()
            if u and check_password_hash(u["sifre"], sifre):
                session["uid"] = u["id"]
                return redirect(url_for("index"))
            hata = "Kullanıcı adı veya şifre hatalı."
    return render_template("giris.html", kayit=kayit, hata=hata)


@app.route("/giris", methods=["GET", "POST"])
def giris():
    return kimlik(False)


@app.route("/kayit", methods=["GET", "POST"])
def kayit():
    return kimlik(True)


@app.route("/cikis")
def cikis():
    session.clear()
    return redirect(url_for("giris"))


# ---------- Sayfalar ----------
@app.route("/")
@giris_gerekli
def index():
    gecmis = request.args.get("gecmis") == "1"
    bugun = date.today()
    bas = bugun - timedelta(days=30) if gecmis else bugun
    gunler = []
    for d, grup in groupby(listele(bas, bugun + timedelta(days=60)), key=lambda k: k["tarih"]):
        ad, uzun = gun_etiketi(d)
        gunler.append({"ad": ad, "tarih": uzun, "gecti": d < bugun, "kayitlar": list(grup)})
    simdi = datetime.now().strftime("%H:%M")
    bugunku = [k for k in listele(bugun, bugun) if not k["yapildi"]]
    siradaki = next((k for k in bugunku if k["saat"] and k["saat"] >= simdi), None)
    return render_template("index.html", gunler=gunler, gecmis=gecmis, bugun_tarih=bugun,
                           ozet={"sayi": len(bugunku), "siradaki": siradaki})


@app.route("/takvim")
@giris_gerekli
def takvim():
    try:
        y, m = map(int, request.args.get("ay", "").split("-"))
        ilk = date(y, m, 1)
    except ValueError:
        t = date.today()
        y, m, ilk = t.year, t.month, t.replace(day=1)
    haftalar = calendar.Calendar(0).monthdatescalendar(y, m)
    gunluk = {}
    for k in listele(haftalar[0][0], haftalar[-1][-1]):
        gunluk.setdefault(k["tarih"], []).append(k)
    onceki = ilk - timedelta(days=1)
    sonraki = ilk + timedelta(days=32)
    return render_template(
        "takvim.html", haftalar=haftalar, gunluk=gunluk, ay=m, baslik=f"{AYLAR[m - 1]} {y}",
        onceki=f"{onceki.year}-{onceki.month:02d}", sonraki=f"{sonraki.year}-{sonraki.month:02d}",
        bugun=date.today(), gun_kisa=[g[:3] for g in GUNLER])


@app.route("/api/hatirlat")
@giris_gerekli
def api_hatirlat():
    bugun = date.today()
    return jsonify([
        {"id": k["id"], "baslik": k["baslik"], "dk": k["hatirlat"],
         "zaman": f'{k["tarih"].isoformat()}T{k["saat"]}'}
        for k in listele(bugun, bugun + timedelta(days=1))
        if k["saat"] and k["hatirlat"] is not None and not k["yapildi"]])


# ---------- İşlemler ----------
@app.post("/ekle")
@giris_gerekli
def ekle():
    f = request.form
    baslik, tarih = f.get("baslik", "").strip(), f.get("tarih", "")
    tekrar = f.get("tekrar", "yok")
    if baslik and tarih and tekrar in TEKRARLAR:
        hatirlat = f.get("hatirlat", "")
        c = get_db().execute(
            "INSERT INTO etkinlik (kullanici_id, baslik, tarih, saat, not_metni, tekrar, bitis, hatirlat, kisi_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session["uid"], baslik, tarih, f.get("saat") or None,
             f.get("not_metni", "").strip() or None, tekrar, f.get("bitis") or None,
             int(hatirlat) if hatirlat.isdigit() else None, kisi_id_al(f.get("kisi_id"))))
        get_db().commit()
        cakisma_uyar(c.lastrowid, tarih, f.get("saat"))
    return redirect(url_for("index"))


@app.post("/tamamla/<int:eid>/<tarih>")
@giris_gerekli
def tamamla(eid, tarih):
    db = get_db()
    if db.execute("SELECT 1 FROM etkinlik WHERE id=? AND kullanici_id=?",
                  (eid, session["uid"])).fetchone():
        if db.execute("DELETE FROM tamam WHERE etkinlik_id=? AND tarih=?", (eid, tarih)).rowcount == 0:
            db.execute("INSERT INTO tamam (etkinlik_id, tarih) VALUES (?, ?)", (eid, tarih))
        db.commit()
    return redirect(request.referrer or url_for("index"))


@app.post("/sil/<int:eid>")
@giris_gerekli
def sil(eid):
    db = get_db()
    if db.execute("DELETE FROM etkinlik WHERE id=? AND kullanici_id=?",
                  (eid, session["uid"])).rowcount:
        db.execute("DELETE FROM tamam WHERE etkinlik_id=?", (eid,))
        db.execute("DELETE FROM gonderilen WHERE etkinlik_id=?", (eid,))
    db.commit()
    return redirect(request.referrer or url_for("index"))


# ---------- Kişiler ----------
def kisileri_al():
    return get_db().execute("SELECT * FROM kisi WHERE kullanici_id=? ORDER BY ad",
                            (session["uid"],)).fetchall()


def kisi_id_al(deger):
    """Seçilen kişi bu kullanıcıya aitse id'sini, değilse None döndürür."""
    if deger and deger.isdigit() and get_db().execute(
            "SELECT 1 FROM kisi WHERE id=? AND kullanici_id=?", (int(deger), session["uid"])).fetchone():
        return int(deger)
    return None


def tel_duzenle(t):
    t = re.sub(r"[^\d+]", "", t or "")
    if t.startswith("00"):
        t = "+" + t[2:]
    elif t.startswith("0"):
        t = "+9" + t
    elif len(t) == 10 and t.startswith("5"):
        t = "+90" + t
    return t


def sms_gonder(telefon, mesaj):
    """Netgsm veya Twilio ayarlıysa gerçek SMS yollar; değilse konsola yazar (simülasyon)."""
    ng_kullanici, ng_sifre = os.environ.get("NETGSM_USER"), os.environ.get("NETGSM_PASS")
    if ng_kullanici and ng_sifre:  # Netgsm REST v2 (Türkiye)
        veri = json.dumps({"msgheader": os.environ.get("NETGSM_HEADER", ng_kullanici),
                           "messages": [{"msg": mesaj, "no": telefon[-10:]}],
                           "encoding": "TR"}).encode()
        istek = urlrequest.Request("https://api.netgsm.com.tr/sms/rest/v2/send", data=veri,
                                   headers={"Content-Type": "application/json"})
        istek.add_header("Authorization", "Basic " + base64.b64encode(
            f"{ng_kullanici}:{ng_sifre}".encode()).decode())
        try:
            cevap = json.loads(urlrequest.urlopen(istek, timeout=10).read().decode())
            return "SMS gönderildi." if str(cevap.get("code")) == "00" else f"Netgsm hata döndürdü: {cevap}"
        except Exception as hata:
            return f"SMS gönderilemedi: {hata}"
    sid, token, kaynak = (os.environ.get(k) for k in ("TWILIO_SID", "TWILIO_TOKEN", "TWILIO_FROM"))
    if not (sid and token and kaynak):
        print(f"[SMS SİMÜLASYONU] {telefon}: {mesaj}")
        return "SMS servisi ayarlı olmadığı için SMS gönderilmedi (simülasyon: terminale yazıldı)."
    veri = parse.urlencode({"To": telefon, "From": kaynak, "Body": mesaj}).encode()
    istek = urlrequest.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=veri)
    istek.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    try:
        urlrequest.urlopen(istek, timeout=10)
        return "SMS gönderildi."
    except Exception as hata:
        return f"SMS gönderilemedi: {hata}"


def sms_ayarli():
    e = os.environ.get
    return bool((e("NETGSM_USER") and e("NETGSM_PASS")) or (e("TWILIO_SID") and e("TWILIO_TOKEN") and e("TWILIO_FROM")))


def eposta_gonder(adres, konu, metin):
    """SMTP ayarlıysa gerçek e-posta yollar; değilse terminale yazar (simülasyon)."""
    sunucu = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    kullanici, sifre = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (kullanici and sifre):
        print(f"[E-POSTA SİMÜLASYONU] {adres} | {konu} | {metin}")
        return "E-posta ayarı yapılmadığı için gönderilmedi (simülasyon: terminale yazıldı)."
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM", kullanici)
    msg["To"], msg["Subject"] = adres, konu
    msg.set_content(metin)
    try:
        with smtplib.SMTP(sunucu, int(os.environ.get("SMTP_PORT", 587)), timeout=15) as sm:
            sm.starttls()
            sm.login(kullanici, sifre)
            sm.send_message(msg)
        return f"E-posta gönderildi: {adres}"
    except Exception as hata:
        return f"E-posta gönderilemedi: {hata}"


@app.route("/kisiler")
@giris_gerekli
def kisiler():
    return render_template("kisiler.html", kisiler=kisileri_al())


@app.post("/kisi/ekle")
@giris_gerekli
def kisi_ekle():
    f = request.form
    ad = f.get("ad", "").strip()
    if not ad:
        return redirect(url_for("kisiler"))
    tel = tel_duzenle(f.get("telefon"))
    get_db().execute("INSERT INTO kisi (kullanici_id, ad, telefon, eposta, not_metni) VALUES (?, ?, ?, ?, ?)",
                     (session["uid"], ad, tel or None, f.get("eposta", "").strip() or None,
                      f.get("not_metni", "").strip() or None))
    get_db().commit()
    flash(f"{ad} eklendi.")
    if f.get("bilgi"):
        eposta = f.get("eposta", "").strip()
        sms_var = bool(tel and sms_ayarli())
        if eposta:
            flash(eposta_gonder(eposta, "Randevu sistemine kaydedildiniz",
                                f"Merhaba {ad},\n\nRandevu sistemimize kaydedildiniz. "
                                "Randevu bilgileriniz bu adrese iletilecektir."))
        if sms_var:
            if re.fullmatch(r"\+\d{10,15}", tel):
                flash(sms_gonder(tel, f"Merhaba {ad}, randevu sistemimize kaydedildiniz."))
            else:
                flash("Telefon numarası geçersiz olduğu için SMS gönderilmedi.")
        if not eposta and not sms_var:
            flash("Bilgilendirme gönderilemedi: e-posta adresi girilmedi.")
    return redirect(url_for("kisiler"))


@app.route("/kisi/<int:kid>")
@giris_gerekli
def kisi_detay(kid):
    k = get_db().execute("SELECT * FROM kisi WHERE id=? AND kullanici_id=?",
                         (kid, session["uid"])).fetchone()
    if not k:
        return redirect(url_for("kisiler"))
    bugun = date.today()
    tum = listele(date(2000, 1, 1), bugun - timedelta(days=1), kid)
    sayac = {d: sum(1 for r in tum if r["durum"] == d) for d in ("geldi", "gelmedi", "iptal")}
    return render_template(
        "kisi.html", k=k, yaklasan=listele(bugun, bugun + timedelta(days=365), kid)[:200],
        gecmis=tum[::-1][:100], toplam=len(tum), sayac=sayac)


_TR = str.maketrans("çğıöşüÇĞİIÖŞÜ", "cgiosucgiiosu")


def sade(metin):
    """Aramada Türkçe karakter ve büyük/küçük harf farkını yok sayar."""
    return (metin or "").translate(_TR).lower()


def cakisma_uyar(eid, tarih, saat):
    if not saat:
        return
    try:
        d = date.fromisoformat(tarih)
    except ValueError:
        return
    diger = [k["baslik"] for k in listele(d, d) if k["saat"] == saat and k["id"] != eid and not k["yapildi"]]
    if diger:
        flash(f"Dikkat: {d.day} {AYLAR[d.month - 1]} saat {saat} için başka randevu da var: " + ", ".join(diger))


@app.route("/ara")
@giris_gerekli
def ara():
    q = request.args.get("q", "").strip()
    randevular, bulunan = [], []
    if q:
        n = sade(q)
        bulunan = [k for k in kisileri_al()
                   if n in sade(" ".join(filter(None, [k["ad"], k["telefon"], k["eposta"], k["not_metni"]])))]
        satirlar = get_db().execute(
            "SELECT e.*, k.ad AS kisi_ad FROM etkinlik e LEFT JOIN kisi k ON k.id = e.kisi_id "
            "WHERE e.kullanici_id=? ORDER BY e.tarih DESC", (session["uid"],)).fetchall()
        randevular = [e for e in satirlar
                      if n in sade(" ".join(filter(None, [e["baslik"], e["not_metni"], e["kisi_ad"]])))][:100]
    return render_template("ara.html", q=q, randevular=randevular, bulunan=bulunan)


@app.route("/kisi/duzenle/<int:kid>", methods=["GET", "POST"])
@giris_gerekli
def kisi_duzenle(kid):
    db = get_db()
    k = db.execute("SELECT * FROM kisi WHERE id=? AND kullanici_id=?", (kid, session["uid"])).fetchone()
    if not k:
        return redirect(url_for("kisiler"))
    if request.method == "POST":
        f = request.form
        ad = f.get("ad", "").strip()
        if ad:
            db.execute("UPDATE kisi SET ad=?, telefon=?, eposta=?, not_metni=? WHERE id=? AND kullanici_id=?",
                       (ad, tel_duzenle(f.get("telefon")) or None, f.get("eposta", "").strip() or None,
                        f.get("not_metni", "").strip() or None, kid, session["uid"]))
            db.commit()
            flash("Kişi bilgileri güncellendi.")
            return redirect(url_for("kisi_detay", kid=kid))
    return render_template("kisi_duzenle.html", k=k)


@app.post("/durum/<int:eid>/<tarih>")
@giris_gerekli
def durum_ata(eid, tarih):
    secim, db = request.form.get("durum", ""), get_db()
    if secim in DURUMLAR and db.execute("SELECT 1 FROM etkinlik WHERE id=? AND kullanici_id=?",
                                        (eid, session["uid"])).fetchone():
        db.execute("DELETE FROM tamam WHERE etkinlik_id=? AND tarih=?", (eid, tarih))
        if secim != "bekliyor":
            db.execute("INSERT INTO tamam (etkinlik_id, tarih, durum) VALUES (?, ?, ?)", (eid, tarih, secim))
        db.commit()
    return redirect(request.referrer or url_for("index"))


@app.post("/kisi/sil/<int:kid>")
@giris_gerekli
def kisi_sil(kid):
    db, uid = get_db(), session["uid"]
    k = db.execute("SELECT ad FROM kisi WHERE id=? AND kullanici_id=?", (kid, uid)).fetchone()
    if k:
        # Randevular silinmez, yalnızca kişiyle bağlantıları kaldırılır.
        db.execute("UPDATE etkinlik SET kisi_id=NULL WHERE kisi_id=? AND kullanici_id=?", (kid, uid))
        db.execute("DELETE FROM kisi WHERE id=? AND kullanici_id=?", (kid, uid))
        db.commit()
        flash(f"{k['ad']} silindi. Randevuları listede kişisiz olarak duruyor.")
    return redirect(url_for("kisiler"))


@app.route("/duzenle/<int:eid>", methods=["GET", "POST"])
@giris_gerekli
def duzenle(eid):
    db = get_db()
    e = db.execute("SELECT * FROM etkinlik WHERE id=? AND kullanici_id=?",
                   (eid, session["uid"])).fetchone()
    if not e:
        return redirect(url_for("index"))
    if request.method == "POST":
        f = request.form
        baslik, tarih, tekrar = f.get("baslik", "").strip(), f.get("tarih", ""), f.get("tekrar", "yok")
        if baslik and tarih and tekrar in TEKRARLAR:
            hatirlat = f.get("hatirlat", "")
            db.execute(
                "UPDATE etkinlik SET baslik=?, tarih=?, saat=?, not_metni=?, tekrar=?, bitis=?, hatirlat=?, kisi_id=? "
                "WHERE id=? AND kullanici_id=?",
                (baslik, tarih, f.get("saat") or None, f.get("not_metni", "").strip() or None,
                 tekrar, f.get("bitis") or None,
                 int(hatirlat) if hatirlat.isdigit() else None,
                 kisi_id_al(f.get("kisi_id")), eid, session["uid"]))
            db.execute("DELETE FROM gonderilen WHERE etkinlik_id=?", (eid,))  # yeni zamana göre tekrar hatırlat
            db.commit()
            cakisma_uyar(eid, tarih, f.get("saat"))
            kid = kisi_id_al(f.get("kisi_id"))
            return redirect(url_for("kisi_detay", kid=kid) if kid else url_for("index"))
    return render_template("duzenle.html", e=e, kisiler=kisileri_al())


# ---------- Arka planda e-posta hatırlatıcısı ----------
def eposta_hazir():
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))


def yaklasanlari_gonder():
    """Hatırlatma zamanı gelen randevular için kişiye e-posta yollar (her tarih için bir kez)."""
    if not eposta_hazir():
        return
    simdi = datetime.now()
    bugun = simdi.date()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        satirlar = db.execute(
            "SELECT e.*, k.ad AS kisi_ad, k.eposta AS kisi_eposta FROM etkinlik e "
            "JOIN kisi k ON k.id = e.kisi_id "
            "WHERE k.eposta IS NOT NULL AND e.saat IS NOT NULL AND e.hatirlat IS NOT NULL "
            "AND e.tarih <= ?", ((bugun + timedelta(days=2)).isoformat(),)).fetchall()
        for e in satirlar:
            for d in olusumlar(e, bugun, bugun + timedelta(days=2)):
                zaman = datetime.fromisoformat(f"{d.isoformat()}T{e['saat']}")
                if not (zaman - timedelta(minutes=e["hatirlat"]) <= simdi <= zaman + timedelta(minutes=5)):
                    continue
                anahtar = (e["id"], d.isoformat())
                if (db.execute("SELECT 1 FROM tamam WHERE etkinlik_id=? AND tarih=?", anahtar).fetchone()
                        or db.execute("SELECT 1 FROM gonderilen WHERE etkinlik_id=? AND tarih=?", anahtar).fetchone()):
                    continue
                metin = (f"Merhaba {e['kisi_ad']},\n\n{gun_etiketi(d)[1]} saat {e['saat']} için "
                         f"\"{e['baslik']}\" randevunuz yaklaşıyor.\n")
                if e["not_metni"]:
                    metin += f"\nNot: {e['not_metni']}\n"
                metin += "\nBu e-posta randevu takip uygulaması tarafından otomatik gönderilmiştir."
                sonuc = eposta_gonder(e["kisi_eposta"], f"Randevu hatırlatması: {e['baslik']}", metin)
                print(f"[Hatırlatıcı] {e['kisi_ad']}: {sonuc}")
                if sonuc.startswith("E-posta gönderildi"):
                    db.execute("INSERT INTO gonderilen VALUES (?, ?)", anahtar)
                    db.commit()
    finally:
        db.close()


def hatirlatici():
    print("[Hatırlatıcı] " + ("açık: yaklaşan randevular için kişilere e-posta gidecek."
                              if eposta_hazir() else "kapalı: SMTP_USER ve SMTP_PASS ayarlanmamış."))
    while True:
        try:
            yaklasanlari_gonder()
        except Exception as hata:
            print("[Hatırlatıcı] hata:", hata)
        time.sleep(60)


init_db()
if __name__ == "__main__":
    # Debug modunda sunucu iki süreç açar; hatırlatıcı yalnızca asıl süreçte çalışmalı.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=hatirlatici, daemon=True).start()
    app.run(debug=True)
