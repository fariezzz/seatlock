# Seatlock — Proyek Mini RPC: Reservasi Tiket Bioskop

Seatlock adalah aplikasi web reservasi tiket bioskop berbasis gRPC.
Tampilannya dibuat seperti web publik yang layak digunakan: pilih film
dan jadwal, pilih kursi di peta, terima kode booking, dan batalkan
dengan kode itu. Di balik tampilannya, seluruh logika tetap di server
gRPC — web hanya perantara lewat gateway HTTP → gRPC.

Kelompok:
- Muhammad Fariez Riziq Ilham — 247006111146
- Achmad Adil Arasy Darmawan — 247006111128
- Fadhli Fajrial Habibie — 247006111142

## Arsitektur

```
Browser (index.html, desain publik: kartu film, peta kursi, ringkasan)
   |  HTTP (REST sederhana)
   v
Web Gateway (web/gateway.py, pustaka standar saja)
   |  gRPC
   v
Server gRPC (server/server.py, port 50051)
   |  SQL (transaksi + constraint unik)
   v
SQLite (server/seatlock.db, dibuat otomatis saat pertama dijalankan)
   |  gRPC (juga dipakai langsung)
   v
Client CLI (client/client.py) + uji beban
```

Server menyimpan state di SQLite, bukan lagi di memori. Restart server
TIDAK menghilangkan pesanan maupun tabel deduplikasi. Berkas database
(`seatlock.db`, plus `-wal`/`-shm` saat berjalan) sengaja TIDAK di-commit
ke Git — tiap mesin membuat databasenya sendiri saat pertama dijalankan,
lalu di-seed dengan jadwal preset di bawah.

## Jadwal tayang preset

| Kode | Film | Waktu | Studio |
|---|---|---|---|
| J1 | Petualangan Langit | 14:00 | Studio 1 |
| J2 | Kota Senja | 17:00 | Studio 1 |
| J3 | Malam Bintang | 20:00 | Studio 2 |

Tiap jadwal punya peta kursi sendiri: 20 kursi (A1–A10, B1–B10).
Tidak ada halaman admin untuk menambah jadwal di versi ini — itu
disengaja, supaya Tier 1 Ringan tetap sederhana dan mudah didemo.
Menambah jadwal berarti menambah satu baris preset di server, lalu
restart server (database yang sudah ada tidak di-seed ulang).

## Antarmuka (.proto)

`proto/bioskop.proto` — 5 RPC, 1 di antaranya streaming:

| RPC | Jenis | Fungsi |
|---|---|---|
| `DaftarJadwal` | Unary | Daftar jadwal tayang preset |
| `PesanKursi` | Unary | Pesan satu kursi pada satu jadwal, idempoten lewat `request_id`, mengembalikan `kode_booking` |
| `BatalkanPesanan` | Unary | Batalkan pesanan sendiri, wajib nama + `kode_booking` yang cocok |
| `LihatKursi` | Unary | Daftar kursi satu jadwal beserta statusnya |
| `StreamingStatusKursi` | Server streaming | Snapshot status kursi satu jadwal setiap ada perubahan |

Keamanan kode booking: kode HANYA dikembalikan di `PesanResponse`
kepada pemesan yang berhasil. `LihatKursi` dan `StreamingStatusKursi`
tidak pernah membawa kode, supaya kode tidak bisa dicuri dari peta
kursi. Pesan error pembatalan juga sengaja tidak membedakan "nama
salah" vs "kode salah", supaya kode tidak bisa ditebak lewat pesan error.

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

Hasilnya `proto/bioskop_pb2.py` dan `proto/bioskop_pb2_grpc.py` (sudah
ikut disertakan di repo, jadi langkah ini boleh dilewati saat demo).

## Menjalankan

Terminal 1 — server gRPC (pakai `0.0.0.0` agar bisa diakses mesin kedua):

```powershell
python server/server.py --host 0.0.0.0 --port 50051
```

Terminal 2 — gateway web:

```powershell
python web/gateway.py --grpc localhost:50051 --host 0.0.0.0 --port 8000
```

Lalu buka `http://localhost:8000/` (atau IP mesin gateway dari mesin kedua).

Terminal 3 — client CLI (cadangan demo jika web bermasalah):

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

## Uji tambahan (v2.1)

Selain uji di atas, versi ini menambahkan pengujian yang diminta dari
review pengujian:

- **Uji timeout dan retry (khusus pengujian, bukan perilaku normal).**
  Jalankan server dengan hook sekali pakai, lalu pesan lewat CLI
  (timeout CLI 2 detik, delay 3 detik, retry otomatis dengan request_id
  yang sama):
  ```powershell
  $env:SEATLOCK_DELAY_ONCE_SEC = "3"
  python server/server.py --host 0.0.0.0 --port 50051
  python client/client.py pesan A4 --nama Budi --jadwal J1
  ```
  Hasil yang diharapkan: percobaan pertama gagal `DEADLINE_EXCEEDED`
  (terlihat baris `[retry]` di CLI), retry berhasil dengan kode booking
  yang sama, dan kursi tercatat tepat satu kali. Jangan pakai variabel
  ini saat demo normal.
- **Uji kegagalan dan pemulihan.** Hentikan server, jalankan `lihat`:
  client menerima `UNAVAILABLE` dalam batas timeout, tidak menggantung.
  Jalankan lagi server: `lihat` berhasil dan pesanan lama masih ada.
- **Uji streaming mendalam.** Jalankan dua `pantau` pada jadwal yang
  sama, pesan satu kursi dari terminal ketiga: kedua pantau menerima
  snapshot versi yang sama. Putuskan lalu sambungkan lagi satu pantau:
  snapshot pertamanya sudah benar.
