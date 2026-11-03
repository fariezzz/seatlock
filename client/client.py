"""Client CLI Seatlock + uji beban. Cadangan demo jika web bermasalah.

Jalankan dari folder bioskop-rpc, server sudah aktif:
    python client/client.py jadwal
    python client/client.py lihat --jadwal J1
    python client/client.py pesan A3 --nama Budi --jadwal J1
    python client/client.py batal A3 --nama Budi --kode ABC123 --jadwal J1
    python client/client.py pantau --jadwal J1
    python client/client.py beban --n 5
    python client/client.py race --n 20 --kursi A1 --jadwal J2

Timeout dan retry: retry HANYA untuk DEADLINE_EXCEEDED / UNAVAILABLE dan
SELALU membawa request_id yang sama, supaya tidak terjadi pemesanan ganda.
Backoff: 0,5 detik, 1 detik, lalu 2 detik.
"""

import argparse
import concurrent.futures
import sys
import os
import time
import uuid

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))

import bioskop_pb2 as pb
import bioskop_pb2_grpc as pb_grpc


def buat_stub(target):
    channel = grpc.insecure_channel(target)
    return channel, pb_grpc.BioskopStub(channel)


def pesan_dengan_retry(stub, jadwal_id, kursi_id, nama, request_id=None,
                       maks=3, timeout=2.0):
    rid = request_id or str(uuid.uuid4())
    for percobaan in range(1, maks + 1):
        try:
            return stub.PesanKursi(pb.PesanRequest(
                jadwal_id=jadwal_id, kursi_id=kursi_id, nama=nama,
                request_id=rid), timeout=timeout)
        except grpc.RpcError as e:
            bisa_ulang = e.code() in (grpc.StatusCode.DEADLINE_EXCEEDED,
                                      grpc.StatusCode.UNAVAILABLE)
            if not bisa_ulang or percobaan == maks:
                raise
            jeda = 0.5 * (2 ** (percobaan - 1))
            print(f"  [retry] percobaan {percobaan} gagal ({e.code().name}), "
                  f"ulang dalam {jeda} detik...")
            time.sleep(jeda)


def tampilkan_kursi(daftar):
    for row in ("A", "B"):
        baris = [k for k in daftar if k.id.startswith(row)]
        teks = " ".join(
            f"{k.id}[{'X' if k.status == 'terpesan' else ' '}]" for k in baris)
        print(f"  {teks}")
    for k in daftar:
        if k.status == "terpesan":
            print(f"    {k.id} -> {k.pemesan}")


def cmd_jadwal(stub, _args):
    res = stub.DaftarJadwal(pb.DaftarJadwalRequest(), timeout=2.0)
    for j in res.jadwal:
        print(f"  {j.id}: {j.film} — {j.waktu}, {j.studio}")


def cmd_lihat(stub, args):
    res = stub.LihatKursi(pb.LihatRequest(jadwal_id=args.jadwal), timeout=2.0)
    print(f"Jadwal: {res.jadwal_id}")
    tampilkan_kursi(res.kursi)


def cmd_pesan(stub, args):
    res = pesan_dengan_retry(stub, args.jadwal, args.kursi, args.nama,
                             request_id=args.request_id)
    print(res.pesan)
    if res.kode_booking:
        print(f"Kode booking Anda: {res.kode_booking} "
              f"(simpan kode ini untuk membatalkan)")


def cmd_batal(stub, args):
    rid = args.request_id or str(uuid.uuid4())
    res = stub.BatalkanPesanan(pb.BatalRequest(
        jadwal_id=args.jadwal, kursi_id=args.kursi, nama=args.nama,
        request_id=rid, kode_booking=args.kode), timeout=2.0)
    print(res.pesan)


def cmd_pantau(stub, args):
    print(f"Memantau status kursi jadwal {args.jadwal} "
          f"(Ctrl+C untuk berhenti)...", flush=True)
    try:
        for update in stub.StreamingStatusKursi(
                pb.StatusRequest(jadwal_id=args.jadwal)):
            print(f"\n--- {update.jadwal_id} versi {update.versi} ---", flush=True)
            tampilkan_kursi(update.kursi)
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nPemantauan dihentikan.", flush=True)


def _satu_lihat(args):
    target, jadwal = args
    channel, stub = buat_stub(target)
    t0 = time.perf_counter()
    stub.LihatKursi(pb.LihatRequest(jadwal_id=jadwal), timeout=5.0)
    return (time.perf_counter() - t0) * 1000


def cmd_beban(stub, args):
    for n in ([args.n] if args.n else [1, 5, 20]):
        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
            lat = list(ex.map(_satu_lihat,
                              [(args.target, args.jadwal)] * 200))
        durasi = time.perf_counter() - t0
        print(f"client={n}: 200 request dalam {durasi:.3f} detik, "
              f"throughput={200 / durasi:.2f} req/s, "
              f"latensi rata-rata={sum(lat) / len(lat):.2f} ms")


def cmd_race(stub, args):
    def satu(i):
        ch, st = buat_stub(args.target)
        try:
            res = pesan_dengan_retry(st, args.jadwal, args.kursi,
                                     f"Client-{i}",
                                     request_id=f"race-{args.jadwal}-{i}",
                                     maks=1)
            return res.sukses
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as ex:
        hasil = list(ex.map(satu, range(args.n)))
    berhasil = sum(1 for h in hasil if h is True)
    print(f"Race {args.n} client memesan {args.kursi} (jadwal {args.jadwal}) "
          f"bersamaan: berhasil={berhasil}, ditolak/gagal={args.n - berhasil} "
          f"(yang benar: berhasil tepat 1)")


def tambah_jadwal(p):
    p.add_argument("--jadwal", default="J1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="localhost:50051")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("jadwal")
    p = sub.add_parser("lihat"); tambah_jadwal(p)
    p = sub.add_parser("pesan")
    p.add_argument("kursi"); p.add_argument("--nama", required=True)
    p.add_argument("--request-id"); tambah_jadwal(p)
    p = sub.add_parser("batal")
    p.add_argument("kursi"); p.add_argument("--nama", required=True)
    p.add_argument("--kode", required=True)
    p.add_argument("--request-id"); tambah_jadwal(p)
    p = sub.add_parser("pantau"); tambah_jadwal(p)
    p = sub.add_parser("beban")
    p.add_argument("--n", type=int, default=None); tambah_jadwal(p)
    p = sub.add_parser("race")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--kursi", default="A1"); tambah_jadwal(p)

    args = parser.parse_args()
    channel, stub = buat_stub(args.target)
    try:
        {"jadwal": cmd_jadwal, "lihat": cmd_lihat, "pesan": cmd_pesan,
         "batal": cmd_batal, "pantau": cmd_pantau, "beban": cmd_beban,
         "race": cmd_race}[args.cmd](stub, args)
    finally:
        channel.close()


if __name__ == "__main__":
    main()
