# Seatlock — Proyek Mini RPC: Reservasi Tiket Bioskop

Studi kasus 1 dari modul: beberapa client memesan kursi yang sama secara
bersamaan. Dibangun dengan gRPC, plus tampilan web tipis lewat gateway
HTTP → gRPC. Web hanya perantara; seluruh logika (lock, deduplikasi,
streaming) tetap di server gRPC.

Kelompok:
- Muhammad Fariez Riziq Ilham — 247006111146
- Achmad Adil Arasy Darmawan — 247006111128
- Fadhli Fajrial Habibie — 247006111142

## Arsitektur

```
Browser ──HTTP──> Gateway (web/gateway.py, pustaka standar saja)
                       │ gRPC
                       v
                  Server gRPC (server/server.py, port 50051)
                       │ gRPC (juga dipakai langsung)
                       v
                  Client CLI (client/client.py) + uji beban
```

Server menyimpan 20 kursi (A1–A10, B1–B10) di memori, dilindungi satu
`threading.Lock`. Operasi pesan/batal yang tidak idempoten dilindungi
`request_id` yang dicatat di tabel `proses` (semantik at-most-once,
seperti `PROSES` di Praktikum 3). Restart server menghilangkan state —
batasan ini ditulis jujur di laporan; perbaikannya adalah menyimpan state
dan tabel deduplikasi di database dengan transaksi atomik.

## Antarmuka (.proto)

`proto/bioskop.proto` — 4 RPC, 1 di antaranya streaming:

| RPC | Jenis | Fungsi |
|---|---|---|
| `PesanKursi` | Unary | Pesan satu kursi, idempoten lewat `request_id` |
| `BatalkanPesanan` | Unary | Batalkan pesanan sendiri |
| `LihatKursi` | Unary | Daftar seluruh kursi dan statusnya |
| `StreamingStatusKursi` | Server streaming | Snapshot status kursi setiap ada perubahan |

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
python client/client.py lihat
python client/client.py pesan A3 --nama Budi
python client/client.py batal A3 --nama Budi
python client/client.py pantau
```

Untuk client dari mesin kedua, ganti targetnya, misal:

```powershell
python client/client.py --target 192.168.1.10:50051 lihat
```

## Pengujian

Uji race — 20 client memesan kursi yang sama bersamaan (yang benar:
tepat 1 berhasil):

```powershell
python client/client.py race --n 20 --kursi A1
```

Uji kinerja — `LihatKursi` untuk 1, 5, dan 20 client bersamaan
(masing-masing 200 request):

```powershell
python client/client.py beban
```

### Hasil uji (diukur di VM pengembang, lewat soket lokal)

| Client bersamaan | Throughput | Latensi rata-rata |
|---|---|---|
| 1 | 501,09 req/s | 1,79 ms |
| 5 | 1.034,15 req/s | 4,57 ms |
| 20 | 1.367,43 req/s | 11,92 ms |

Uji race: 20 client memesan A1 bersamaan → berhasil tepat 1, 19 ditolak.
Angka di atas akan berbeda di mesin presentasi; ukur ulang dengan
`beban` dan `race` di mesin demo dan pakai angka hasil ukur ulang itu di
laporan, bukan angka tabel ini.

Catatan pengukuran: throughput naik seiring jumlah client karena server
melayani paralel (max_workers 20), sedangkan latensi per request ikut
naik karena antrean dan contention lock — ini normal dan harus
dijelaskan begitu di analisis laporan, bukan disembunyikan.

## Aturan penting yang tidak boleh dilanggar

1. `request_id` dibuat SEKALI per aksi dan diteruskan utuh dari browser
   → gateway → server gRPC. Jangan dibuat ulang di tiap lapisan, karena
   retry dengan ID baru akan tercatat sebagai pesanan baru.
2. Timeout gateway (5 detik) lebih panjang dari timeout gRPC (2 detik),
   supaya gateway tidak timeout duluan saat gRPC masih bekerja.
3. Uji beban diukur lewat client gRPC langsung, bukan lewat klik browser,
   supaya yang terukur adalah latensi RPC, bukan latensi web + RPC.

## Demo 2 mesin (naskah video, maks. 5 menit)

1. Mesin 1 menjalankan server dan gateway; mesin 2 membuka web dan
   memesan satu kursi → peta berubah live.
2. Terminal mesin 2 menjalankan `race --n 20 --kursi A1` → hanya 1
   berhasil; peta web ikut berubah live.
3. Terminal menjalankan `beban` → tunjukkan tabel throughput/latensi
   untuk 1, 5, dan 20 client.

## Pembagian tugas (usulan, sesuaikan dengan commit Git masing-masing)

- Server + proto + lock/dedup: satu anggota.
- Client CLI + uji beban + uji race: satu anggota.
- Gateway + halaman web + laporan + diagram + video demo: satu anggota.

Setiap anggota wajib punya riwayat commit sendiri di repositori Git,
sesuai ketentuan modul.

## Pengumpulan

Zip `Kelompok<no>_ProyekRPC.zip` berisi laporan PDF dan seluruh kode
sumber di folder ini (kecuali `.venv/` dan `__pycache__/`). Laporan
mencantumkan tautan repositori Git dan tautan video demo, deskripsi
studi kasus dan diagram arsitektur, isi `.proto` beserta penjelasan
tiap RPC, strategi concurrency dan toleransi kegagalan, hasil pengujian
kinerja dan analisisnya, serta pembagian tugas anggota.
