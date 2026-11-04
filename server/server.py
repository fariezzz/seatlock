import argparse
import os
import random
import sqlite3
import string
import sys
import threading
import time
from concurrent import futures

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))

import bioskop_pb2 as pb
import bioskop_pb2_grpc as pb_grpc

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seatlock.db")
JADWAL_PRESET = [
    ("J1", "Petualangan Langit", "14:00", "Studio 1"),
    ("J2", "Kota Senja", "17:00", "Studio 1"),
    ("J3", "Malam Bintang", "20:00", "Studio 2"),
]
KURSI_AWAL = [f"{row}{n}" for row in ("A", "B") for n in range(1, 11)]


def buat_kode_booking():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class BioskopServicer(pb_grpc.BioskopServicer):
    def __init__(self, db_path=DB_PATH):
        self.lock = threading.Lock()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()
        # versi streaming per jadwal (hanya penanda perubahan, bukan data);
        # snapshot awal selalu dikirim saat client terhubung, jadi reset
        # versi saat restart tidak menghilangkan data apa pun.
        self.versi = {}
        self.versi_event = threading.Event()

    def _init_db(self):
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS jadwal (
            id TEXT PRIMARY KEY, film TEXT, waktu TEXT, studio TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS kursi (
            jadwal_id TEXT, kursi_id TEXT, status TEXT, pemesan TEXT,
            kode_booking TEXT,
            PRIMARY KEY (jadwal_id, kursi_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS proses (
            request_id TEXT PRIMARY KEY, sukses INTEGER, pesan TEXT,
            kursi_id TEXT, status TEXT, pemesan TEXT, kode_booking TEXT)""")
        for jid, film, waktu, studio in JADWAL_PRESET:
            c.execute("INSERT OR IGNORE INTO jadwal VALUES (?,?,?,?)",
                      (jid, film, waktu, studio))
            for k in KURSI_AWAL:
                c.execute(
                    "INSERT OR IGNORE INTO kursi VALUES (?,?,?,?,?)",
                    (jid, k, "kosong", "", ""))
        c.commit()

    def _jadwal_default(self):
        return JADWAL_PRESET[0][0]

    def _naik_versi(self, jadwal_id):
        self.versi[jadwal_id] = self.versi.get(jadwal_id, 0) + 1
        self.versi_event.set()
        self.versi_event.clear()

    def DaftarJadwal(self, request, context):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, film, waktu, studio FROM jadwal ORDER BY id")
            return pb.DaftarJadwalResponse(
                jadwal=[pb.Jadwal(id=r[0], film=r[1], waktu=r[2], studio=r[3])
                        for r in rows])

    def PesanKursi(self, request, context):
        jid = request.jadwal_id or self._jadwal_default()
        with self.lock:
            c = self.conn
            # 1. Deduplikasi lintas restart: tabel proses bertahan di DB.
            if request.request_id:
                row = c.execute(
                    "SELECT sukses, pesan, kursi_id, status, pemesan, "
                    "kode_booking FROM proses WHERE request_id=?",
                    (request.request_id,)).fetchone()
                if row:
                    return pb.PesanResponse(
                        sukses=bool(row[0]), pesan=row[1],
                        kursi=pb.Kursi(id=row[2], status=row[3], pemesan=row[4])
                        if row[2] else None,
                        kode_booking=row[5] or "")
            kursi = c.execute(
                "SELECT status, pemesan FROM kursi WHERE jadwal_id=? "
                "AND kursi_id=?", (jid, request.kursi_id)).fetchone()
            if kursi is None:
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} tidak ada pada jadwal {jid}.")
            elif kursi[0] == "terpesan":
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} sudah terpesan "
                          f"oleh {kursi[1]}.",
                    kursi=pb.Kursi(id=request.kursi_id, status="terpesan",
                                   pemesan=kursi[1]))
            else:
                kode = buat_kode_booking()
                while c.execute(
                        "SELECT 1 FROM kursi WHERE jadwal_id=? "
                        "AND kode_booking=?", (jid, kode)).fetchone():
                    kode = buat_kode_booking()
                c.execute(
                    "UPDATE kursi SET status='terpesan', pemesan=?, "
                    "kode_booking=? WHERE jadwal_id=? AND kursi_id=?",
                    (request.nama, kode, jid, request.kursi_id))
                self._naik_versi(jid)
                hasil = pb.PesanResponse(
                    sukses=True,
                    pesan=f"Kursi {request.kursi_id} berhasil dipesan "
                          f"oleh {request.nama}. Simpan kode booking Anda: {kode}.",
                    kursi=pb.Kursi(id=request.kursi_id, status="terpesan",
                                   pemesan=request.nama),
                    kode_booking=kode)
            if request.request_id:
                c.execute(
                    "INSERT OR IGNORE INTO proses VALUES (?,?,?,?,?,?,?)",
                    (request.request_id, int(hasil.sukses), hasil.pesan,
                     hasil.kursi.id if hasil.HasField("kursi") else "",
                     hasil.kursi.status if hasil.HasField("kursi") else "",
                     hasil.kursi.pemesan if hasil.HasField("kursi") else "",
                     hasil.kode_booking))
            c.commit()
            return hasil

    def BatalkanPesanan(self, request, context):
        jid = request.jadwal_id or self._jadwal_default()
        with self.lock:
            c = self.conn
            if request.request_id:
                row = c.execute(
                    "SELECT sukses, pesan, kursi_id, status, pemesan, "
                    "kode_booking FROM proses WHERE request_id=?",
                    (request.request_id,)).fetchone()
                if row:
                    return pb.PesanResponse(
                        sukses=bool(row[0]), pesan=row[1],
                        kursi=pb.Kursi(id=row[2], status=row[3], pemesan=row[4])
                        if row[2] else None,
                        kode_booking=row[5] or "")
            kursi = c.execute(
                "SELECT status, pemesan, kode_booking FROM kursi "
                "WHERE jadwal_id=? AND kursi_id=?",
                (jid, request.kursi_id)).fetchone()
            if kursi is None:
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} tidak ada pada jadwal {jid}.")
            elif kursi[0] == "kosong":
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan=f"Kursi {request.kursi_id} sudah kosong.")
            elif kursi[1] != request.nama or kursi[2] != request.kode_booking:
                # Pesan sengaja tidak membedakan "nama salah" vs "kode salah",
                # supaya kode booking tidak bisa ditebak lewat pesan error.
                hasil = pb.PesanResponse(
                    sukses=False,
                    pesan="Pembatalan ditolak: nama atau kode booking "
                          "tidak cocok dengan pesanan ini.")
            else:
                c.execute(
                    "UPDATE kursi SET status='kosong', pemesan='', "
                    "kode_booking='' WHERE jadwal_id=? AND kursi_id=?",
                    (jid, request.kursi_id))
                self._naik_versi(jid)
                hasil = pb.PesanResponse(
                    sukses=True,
                    pesan=f"Pesanan kursi {request.kursi_id} dibatalkan.",
                    kursi=pb.Kursi(id=request.kursi_id, status="kosong"))
            if request.request_id:
                c.execute(
                    "INSERT OR IGNORE INTO proses VALUES (?,?,?,?,?,?,?)",
                    (request.request_id, int(hasil.sukses), hasil.pesan,
                     hasil.kursi.id if hasil.HasField("kursi") else "",
                     hasil.kursi.status if hasil.HasField("kursi") else "",
                     hasil.kursi.pemesan if hasil.HasField("kursi") else "",
                     ""))
            c.commit()
            return hasil

    def LihatKursi(self, request, context):
        jid = request.jadwal_id or self._jadwal_default()
        with self.lock:
            rows = self.conn.execute(
                "SELECT kursi_id, status, pemesan FROM kursi "
                "WHERE jadwal_id=? ORDER BY substr(kursi_id,1,1), CAST(substr(kursi_id,2) AS INTEGER)", (jid,))
            return pb.LihatResponse(
                kursi=[pb.Kursi(id=r[0], status=r[1], pemesan=r[2])
                       for r in rows],
                jadwal_id=jid)

    def StreamingStatusKursi(self, request, context):
        jid = request.jadwal_id or self._jadwal_default()
        versi_terkirim = -1
        while context.is_active():
            with self.lock:
                v = self.versi.get(jid, 0)
                if v != versi_terkirim:
                    versi_terkirim = v
                    rows = self.conn.execute(
                        "SELECT kursi_id, status, pemesan FROM kursi "
                        "WHERE jadwal_id=? ORDER BY substr(kursi_id,1,1), CAST(substr(kursi_id,2) AS INTEGER)", (jid,))
                    yield pb.StatusUpdate(
                        kursi=[pb.Kursi(id=r[0], status=r[1], pemesan=r[2])
                               for r in rows],
                        versi=versi_terkirim, jadwal_id=jid)
            self.versi_event.wait(timeout=1.0)


def serve(host="0.0.0.0", port=50051, max_workers=20, unix=None):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_BioskopServicer_to_server(BioskopServicer(), server)
    server.add_insecure_port(f"{host}:{port}")
    if unix:
        server.add_insecure_port(f"unix://{unix}")
    server.start()
    print(f"Server Seatlock berjalan di {host}:{port} "
          f"(max_workers={max_workers}, DB: {DB_PATH})...", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--unix", default=None)
    args = parser.parse_args()
    serve(host=args.host, port=args.port, unix=args.unix)
