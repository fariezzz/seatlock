"""Client CLI bioskop + uji beban. Ini juga cadangan demo jika web bermasalah.

Jalankan dari folder bioskop-rpc, server sudah aktif di localhost:50051:
    python client/client.py lihat
    python client/client.py pesan A3 --nama Budi
    python client/client.py batal A3 --nama Budi
    python client/client.py pantau
    python client/client.py beban --n 5
    python client/client.py race --n 20 --kursi A1

Timeout dan retry: retry HANYA untuk DEADLINE_EXCEEDED / UNAVAILABLE dan
SELALU membawa request_id yang sama, supaya tidak terjadi pemesanan ganda
(seperti L3.2 di Praktikum 3). Backoff: 0,5 detik, 1 detik, lalu 2 detik.
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


def pesan_dengan_retry(stub, kursi_id, nama, request_id=None, maks=3,
                       timeout=2.0):
    rid = request_id or str(uuid.uuid4())
    for percobaan in range(1, maks + 1):
        try:
            return stub.PesanKursi(pb.PesanRequest(
                kursi_id=kursi_id, nama=nama, request_id=rid),
                timeout=timeout)
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


def cmd_lihat(stub, _args):
    res = stub.LihatKursi(pb.LihatRequest(), timeout=2.0)
    tampilkan_kursi(res.kursi)


def cmd_pesan(stub, args):
    res = pesan_dengan_retry(stub, args.kursi, args.nama,
                             request_id=args.request_id)
    print(res.pesan)


def cmd_batal(stub, args):
    rid = args.request_id or str(uuid.uuid4())
    res = stub.BatalkanPesanan(pb.BatalRequest(
        kursi_id=args.kursi, nama=args.nama, request_id=rid), timeout=2.0)
    print(res.pesan)


def cmd_pantau(stub, _args):
    print("Memantau status kursi (Ctrl+C untuk berhenti)...", flush=True)
    try:
        for update in stub.StreamingStatusKursi(pb.StatusRequest()):
            print(f"\n--- versi {update.versi} ---", flush=True)
            tampilkan_kursi(update.kursi)
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nPemantauan dihentikan.", flush=True)


def _satu_lihat(args):
    target, _ = args
    channel, stub = buat_stub(target)
    t0 = time.perf_counter()
    stub.LihatKursi(pb.LihatRequest(), timeout=5.0)
    return (time.perf_counter() - t0) * 1000


def cmd_beban(stub, args):
    # Uji kinerja LihatKursi (baca, aman diulang) untuk N client bersamaan,
    # sesuai ketentuan modul: 1, 5, dan 20 client. Jalankan satu per satu
    # untuk tiap N, atau satu N saja dengan --n.
    for n in ([args.n] if args.n else [1, 5, 20]):
        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
            lat = list(ex.map(_satu_lihat, [(args.target, None)] * 200))
        durasi = time.perf_counter() - t0
        print(f"client={n}: 200 request dalam {durasi:.3f} detik, "
              f"throughput={200 / durasi:.2f} req/s, "
              f"latensi rata-rata={sum(lat) / len(lat):.2f} ms")


def cmd_race(stub, args):
    # Uji race: N client memesan kursi yang SAMA bersamaan.
    # Yang benar: tepat 1 berhasil, sisanya ditolak (lock bekerja).
    def satu(i):
        ch, st = buat_stub(args.target)
        try:
            res = pesan_dengan_retry(st, args.kursi, f"Client-{i}",
                                     request_id=f"race-{i}", maks=1)
            return res.sukses
        except Exception as e:
            return f"error: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as ex:
        hasil = list(ex.map(satu, range(args.n)))
    berhasil = sum(1 for h in hasil if h is True)
    print(f"Race {args.n} client memesan {args.kursi} bersamaan: "
          f"berhasil={berhasil}, ditolak/gagal={args.n - berhasil} "
          f"(yang benar: berhasil tepat 1)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="localhost:50051")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("lihat")
    p = sub.add_parser("pesan")
    p.add_argument("kursi")
    p.add_argument("--nama", required=True)
    p.add_argument("--request-id")
    p = sub.add_parser("batal")
    p.add_argument("kursi")
    p.add_argument("--nama", required=True)
    p.add_argument("--request-id")
    sub.add_parser("pantau")
    p = sub.add_parser("beban")
    p.add_argument("--n", type=int, default=None,
                   help="satu nilai N saja; kosongkan untuk 1, 5, dan 20")
    p = sub.add_parser("race")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--kursi", default="A1")

    args = parser.parse_args()
    channel, stub = buat_stub(args.target)
    try:
        {"lihat": cmd_lihat, "pesan": cmd_pesan, "batal": cmd_batal,
         "pantau": cmd_pantau, "beban": cmd_beban, "race": cmd_race}[args.cmd](
            stub, args)
    finally:
        channel.close()


if __name__ == "__main__":
    main()
