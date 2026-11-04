# Seatlock: Rancang Bangun Sistem Reservasi Tiket Bioskop Berbasis gRPC

Seatlock adalah aplikasi web reservasi tiket bioskop berbasis gRPC.
Tampilannya dibuat seperti web publik yang layak digunakan: pilih film
dan jadwal, pilih kursi di peta, terima kode booking, dan batalkan
dengan kode itu. Di balik tampilannya, seluruh logika tetap di server
gRPC. Web hanya perantara lewat gateway HTTP ke gRPC, dan client CLI
memanggil server gRPC secara langsung tanpa melalui gateway.

Kelompok:
- Muhammad Fariez Riziq Ilham - 247006111146
- Achmad Adil Arasy Darmawan - 247006111128
- Fadhli Fajrial Habibie - 247006111142

Repositori: https://github.com/fariezzz/seatlock

## Arsitektur

Ada dua client yang berbeda, dan satu server yang sama:

```
Browser (web/index.html)
   |  REST (HTTP)
   v
Gateway (web/gateway.py, HTTP port 8000, REST ke gRPC, SSE untuk live)
   |  gRPC
   v
Server gRPC (server/server.py, port 50051, Lock, dedup, SQLite)
   |  SQL (transaksi, di bawah lock yang sama)
   v
SQLite (server/seatlock.db: tabel jadwal, kursi, proses)

Client CLI (client/client.py: jadwal, lihat, pesan, batal, pantau, beban, race)
   |  gRPC langsung ke Server gRPC, tanpa lewat gateway
   v
Server gRPC (server yang sama dengan jalur web di atas)
```

Poin yang sering salah dibaca, jadi ditegaskan di sini:

- Browser tidak memanggil gRPC mentah secara langsung. Browser
  memanggil gateway lewat REST, lalu gateway yang memanggil server
  lewat gRPC. Gateway adalah jembatan HTTP ke gRPC yang paling ringan
  untuk proyek ini, karena browser tidak punya dukungan gRPC mentah
  bawaan, sedangkan alternatif seperti grpc-web plus proxy Envoy
  menambah satu komponen yang harus dikonfigurasi dan didemo.
- Client CLI tidak memanggil gateway. Gateway adalah server HTTP,
  bukan server gRPC, jadi ia tidak bisa menerima panggilan gRPC dari
  CLI. CLI memanggil server gRPC secara langsung.
- Hanya server yang membaca dan menulis SQLite. CLI tidak pernah
  menyentuh berkas database. CLI hanya mengirim RPC ke server, lalu
  server yang melakukan baca dan tulis di bawah lock, di dalam
  transaksi SQLite.
- SQLite bukan layanan terpisah. Ia adalah satu berkas lokal di mesin
  server saja (`server/seatlock.db`), dibuat otomatis saat server
  pertama dijalankan. Gateway dan client tidak menyentuh berkas itu.

Alur pemanggilan RPC, ringkasnya:

1. Browser mengirim REST ke gateway.
2. Gateway menerjemahkan REST menjadi pemanggilan gRPC ke server.
3. Server mengunci state, memeriksa deduplikasi, memvalidasi input,
   memeriksa status kursi, mengubah status kursi, menaikkan versi
   streaming, mencatat hasil ke tabel deduplikasi, lalu mengembalikan
   hasil.
4. Gateway mengembalikan hasil itu ke browser sebagai JSON, atau
   sebagai event SSE untuk streaming.

Untuk CLI, langkah 1 dan 2 dilewati: CLI langsung memanggil server
gRPC, sehingga uji beban dan uji race mengukur latensi RPC murni,
bukan latensi web ditambah RPC.

## Tampilan web

Web terdiri dari header Seatlock, kartu film dengan persentase
okupansi, peta kursi dengan layar di atasnya, panel ringkasan
pesanan, dan kotak kode booking yang menonjol.

Di balik tampilan itu, web tetap satu halaman HTML tanpa framework
JavaScript dan tanpa build step, dan gateway hanya memakai pustaka
standar Python ditambah grpcio. Tidak ada halaman admin untuk
menambah jadwal di versi ini. Itu disengaja, supaya cakupan tetap
sederhana dan mudah didemo.

