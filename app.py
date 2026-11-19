import os, json, sqlite3, glob
from flask import Flask, Response, jsonify, request, send_from_directory, render_template, abort
from werkzeug.utils import secure_filename
import engine

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "runs.db")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP, filename TEXT, src TEXT,
                 vendor TEXT, vendor_key TEXT, invoice_no TEXT, inv_date TEXT, po TEXT, total REAL, decision TEXT, reasons TEXT, events TEXT, result TEXT)""")
    return c

def resolve(src, name):
    folder = {"samples": "samples", "uploads": "uploads"}.get(src) or abort(404)
    p = os.path.join(BASE, folder, os.path.basename(name))
    return p if os.path.isfile(p) else abort(404)

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/samples")
def samples():
    return jsonify([os.path.basename(p) for p in sorted(glob.glob(os.path.join(BASE, "samples", "*.pdf")))])

@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".pdf"): return jsonify(error="Please upload a PDF file"), 400
    name = secure_filename(f.filename); f.save(os.path.join(BASE, "uploads", name))
    return jsonify(src="uploads", file=name)

@app.route("/file/<src>/<name>")
def file(src, name):
    p = resolve(src, name); return send_from_directory(os.path.dirname(p), os.path.basename(p), mimetype="application/pdf")

@app.route("/preview/<src>/<name>.png")
def preview(src, name):
    import subprocess
    p = resolve(src, name + ".pdf") if not name.endswith(".pdf") else resolve(src, name)
    out = subprocess.run(["pdftoppm", "-r", "110", "-png", "-singlefile", p], capture_output=True).stdout
    return Response(out, mimetype="image/png", headers={"Cache-Control": "no-cache"})

@app.route("/api/stream")
def stream():
    src, name = request.args.get("src"), request.args.get("file")
    path = resolve(src, name)
    def gen():
        con = db()
        prior = [dict(run_id=r["id"], vendor_key=r["vendor_key"], invoice_no=r["invoice_no"], po=r["po"], total=r["total"], decision=r["decision"], date=r["inv_date"])
                 for r in con.execute("SELECT * FROM runs")]
        final, events = None, []
        try:
            for e in engine.process(path, prior):
                if e["status"] != "running": events.append(e)
                final = e if e["stage"] == "decision" and e["status"] != "running" else final
                yield f"data: {json.dumps(e)}\n\n"
            r = final["data"]; f = r["fields"]
            cur = con.execute("INSERT INTO runs(filename,src,vendor,vendor_key,invoice_no,inv_date,po,total,decision,reasons,events,result) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, src, f.get("vendor"), r["vendor_key"], f.get("invoice_no"), f.get("date"), r["po"], f.get("total"), r["decision"],
                 json.dumps(r["reasons"]), json.dumps(events), json.dumps(r)))
            con.commit()
            yield f"data: {json.dumps({'stage': 'saved', 'run_id': cur.lastrowid})}\n\n"
        except Exception as ex:   # never leave the UI hanging
            yield f"data: {json.dumps({'stage': 'error', 'summary': f'Processing error: {ex.__class__.__name__}: {ex}'})}\n\n"
        finally:
            con.close()
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/runs")
def runs():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT id,ts,filename,src,vendor,vendor_key,invoice_no,inv_date,po,total,decision,reasons FROM runs ORDER BY id DESC")]
    for r in rows: r["reasons"] = json.loads(r["reasons"])
    s = {"total": len(rows), **{k: sum(1 for r in rows if r["decision"] == k) for k in ("APPROVE", "NEEDS_REVIEW", "REJECT")}}
    s["approved_amount"] = round(sum(r["total"] or 0 for r in rows if r["decision"] == "APPROVE"), 2)
    s["held_amount"] = round(sum(r["total"] or 0 for r in rows if r["decision"] != "APPROVE"), 2)
    return jsonify(runs=rows, stats=s)

@app.route("/api/runs/<int:rid>")
def run(rid):
    r = db().execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone() or abort(404)
    r = dict(r); r["events"] = json.loads(r["events"]); r["result"] = json.loads(r["result"]); r["reasons"] = json.loads(r["reasons"]); return jsonify(r)

@app.route("/api/reset", methods=["POST"])
def reset():
    con = db(); con.execute("DELETE FROM runs"); con.execute("DELETE FROM sqlite_sequence WHERE name='runs'"); con.commit(); return jsonify(ok=True)

@app.route("/api/pos")
def pos(): 
    con = db(); billed = {}
    for r in con.execute("SELECT po,SUM(total) t FROM runs WHERE decision='APPROVE' AND po IS NOT NULL GROUP BY po"): billed[r["po"]] = r["t"]
    return jsonify([{**p, "billed": round(billed.get(k, 0), 2), "remaining": round(p["po_amount"] - billed.get(k, 0), 2)} for k, p in engine.load_pos().items()])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
