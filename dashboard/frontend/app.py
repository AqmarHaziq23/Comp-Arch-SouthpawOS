"""SouthpawOS - web dashboard (Member E).

Run:   python app.py --sim        # fake Pi + demo data and logins, local SQLite file
       docker compose up --build  # the real thing: this app + a Postgres database
Pages: /login, /signup, /live (latest round, big screen), / (gym leaderboard),
       /player/<player id>, /sessions, /session/<id>
Every page needs a login (coach or athlete) and only shows the athletes of
the signed-in user's gym.
Pi -> dashboard: POST /api/round, one finished round + the NFC card ID (needs the INGEST_TOKEN).
"""
import argparse
import os
import secrets
import threading
import time

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for

import accounts
import db
import store

app = Flask(__name__)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)

PUBLIC = {"login", "signup", "static", "api_round"}     # api_round checks the token instead
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")       # shared secret between the Pi and this app


def start(database_url=None):
    """Connect to the database and load the cookie-signing key. Call once before serving."""
    db.init(database_url)
    app.secret_key = accounts.secret_key()


# ---------- login ----------
@app.before_request
def require_login():
    g.user = accounts.get_user(session["user_id"]) if "user_id" in session else None
    if g.user or request.endpoint in PUBLIC or request.endpoint is None:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Log in first."), 401
    return redirect(url_for("login"))


def home():
    """Athletes land on their own stats, coaches on their gym."""
    if g.user["role"] == "athlete":
        return redirect(url_for("player_page", pid=g.user["player_id"]))
    return redirect(url_for("index"))


def sign_in(user):
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    g.user = user
    return home()


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return home()
    f, error = request.form, None
    if request.method == "POST":
        user = accounts.check_login(f.get("role"), f.get("username"), f.get("password"))
        if user:
            return sign_in(user)
        error = f"Wrong username or password for {'a coach' if f.get('role') == 'coach' else 'an athlete'} login."
    return render_template("login.html", error=error, role=f.get("role", "athlete"), f=f), (401 if error else 200)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return home()
    f, error = request.form, None
    if request.method == "POST":
        if f.get("password") != f.get("password2"):
            error = "The two passwords do not match."
        else:
            try:
                return sign_in(accounts.create_user(
                    f.get("role"), f.get("username"), f.get("password"), f.get("name"),
                    gym_name=f.get("gym_name"), gym_code=f.get("gym_code"), card_uid=f.get("card_uid")))
            except ValueError as e:
                error = str(e)
    return render_template("signup.html", error=error, role=f.get("role", "athlete"), f=f), (400 if error else 200)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.post("/account/card")
def account_card():
    """An athlete links (or changes) the NFC card their rounds are saved under."""
    if g.user["role"] != "athlete":
        abort(403)
    try:
        accounts.set_card(g.user["id"], request.form.get("card_uid"))
    except ValueError as e:
        return render_template("profile.html", p=store.player_profile(g.user), own=True, card_error=str(e)), 400
    return home()


@app.post("/account/delete")
def account_delete():
    """An athlete removes their login and every session saved under their card."""
    if g.user["role"] != "athlete":
        abort(403)
    store.delete_sessions(g.user["card_uid"])
    accounts.delete_user(g.user["id"])
    session.clear()
    return redirect(url_for("login"))


# ---------- input from the Pi ----------
@app.post("/api/round")
def api_round():
    """One finished round (or a list of them). Open to the internet, so the Pi proves itself with the shared token."""
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not INGEST_TOKEN:
        return jsonify(error="INGEST_TOKEN is not set on the server."), 503
    if not secrets.compare_digest(sent.encode(), INGEST_TOKEN.encode()):
        return jsonify(error="Wrong or missing token."), 401
    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify(error="Body must be JSON."), 400
    try:
        ids = [store.ingest_round(item) for item in (data if isinstance(data, list) else [data])]
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, session_ids=ids)


# ---------- pages (all limited to the signed-in user's gym) ----------
def board():
    return store.leaderboard(accounts.athletes(g.user["gym_id"]), g.user["gym_id"])


@app.get("/")
def index():
    return render_template("index.html", board=board())


@app.get("/live")
def live():
    """The most recent round uploaded for this gym. The page reloads itself when a newer one arrives."""
    return render_template("live.html", r=store.latest_round(g.user["gym_id"]), board=board()[:5],
                           big=request.args.get("big") == "1")


@app.get("/api/latest")
def api_latest():
    r = store.latest_round(g.user["gym_id"])
    return jsonify(id=r["id"] if r else None, received_at=r["received_at"] if r else None)


@app.get("/sessions")
def sessions():
    return render_template("sessions.html", rows=store.recent_sessions(g.user["gym_id"], 100))


@app.get("/session/<int:sid>")
def session_page(sid):
    d = store.session_detail(sid, g.user["gym_id"])
    if not d:
        abort(404)
    return render_template("session.html", d=d, rec=store.hr_recovery(d["hr"]))


@app.get("/player/<pid>")
def player_page(pid):
    athlete = accounts.athlete(g.user["gym_id"], pid)
    if not athlete:
        abort(404)
    return render_template("profile.html", p=store.player_profile(athlete), own=athlete["id"] == g.user["id"])


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


@app.template_filter("when")
def when(ts):
    return time.strftime("%d %b, %H:%M", time.localtime(ts))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="run the built-in fake Pi with demo data")
    ap.add_argument("--speed", type=float, default=1.0, help="simulator speed multiplier")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    if args.sim:
        import sim
        start(os.environ.get("DATABASE_URL") or "sqlite:///" + (db.HERE / "demo.db").as_posix())
        accounts.seed_demo()
        store.seed_demo(accounts.all_cards())
        print(f"Demo logins (password {accounts.DEMO_PASSWORD}):")
        for gym_name, code, coach, people in accounts.DEMO:
            print(f"  {gym_name} [code {code}]  coach: {coach}   athletes: {', '.join(p[0] for p in people)}")
        threading.Thread(target=sim.run, args=(store.ingest_round, args.speed), daemon=True).start()
    else:
        start()
        if not INGEST_TOKEN:
            print("WARNING: INGEST_TOKEN is not set, so uploads from the Pi will be refused.")
    app.run(host="0.0.0.0", port=args.port, threaded=True)