## Jadwal tayang preset

| Kode | Film | Waktu | Studio |
|---|---|---|---|
| J1 | Petualangan Langit | 14:00 | Studio 1 |
| J2 | Kota Senja | 17:00 | Studio 1 |
| J3 | Malam Bintang | 20:00 | Studio 2 |

Tiap jadwal punya peta kursi sendiri: 20 kursi (A1-A10, B1-B10).
Menambah jadwal berarti menambah satu baris preset di server, lalu
restart server. Database yang sudah ada tidak di-seed ulang.

Server menyimpan state di SQLite, bukan lagi di memori. Restart
server TIDAK menghilangkan pesanan maupun tabel deduplikasi. Berkas
database (`seatlock.db`, plus `-wal`/`-shm` saat berjalan) sengaja
TIDAK di-commit ke Git. Tiap mesin membuat databasenya sendiri saat
pertama dijalankan, lalu di-seed dengan jadwal preset di atas.

### Reset database

Hentikan dulu server (Ctrl+C), baru hapus berkasnya, lalu jalankan
lagi server. Menghapus hanya `seatlock.db` saja bisa membuat data
lama muncul lagi dari WAL yang tertinggal, dan menghapus selagi
server berjalan bisa tidak berpengaruh atau membuat database tidak
konsisten.

```powershell
Remove-Item server\seatlock.db*
```

Saat restart, server membuat database baru, mengisi 3 jadwal kali
20 kursi kosong, dan tabel deduplikasi ikut bersih, sehingga
`request_id` lama bisa dipakai lagi. Berguna sebelum demo atau
sebelum merekam video demo.

## Antarmuka (.proto)

`proto/bioskop.proto` - 5 RPC, 1 di antaranya streaming:

| RPC | Jenis | Fungsi |
|---|---|---|
| `DaftarJadwal` | Unary | Daftar jadwal tayang preset |
| `PesanKursi` | Unary | Pesan satu kursi pada satu jadwal, idempoten lewat `request_id`, mengembalikan `kode_booking` |
| `BatalkanPesanan` | Unary | Batalkan pesanan sendiri, wajib nama plus `kode_booking` yang cocok |
| `LihatKursi` | Unary | Daftar kursi satu jadwal beserta statusnya |
| `StreamingStatusKursi` | Server streaming | Snapshot status kursi satu jadwal setiap ada perubahan, dengan nomor versi |

Keamanan kode booking: kode HANYA dikembalikan di `PesanResponse`
kepada pemesan yang berhasil. `LihatKursi` dan
`StreamingStatusKursi` tidak pernah membawa kode, supaya kode tidak
bisa dicuri dari peta kursi. Pesan error pembatalan juga sengaja
tidak membedakan "nama salah" vs "kode salah", supaya kode tidak
bisa ditebak lewat pesan error.

Nomor versi streaming disimpan di memori server, di bawah lock yang
sama dengan perubahan kursi. Versi ini hanya penanda perubahan,
bukan data. Versi kembali ke awal saat restart, dan itu tidak
menghilangkan data apa pun.

## Instalasi (Windows, sama seperti praktikum)

```powershell
python --version   # minimal 3.10
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install grpcio grpcio-tools
```

## Generate stub

Dari folder proyek:

```powershell
python -m grpc_tools.protoc -Iproto --python_out=proto --grpc_python_out=proto proto/bioskop.proto
```

Hasilnya `proto/bioskop_pb2.py` dan `proto/bioskop_pb2_grpc.py`
(sudah ikut disertakan di repo, jadi langkah ini boleh dilewati
saat demo).

## Menjalankan

Terminal 1 - server gRPC (pakai `0.0.0.0` agar bisa diakses mesin
kedua):

```powershell
python server/server.py --host 0.0.0.0 --port 50051
```

Terminal 2 - gateway web:

```powershell
python web/gateway.py --grpc localhost:50051 --host 0.0.0.0 --port 8000
```