- **Uji validasi.** Jadwal tidak ada, kursi tidak valid, nama kosong,
  kursi kosong, dan kode booking salah semuanya ditolak. Penolakan
  validasi tidak dicatat di tabel proses, sehingga retry dengan
  request_id yang sama setelah memperbaiki input berhasil; penolakan
  karena state (sudah terpesan/sudah kosong) tetap dicatat.
- **Uji race 10 pengulangan.** Ulangi `race --n 20` pada kursi segar
  A1–A10 di jadwal J2: tiap pengulangan harus tepat 1 berhasil, 19
  ditolak.

## Pengujian

Uji race — 20 client memesan kursi yang sama bersamaan pada satu jadwal
(yang benar: tepat 1 berhasil):

```powershell
python client/client.py race --n 20 --kursi A1 --jadwal J2
```

Uji kinerja — `LihatKursi` untuk 1, 5, dan 20 client bersamaan
(masing-masing 200 request):

```powershell
python client/client.py beban --jadwal J1
```

Uji persistensi — pesan kursi, hentikan server (Ctrl+C), jalankan lagi,
lalu `lihat`: pesanan harus masih ada. Uji deduplikasi lintas restart —
pesan ulang dengan `request_id` yang sama setelah restart harus
mengembalikan hasil dan kode booking yang sama, bukan pesanan baru.

### Hasil uji (diukur di mesin pengembang, lewat soket lokal)

| Client bersamaan | Throughput | Latensi rata-rata |
|---|---|---|
| 1 | 499,54 req/s | 1,79 ms |
| 5 | 786,29 req/s | 6,07 ms |
| 20 | 891,64 req/s | 20,66 ms |

Uji race: 20 client memesan A1 pada jadwal J2 bersamaan → berhasil
tepat 1, 19 ditolak.
Angka di atas akan berbeda di mesin presentasi; ukur ulang dengan
`beban` dan `race` di mesin demo dan pakai angka hasil ukur ulang itu di
laporan, bukan angka tabel ini.

Catatan pengukuran: dibanding versi in-memory sebelumnya (1.367 req/s
pada 20 client), throughput pada 20 client turun menjadi 892 req/s dan
latensi naik menjadi 20,66 ms. Itu harga yang dibayar untuk persistensi:
setiap operasi tulis kini melewati transaksi SQLite dan lock yang sama.
Trade-off ini disengaja dan dijelaskan di laporan — persistensi dinilai
lebih berharga daripada throughput mentah untuk aplikasi reservasi, dan
angka ini masih naik seiring jumlah client, bukan runtuh.

## Aturan penting yang tidak boleh dilanggar

1. `request_id` dibuat SEKALI per aksi dan diteruskan utuh dari browser
   → gateway → server gRPC. Jangan dibuat ulang di tiap lapisan, karena
   retry dengan ID baru akan tercatat sebagai pesanan baru.
2. Timeout gateway (5 detik) lebih panjang dari timeout gRPC (2 detik),
   supaya gateway tidak timeout duluan saat gRPC masih bekerja.
3. Uji beban diukur lewat client gRPC langsung, bukan lewat klik browser,
   supaya yang terukur adalah latensi RPC, bukan latensi web + RPC.
4. Jangan pernah menampilkan `kode_booking` di peta kursi atau di
   streaming. Kode hanya untuk pemesan yang berhasil.
5. Server menolak input tidak valid (nama/kursi/jadwal kosong atau tidak
   ada) sebelum memproses, dan penolakan itu tidak dicatat di tabel
   proses, sehingga input boleh diperbaiki lalu di-retry dengan
   request_id yang sama.

## Demo 2 mesin (naskah video, maks. 5 menit)

1. Mesin 1 menjalankan server dan gateway; mesin 2 membuka web Seatlock,
   memilih jadwal, memesan satu kursi, dan menerima kode booking.
2. Hentikan lalu jalankan lagi server di mesin 1; di web, pesanan tadi
   masih ada — bukti persistensi SQLite.
3. Terminal mesin 2 menjalankan `race --n 20 --kursi A1 --jadwal J2` →
   hanya 1 berhasil; peta web ikut berubah live.
4. Terminal menjalankan `beban` → tunjukkan tabel throughput/latensi
   untuk 1, 5, dan 20 client.
5. (Opsional, kalau waktu cukup) Batalkan dengan kode yang salah
   (ditolak), lalu dengan kode yang benar (berhasil).

## Pembagian tugas (usulan, sesuaikan dengan commit Git masing-masing)

- Server + proto + database SQLite: satu anggota.
- Client CLI + uji beban + uji race: satu anggota.
- Gateway + halaman web Seatlock + laporan + diagram + video demo:
  satu anggota.

Setiap anggota wajib punya riwayat commit sendiri di repositori Git,
sesuai ketentuan modul. Lihat `seatlock-patches.zip` (terpisah dari zip
ini) untuk pembagian patch per anggota.

## Pengumpulan

Zip `Kelompok<no>_ProyekRPC.zip` berisi laporan PDF dan seluruh kode
sumber di folder ini (kecuali `.venv/`, `__pycache__/`, dan
`seatlock.db*`). Laporan mencantumkan tautan repositori Git dan tautan
video demo, deskripsi studi kasus dan diagram arsitektur, isi `.proto`
beserta penjelasan tiap RPC, strategi concurrency dan toleransi
kegagalan, hasil pengujian kinerja dan analisisnya, serta pembagian
tugas anggota.
