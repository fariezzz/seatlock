"""Gateway HTTP -> gRPC untuk Seatlock (Tier 1 Ringan).

Hanya memakai pustaka standar Python + grpcio (tidak perlu Flask).
Browser TIDAK memanggil gRPC langsung; gateway menerjemahkan HTTP
menjadi RPC, dan meneruskan request_id apa adanya supaya retry dari
browser tidak membuat pemesanan ganda.

Jalankan dari folder bioskop-rpc (server gRPC sudah aktif):
    python web/gateway.py [--grpc localhost:50051] [--port 8000]
Lalu buka http://localhost:8000/ di browser mesin mana pun yang satu
jaringan dengan mesin gateway.

Endpoint:
    GET  /api/jadwal               -> DaftarJadwal
    GET  /api/kursi?jadwal=J1      -> LihatKursi
    POST /api/pesan                -> PesanKursi
         {"jadwal_id","kursi_id","nama","request_id"}
    POST /api/batal                -> BatalkanPesanan
         {"jadwal_id","kursi_id","nama","request_id","kode_booking"}
    GET  /api/stream?jadwal=J1     -> StreamingStatusKursi, diubah jadi SSE
Timeout gateway (5 detik) sengaja LEBIH PANJANG dari timeout gRPC
(2 detik), supaya gateway tidak timeout duluan saat gRPC masih bekerja.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))

import bioskop_pb2 as pb
import bioskop_pb2_grpc as pb_grpc

GRPC_TARGET = "localhost:50051"
WEB_DIR = os.path.dirname(__file__)


def stub_baru():
    channel = grpc.insecure_channel(GRPC_TARGET)
    return channel, pb_grpc.BioskopStub(channel)


def kursi_ke_dict(k):
    return {"id": k.id, "status": k.status, "pemesan": k.pemesan}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _kirim_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _baca_json(self):
        panjang = int(self.headers.get("Content-Length", 0))
        if not panjang:
            return {}
        return json.loads(self.rfile.read(panjang) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        jadwal_id = query.get("jadwal", [""])[0]
        if parsed.path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/jadwal":
            channel, stub = stub_baru()
            try:
                res = stub.DaftarJadwal(pb.DaftarJadwalRequest(), timeout=5.0)
                self._kirim_json({"jadwal": [
                    {"id": j.id, "film": j.film, "waktu": j.waktu,
                     "studio": j.studio} for j in res.jadwal]})
            except grpc.RpcError as e:
                self._kirim_json({"error": e.code().name}, status=502)
            finally:
                channel.close()
        elif parsed.path == "/api/kursi":
            channel, stub = stub_baru()
            try:
                res = stub.LihatKursi(pb.LihatRequest(jadwal_id=jadwal_id),
                                      timeout=5.0)
                self._kirim_json({"jadwal_id": res.jadwal_id,
                                  "kursi": [kursi_ke_dict(k) for k in res.kursi]})
            except grpc.RpcError as e:
                self._kirim_json({"error": e.code().name}, status=502)
            finally:
                channel.close()
        elif parsed.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            channel, stub = stub_baru()
            try:
                for update in stub.StreamingStatusKursi(
                        pb.StatusRequest(jadwal_id=jadwal_id)):
                    data = json.dumps({
                        "versi": update.versi, "jadwal_id": update.jadwal_id,
                        "kursi": [kursi_ke_dict(k) for k in update.kursi]})
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, grpc.RpcError):
                pass
            finally:
                channel.close()
        else:
            self._kirim_json({"error": "tidak ditemukan"}, status=404)

    def do_POST(self):
        if self.path not in ("/api/pesan", "/api/batal"):
            self._kirim_json({"error": "tidak ditemukan"}, status=404)
            return
        try:
            body = self._baca_json()
        except (json.JSONDecodeError, ValueError):
            self._kirim_json({"error": "badan JSON tidak valid"}, status=400)
            return
        jadwal_id = body.get("jadwal_id", "")
        kursi_id = body.get("kursi_id", "")
        nama = body.get("nama", "")
        request_id = body.get("request_id", "")
        if not jadwal_id or not kursi_id or not nama or not request_id:
            self._kirim_json(
                {"error": "jadwal_id, kursi_id, nama, dan request_id wajib diisi"},
                status=400)
            return
        channel, stub = stub_baru()
        try:
            if self.path == "/api/pesan":
                res = stub.PesanKursi(pb.PesanRequest(
                    jadwal_id=jadwal_id, kursi_id=kursi_id, nama=nama,
                    request_id=request_id), timeout=5.0)
            else:
                kode = body.get("kode_booking", "")
                if not kode:
                    self._kirim_json(
                        {"error": "kode_booking wajib diisi untuk membatalkan"},
                        status=400)
                    return
                res = stub.BatalkanPesanan(pb.BatalRequest(
                    jadwal_id=jadwal_id, kursi_id=kursi_id, nama=nama,
                    request_id=request_id, kode_booking=kode), timeout=5.0)
            self._kirim_json({"sukses": res.sukses, "pesan": res.pesan,
                              "kode_booking": res.kode_booking,
                              "kursi": kursi_ke_dict(res.kursi)
                              if res.HasField("kursi") else None})
        except grpc.RpcError as e:
            self._kirim_json({"error": e.code().name}, status=502)
        finally:
            channel.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--grpc", default="localhost:50051")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    GRPC_TARGET = args.grpc
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Gateway Seatlock berjalan di http://{args.host}:{args.port}/ "
          f"(gRPC: {args.grpc})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGateway dihentikan.")