Lalu buka `http://localhost:8000/` (atau IP mesin gateway dari
mesin kedua).

Terminal 3 - client CLI (cadangan demo jika web bermasalah):

```powershell
python client/client.py jadwal
python client/client.py lihat --jadwal J1
python client/client.py pesan A3 --nama Budi --jadwal J1
python client/client.py batal A3 --nama Budi --kode ABC123 --jadwal J1
python client/client.py pantau --jadwal J1
```

Untuk client dari mesin kedua, ganti targetnya, misal:

```powershell
python client/client.py --target 192.168.1.10:50051 lihat --jadwal J1
```

## Aturan penting yang tidak boleh dilanggar

1. `request_id` dibuat SEKALI per aksi dan diteruskan utuh dari
   browser ke gateway ke server gRPC. Jangan dibuat ulang di tiap
   lapisan, karena retry dengan ID baru akan tercatat sebagai
   pesanan baru. Pengecualian yang disengaja: web membuat
   `request_id` baru di tiap klik, jadi web tidak punya tombol
   retry otomatis. Retry otomatis hanya ada di CLI.
2. Timeout gateway (5 detik) lebih panjang dari timeout gRPC CLI
   (2 detik), supaya gateway tidak timeout duluan saat gRPC masih
   bekerja.
3. Uji beban diukur lewat client gRPC langsung, bukan lewat klik
   browser, supaya yang terukur adalah latensi RPC, bukan latensi
   web plus RPC.
4. Jangan pernah menampilkan `kode_booking` di peta kursi atau di
   streaming. Kode hanya untuk pemesan yang berhasil.
5. Server menolak input tidak valid (nama, kursi, atau jadwal kosong
   atau tidak ada) sebelum memproses, dan penolakan itu tidak
   dicatat di tabel proses, sehingga input boleh diperbaiki lalu
   di-retry dengan `request_id` yang sama.
6. Jangan menghapus database untuk uji kegagalan dan pemulihan.
   Uji itu justru membuktikan data bertahan melewati restart.

## Pengujian

Bagian ini mencakup semua pengujian, bukan hanya pengujian yang
ditambahkan belakangan. Sebelum tiap pengujian, pastikan pengujian
berjalan di kursi yang masih kosong, atau reset database dulu
dengan cara di atas. Kalau tidak, hasil pengujian bisa tercampur
dengan pesanan dari pengujian sebelumnya.

### 1. Uji fungsional

Perintah dasar yang harus berhasil, di luar pengujian khusus:

```powershell
python client/client.py jadwal
python client/client.py lihat --jadwal J1
python client/client.py pesan A3 --nama Budi --jadwal J1
python client/client.py batal A3 --nama Budi --kode ABC123 --jadwal J1
```

(Ganti `ABC123` dengan kode yang benar-benar dikembalikan
`pesan` sebelumnya.)

Yang diperiksa:

- `jadwal` menampilkan J1, J2, dan J3 dengan film, waktu, dan
  studionya.
- `lihat` menampilkan 20 kursi per jadwal, terurut A1-A10 lalu
  B1-B10, dengan status dan nama pemesan yang benar.
- `pesan` yang berhasil mengembalikan kode booking 6 karakter.
- `batal` dengan kode yang benar mengosongkan kursi lagi.
- `lihat` tidak pernah menampilkan kode booking, sebelum maupun
  sesudah pemesanan.

### 2. Uji informasi kursi

Satu rangkaian terminal yang membuktikan tiga kondisi kursi:

```powershell
python client/client.py lihat --jadwal J1
python client/client.py pesan A7 --nama Budi --jadwal J1 --request-id info-1
python client/client.py lihat --jadwal J1
python client/client.py batal A7 --nama Budi --kode CDU5H7 --jadwal J1 --request-id info-2
python client/client.py lihat --jadwal J1
```

Yang diperiksa: `lihat` pertama menunjukkan A7 kosong, `lihat`
kedua menunjukkan A7 terpesan atas nama Budi, dan `lihat` ketiga
menunjukkan A7 kosong lagi. Kode booking tidak muncul di output
`lihat` mana pun. (Ganti `CDU5H7` dengan kode yang benar-benar
dikembalikan pada perintah `pesan` di rangkaian itu.)

