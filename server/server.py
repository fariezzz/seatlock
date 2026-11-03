"""Server gRPC reservasi tiket bioskop.

State kursi disimpan di memori (cukup untuk demo proyek mini; batasan ini
ditulis jujur di laporan, sama seperti PROSES di Praktikum 3).
Seluruh baca-ubah-tulis kursi dan pencatatan deduplikasi dilindungi satu
threading.Lock, sehingga tidak ada lost update seperti di L3.3.

Jalankan dari folder bioskop-rpc:
    python server/server.py [--port 50051] [--host 0.0.0.0]
Pakai --host 0.0.0.0 agar bisa diakses dari mesin kedua saat demo.
"""

import argparse
import threading
import time
from concurrent import futures

import grpc

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))

import bioskop_pb2 as pb
import bioskop_pb2_grpc as pb_grpc

# 20 kursi: A1-A10 dan B1-B10.
KURSI_AWAL = [f"{row}{n}" for row in ("A", "B") for n in range(1, 11)]


class BioskopServicer(pb_grpc.BioskopServicer):
    def __init__(self):
        self.lock = threading.Lock()
        # id -> {"status": "kosong"|"terpesan", "pemesan": str}
        self.kursi = {k: {"status": "kosong", "pemesan": ""} for k in KURSI_AWAL}
        # request_id -> PesanResponse (deduplikasi, semantik at-most-once)
        self.proses = {}
        self.versi = 0
        self.versi_event = threading.Event()

    def _snapshot(self):
        return [
            pb.Kursi(id=k, status=v["status"], pemesan=v["pemesan"])
            for k, v in self.kursi.items()
        ]

    def _naik_versi(self):
        self.versi += 1
        self.versi_event.set()
        self.versi_event.clear()

    def PesanKursi(self, request, context):
        with self.lock:
            # 1. Deduplikasi: retry dengan request_id sama = hasil lama.
            if request.request_id and request.request_id in self.proses:
                return self.proses[request.request_id]
            kursi = self.kursi.get(request.kursi_id)
            if kursi is None:
                hasil = pb.PesanResponse(
                    sukses=False, pesan=f"Kursi {request.kursi_id} tidak ada.")
            elif kursi["status"] == "terpesan":
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} sudah terpesan "
                          f"oleh {kursi['pemesan']}.",
                    kursi=pb.Kursi(id=request.kursi_id, status="terpesan",
                                   pemesan=kursi["pemesan"]))
            else:
                kursi["status"] = "terpesan"
                kursi["pemesan"] = request.nama
                self._naik_versi()
                hasil = pb.PesanResponse(
                    sukses=True,
                    pesan=f"Kursi {request.kursi_id} berhasil dipesan "
                          f"oleh {request.nama}.",
                    kursi=pb.Kursi(id=request.kursi_id, status="terpesan",
                                   pemesan=request.nama))
            if request.request_id:
                self.proses[request.request_id] = hasil
            return hasil

    def BatalkanPesanan(self, request, context):
        with self.lock:
            if request.request_id and request.request_id in self.proses:
                return self.proses[request.request_id]
            kursi = self.kursi.get(request.kursi_id)
            if kursi is None:
                hasil = pb.PesanResponse(
                    sukses=False, pesan=f"Kursi {request.kursi_id} tidak ada.")
            elif kursi["status"] == "kosong":
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} sudah kosong.")
            elif kursi["pemesan"] != request.nama:
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} dipesan oleh "
                          f"{kursi['pemesan']}, bukan oleh {request.nama}.")
            else:
                kursi["status"] = "kosong"
                kursi["pemesan"] = ""
                self._naik_versi()
                hasil = pb.PesanResponse(
                    sukses=True,
                    pesan=f"Pesanan kursi {request.kursi_id} dibatalkan.",
                    kursi=pb.Kursi(id=request.kursi_id, status="kosong"))
            if request.request_id:
                self.proses[request.request_id] = hasil
            return hasil

    def LihatKursi(self, request, context):
        with self.lock:
            return pb.LihatResponse(kursi=self._snapshot())

    def StreamingStatusKursi(self, request, context):
        # Kirim snapshot awal, lalu snapshot baru setiap ada perubahan.
        # Client yang berhenti (context tidak aktif) akan keluar dari loop.
        versi_terkirim = -1
        while context.is_active():
            with self.lock:
                if self.versi != versi_terkirim:
                    versi_terkirim = self.versi
                    yield pb.StatusUpdate(kursi=self._snapshot(),
                                          versi=versi_terkirim)
            # Tunggu perubahan, maksimal 1 detik, supaya context.is_active
            # tetap diperiksa berkala dan stream bisa berhenti dengan bersih.
            self.versi_event.wait(timeout=1.0)


def serve(host="0.0.0.0", port=50051, max_workers=20, unix=None):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_BioskopServicer_to_server(BioskopServicer(), server)
    server.add_insecure_port(f"{host}:{port}")
    if unix:
        server.add_insecure_port(f"unix://{unix}")
    server.start()
    print(f"Server Bioskop berjalan di {host}:{port} "
          f"(max_workers={max_workers})...")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--unix", default=None,
                        help="opsional, mis. /tmp/bioskop.sock")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, unix=args.unix)