### 3. Uji konflik kursi

Pesan kursi yang sama dengan `request_id` yang berbeda, dari dua
nama yang berbeda. Yang pertama berhasil, yang kedua ditolak
karena kursi sudah terpesan. Ini berbeda dengan deduplikasi:
konflik memakai ID berbeda dan ditolak, sedangkan deduplikasi
memakai ID yang sama dan mengembalikan hasil lama.

### 4. Uji deduplikasi

Deduplikasi HARUS memakai `request_id` yang sama secara eksplisit.
Kalau tidak ditulis, CLI membuat UUID baru di tiap perintah, dan
itu bukan pengujian deduplikasi.

```powershell
python client/client.py pesan A5 --nama Budi --jadwal J1 --request-id dedup-002
python client/client.py pesan B5 --nama Ani --jadwal J1 --request-id dedup-002
python client/client.py lihat --jadwal J1
```

Yang diperiksa: perintah kedua mengembalikan hasil perintah
pertama (A5 atas nama Budi, dengan kode booking yang sama),
bukan pesanan B5 atas nama Ani. `lihat` harus menunjukkan A5
terpesan atas nama Budi dan B5 tetap kosong.

Perilaku pencatatan yang perlu dipahami:

- Penolakan karena state (kursi sudah terpesan, kursi sudah
  kosong) DICATAT di tabel proses, sehingga retry dengan ID yang
  sama mengembalikan penolakan lama itu.
- Penolakan validasi (nama, kursi, atau jadwal kosong atau tidak
  ada) TIDAK dicatat, sehingga input boleh diperbaiki lalu
  di-retry dengan ID yang sama dan berhasil.

### 5. Uji race (konkurensi)

20 client memesan kursi yang sama bersamaan pada satu jadwal:

```powershell
python client/client.py race --n 20 --kursi A1 --jadwal J2
```

Yang benar: tepat 1 berhasil, 19 ditolak. Perintah `race`
sengaja tidak memakai retry otomatis (maksimal 1 percobaan),
supaya retry tidak menyamarkan hasil race.

Uji ini diulang 10 kali, tiap kali pada kursi segar A1-A10 di
jadwal J2. Tiap pengulangan harus tepat 1 berhasil dan 19
ditolak. Inilah bukti empiris bahwa satu lock di server plus
transaksi SQLite mencegah kursi yang sama terpesan dua kali.

### 6. Uji streaming

Jalankan dua `pantau` pada jadwal yang sama, lalu pesan satu
kursi dari terminal ketiga:

```powershell
python client/client.py pantau --jadwal J1
python client/client.py pesan A3 --nama Budi --jadwal J1
```

Yang diperiksa:

- Kedua pantau menerima snapshot dengan nomor versi yang sama
  setelah perubahan.
- Snapshot pertama setelah putus lalu sambung lagi sudah benar,
  tanpa harus menunggu perubahan berikutnya.
- Jalur web yang sama juga teruji: gateway meneruskan streaming
  gRPC sebagai SSE ke browser lewat `/api/stream?jadwal=`, dan
  peta web ikut berubah live tanpa refresh.

Nomor versi hanya naik saat ada perubahan status kursi pada
jadwal itu, jadi pantau yang tidak berubah tidak menerima
snapshot duplikat yang tidak perlu.

### 7. Uji kegagalan dan pemulihan

```powershell
python client/client.py pesan A3 --nama Budi --jadwal J1 --request-id pulih-1
```

Lalu hentikan server (Ctrl+C) selagi `pantau` berjalan di
terminal lain, dan jalankan `lihat`:

- `pantau` harus keluar dengan error, bukan menggantung.
- `lihat` ke server yang mati harus gagal dalam batas timeout
  (`DEADLINE_EXCEEDED` untuk perintah biasa, `UNAVAILABLE` untuk
  stream yang sedang berjalan), bukan menggantung tanpa batas.

Jalankan lagi server TANPA menghapus database, lalu:

- `lihat` berhasil dan pesanan tadi masih ada. Ini bukti
  persistensi SQLite.
- Pesan kursi lain dengan `request_id` yang sama (`pulih-1`)
  harus mengembalikan hasil dan kode booking lama, bukan pesanan
  baru. Ini bukti deduplikasi lintas restart.

### 8. Uji timeout dan retry

Hook ini KHUSUS pengujian, bukan perilaku normal. Jangan pakai
variabel ini saat demo normal.

```powershell
$env:SEATLOCK_DELAY_ONCE_SEC = "3"
python server/server.py --host 0.0.0.0 --port 50051
python client/client.py pesan A4 --nama Budi --jadwal J1
```

Cara kerja hook: pemesanan pertama yang berhasil ditunda 3 detik
SETELAH commit dan SETELAH lock dilepas, baru responsnya
dikirim. Karena timeout CLI 2 detik, percobaan pertama gagal
`DEADLINE_EXCEEDED` (terlihat baris `[retry]` di CLI), lalu retry
otomatis dengan `request_id` yang sama berhasil dengan kode
booking yang sama, dan kursi tercatat tepat satu kali.

Retry CLI (`pesan_dengan_retry`, dipakai `pesan` dan `race`):
maksimal 3 percobaan, hanya untuk `DEADLINE_EXCEEDED` atau
`UNAVAILABLE`, dengan backoff 0,5 detik lalu 1 detik, dan
`request_id` yang sama di semua percobaan. `batal` TIDAK punya
retry otomatis: timeout 2 detik langsung, tapi retry manual
dengan `--request-id` yang sama tetap aman karena deduplikasi
server. Gateway juga TIDAK me-retry: ia hanya meneruskan
`request_id` dengan timeout 5 detik.

### 9. Uji validasi

Server menolak sebelum memproses, dan penolakan ini tidak
dicatat di tabel proses (lihat bagian deduplikasi):

- Jadwal tidak ada, misal J99.
- Kursi tidak valid atau kosong.
- Nama kosong atau hanya spasi.
- Jadwal kosong.

Kode booking yang salah pada `batal` juga ditolak, dengan pesan
error yang sengaja tidak membedakan nama salah vs kode salah.

Catatan PowerShell: shell memotong argumen string kosong `""`,
sehingga `--nama ""` gagal di argparse dan tidak pernah sampai
ke server. Untuk menguji nama kosong lewat CLI, pakai satu spasi
`" "`, karena server memeriksa setelah `strip()` dan menolaknya
sebagai kosong.

### 10. Uji kinerja (beban)

`LihatKursi` untuk 1, 5, dan 20 client bersamaan, masing-masing
200 request:

```powershell
python client/client.py beban --jadwal J1
```

Perintah `beban` memakai timeout 5 detik per panggilan
`LihatKursi`, berbeda dengan timeout 2 detik pada perintah CLI
biasa, supaya panggilan tidak timeout duluan di bawah beban.
Yang diukur adalah client gRPC langsung, bukan klik browser.

#### Hasil uji kinerja terbaru

| Client bersamaan | Throughput | Latensi rata-rata |
|---|---|---|
| 1 | 233,15 req/s | 3,88 ms |
| 5 | 1039,70 req/s | 4,34 ms |
| 20 | 1203,43 req/s | 14,47 ms |

Uji race: 20 client memesan A1 pada jadwal J2 bersamaan,
berhasil tepat 1, 19 ditolak.

## Struktur repo

```
proto/
  bioskop.proto          # kontrak 5 RPC
  bioskop_pb2.py         # stub hasil generate (sudah disertakan)
  bioskop_pb2_grpc.py
server/
  server.py              # server gRPC, Lock, SQLite, dedup, hook delay uji
client/
  client.py              # CLI: jadwal, lihat, pesan, batal, pantau, beban, race
web/
  gateway.py             # gateway HTTP ke gRPC, pustaka standar plus grpcio
  index.html             # satu halaman web Seatlock, tanpa framework JS
```
