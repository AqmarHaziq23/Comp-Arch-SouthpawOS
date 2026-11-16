"""Smoke test on a temporary SQLite database (or DATABASE_URL, to run it against Postgres)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as A, accounts, db, store
A.start(os.environ.get("DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db").replace("\\", "/"))
assert not db.rows("SELECT 1 FROM users LIMIT 1"), "run this test on an empty database"
A.INGEST_TOKEN = "pi-secret"
PI = {"Authorization": "Bearer pi-secret"}
PW = accounts.DEMO_PASSWORD
KAI_CARD, MEI_CARD, ARJUN_CARD = "04A2B91C", "04C7D2E5", "04F10A33"   # Kai + Mei: Southpaw Gym. Arjun: Northside.

def client(role=None, username=None):
    c = A.app.test_client()
    if role:
        r = c.post("/login", data={"role": role, "username": username, "password": PW})
        assert r.status_code == 302, (role, username, r.status_code)
    return c

def ok(c, path, code=200):
    r = c.get(path); assert r.status_code == code, (path, r.status_code); return r.get_data(as_text=True)

pi = A.app.test_client()
def upload(body, code=200, headers=PI):
    r = pi.post("/api/round", json=body, headers=headers); assert r.status_code == code, (body, r.status_code, r.get_json())
    return r.get_json()

# ---- nobody gets in without a login
anon = client()
for p in ["/", "/live", "/sessions", "/session/1", "/player/KAI"]:
    r = anon.get(p); assert r.status_code == 302 and r.headers["Location"].endswith("/login"), p
assert anon.get("/api/latest").status_code == 401
assert anon.post("/api/event", json={}).status_code == 404 and anon.get("/api/live").status_code == 404   # live feed is gone
assert "SouthpawOS" in ok(anon, "/login") and "Athlete" in ok(anon, "/signup")

# ---- empty database: sign-up works, pages are empty, not broken
accounts.seed_demo()
assert anon.post("/login", data={"role": "athlete", "username": "kai", "password": "nope"}).status_code == 401
assert anon.post("/login", data={"role": "coach", "username": "kai", "password": PW}).status_code == 401    # role must match
assert anon.post("/login", data={"role": "athlete", "username": "coach", "password": PW}).status_code == 401
coach, coach2, kai, arjun = client("coach", "coach"), client("coach", "coach2"), client("athlete", "KAI"), client("athlete", "arjun")
for p in ["/", "/live", "/sessions", "/player/KAI"]:
    ok(coach, p)
assert "No sessions yet" in ok(kai, "/player/KAI")
assert "No rounds yet" in ok(kai, "/live") and kai.get("/api/latest").get_json() == {"id": None, "received_at": None}

# ---- uploads from the Pi need the token
round1 = {"session": "pi-1", "round": 1, "card": "04a2b91c", "score": 120, "thrown": 18, "landed": 14,
          "combos_called": 6, "combos_hit": 2, "best_streak": 2, "hr": {"bpm": 140, "good": True}}
upload(round1, 401, headers={}); upload(round1, 401, headers={"Authorization": "Bearer wrong"})
A.INGEST_TOKEN = ""; upload(round1, 503); A.INGEST_TOKEN = "pi-secret"       # server without a token refuses everyone
assert not db.rows("SELECT 1 FROM sessions LIMIT 1")
# bad data is refused with a reason
for bad in [{"round": 1}, {"session": "x"}, {"session": "x", "round": 0}, {"session": "x", "round": "one"},
            {"session": "x", "round": 1, "thrown": 2, "landed": 5}, {"session": "x", "round": 1, "score": -5},
            {"session": "x", "round": 1, "hr": {"bpm": 900}}, "nope"]:
    assert upload(bad, 400)["error"], bad
assert pi.post("/api/round", data="nope", content_type="text/plain", headers=PI).status_code == 400
assert not db.rows("SELECT 1 FROM sessions LIMIT 1")

# ---- one round at a time: the session appears after round 1 and grows
sid = upload(round1)["session_ids"][0]
s = store.session_detail(sid, 1)
assert (s["session"]["nickname"], s["session"]["score"], s["session"]["rounds"], s["session"]["accuracy"]) == ("Kai", 120, 1, 78), s
assert "120" in ok(kai, f"/session/{sid}") and "2/6" in ok(kai, f"/session/{sid}")
assert upload({"session": "pi-1", "round": 2, "card": KAI_CARD, "score": 80, "thrown": 10, "landed": 8,
               "hr": {"bpm": 150, "good": False}})["session_ids"] == [sid]
s = store.session_detail(sid, 1)
assert (s["session"]["score"], s["session"]["thrown"], s["session"]["landed"], len(s["rounds"])) == (200, 28, 22, 2)
assert store.hr_recovery(s["hr"]) == {"avg_rest": 140}                       # the unreliable reading is left out
# the Pi retrying a round replaces it instead of counting it twice
upload(dict(round1, score=130)); s = store.session_detail(sid, 1)
assert (s["session"]["score"], len(s["rounds"])) == (210, 2), s
# several rounds in one request; a second session for Mei; one for Arjun (other gym); a guest; an unknown card
ids = upload([{"session": "pi-2", "round": 1, "card": MEI_CARD, "score": 90, "thrown": 12, "landed": 6},
              {"session": "pi-3", "round": 1, "card": ARJUN_CARD, "score": 300, "thrown": 20, "landed": 19, "best_streak": 5},
              {"session": "pi-4", "round": 1, "score": 50, "thrown": 5, "landed": 5},
              {"session": "pi-5", "round": 1, "card": "0BADCAFE", "score": 70, "thrown": 9, "landed": 3}])["session_ids"]
mei_sid, arjun_sid, guest_sid, stray_sid = ids

# ---- coach: only the athletes of their own gym
html = ok(coach, "/")
assert "Kai" in html and "Mei" in html and "Arjun" not in html and "Sam" not in html and "SOUTH1" in html
html = ok(coach2, "/")
assert "Arjun" in html and "Sam" in html and "Kai" not in html and "NORTH1" in html and "SOUTH1" not in html
board = store.leaderboard(accounts.athletes(1), 1)
assert [(b["nickname"], b["best_score"], b["sessions"], b["accuracy"]) for b in board] == [("Kai", 210, 1, 79), ("Mei", 90, 1, 50)], board
ok(coach, "/player/KAI"); ok(coach, "/player/kai"); ok(coach, f"/session/{sid}"); ok(coach, f"/session/{mei_sid}")
ok(coach, "/player/ARJUN", 404); ok(coach, f"/session/{arjun_sid}", 404); ok(coach, "/player/guest", 404); ok(coach, "/player/coach", 404)
ok(coach2, "/player/KAI", 404); ok(coach2, f"/session/{sid}", 404); ok(coach2, f"/session/{arjun_sid}")
html = ok(coach, "/sessions"); assert "Kai" in html and "Mei" in html and "Arjun" not in html
html = ok(coach, "/live"); assert "Kai" in html and "Arjun" not in html
assert "Delete my account" not in ok(coach, "/player/KAI") and "Link card" not in ok(coach, "/player/KAI")
assert coach.post("/account/delete").status_code == 403 and coach.post("/account/card", data={"card_uid": "0C0C0C0C"}).status_code == 403
# guest sessions and cards without an account belong to no gym, so nobody sees them
for c in (coach, coach2, kai):
    ok(c, f"/session/{guest_sid}", 404); ok(c, f"/session/{stray_sid}", 404)

# ---- athlete: own stats, plus the athletes of their gym
r = client().post("/login", data={"role": "athlete", "username": "kai", "password": PW})
assert r.headers["Location"].endswith("/player/KAI")                 # lands on their own stats
assert "Delete my account" in ok(kai, "/player/KAI") and "Delete my account" not in ok(kai, "/player/MEI")
html = ok(kai, "/"); assert "Mei" in html and "Arjun" not in html and "SOUTH1" not in html
ok(kai, "/player/ARJUN", 404); ok(kai, f"/session/{arjun_sid}", 404); ok(arjun, "/player/KAI", 404)
ok(arjun, f"/session/{arjun_sid}"); ok(kai, "/api/latest"); ok(kai, "/live?big=1")
prof = store.player_profile(accounts.athlete(2, "arjun"))
assert (prof["best_score"], prof["best_streak"], prof["total_sessions"], prof["accuracy"]) == (300, 5, 1, 95), prof

# ---- "Latest round" page: the most recent round uploaded for the viewer's own gym
# so far the newest rounds are the guest's and the unknown card's, which no gym sees
r = store.latest_round(1); assert (r["nickname"], r["round_no"], r["score"], r["session_id"]) == ("Mei", 1, 90, mei_sid), r
r = store.latest_round(2); assert (r["nickname"], r["score"], r["best_streak"]) == ("Arjun", 300, 5), r
html = ok(kai, "/live"); assert "Mei" in html and "Latest round" in html and "Arjun" not in html and "no reading" in html and "Only the totals were uploaded" in html
html = ok(arjun, "/live?big=1"); assert "Arjun" in html and "300" in html and "Mei" not in html
before = kai.get("/api/latest").get_json(); other_gym = arjun.get("/api/latest").get_json()
new_sid = upload({"session": "pi-6", "round": 1, "card": KAI_CARD, "score": 10, "thrown": 3, "landed": 1,
                  "combos_called": 1, "combos_hit": 0, "hr": {"bpm": 151, "good": False}})["session_ids"][0]
after = kai.get("/api/latest").get_json(); assert after != before and after["id"]            # the page will reload
assert arjun.get("/api/latest").get_json() == other_gym                                      # other gym: nothing changed
html = ok(kai, "/live"); assert "Kai" in html and "0/1" in html and "151" in html and "unreliable" in html
assert f"/session/{new_sid}" in html
upload({"session": "pi-1", "round": 3, "card": KAI_CARD, "score": 40, "thrown": 6, "landed": 4})   # older session, newer round
r = store.latest_round(1); assert (r["session_id"], r["round_no"], r["session"]["score"], r["session"]["rounds"]) == (sid, 3, 250, 3), r

# ---- round dashboard: the combos, punches and impacts behind a round's totals
detail = {"session": "pi-7", "round": 1, "card": MEI_CARD, "score": 85, "duration_s": 30, "unmatched_impacts": 2,
          "best_streak": 1, "hr": {"bpm": 138, "good": True}, "combos": [
    {"called": [1, 2, 3], "hit": True, "correct": 3, "points": 50, "punches": [
        {"hand": "L", "type": "J", "peak_g": 10, "t": 1.4, "landed": True, "impact": 60},
        {"hand": "R", "type": "C", "peak_g": 16, "t": 1.9, "landed": True, "impact": 80},
        {"hand": "L", "type": "HOOK", "peak_g": 14, "t": 2.5, "landed": False}]},
    {"called": "2-6", "hit": False, "correct": 1, "points": 15, "punches": [
        {"hand": "r", "type": "c", "peak_g": 12, "t": 6.0, "landed": True, "impact_strength": 40},
        {"hand": "R", "type": "X", "peak_g": 8, "t": 999999, "landed": False}]},
    {"called": [4], "hit": False, "correct": 0, "points": 0, "punches": []}]}
dsid = upload(detail)["session_ids"][0]
r = store.round_detail(dsid, 1, 1)
assert (r["thrown"], r["landed"], r["accuracy"], r["combos_called"], r["combos_hit"]) == (5, 3, 60, 3, 1), r   # counted from combos
assert (r["avg_g"], r["max_g"], r["avg_impact"], r["per_min"], r["unmatched_impacts"]) == (12.0, 16, 60.0, 10, 2), r
assert [(t["code"], t["thrown"], t["landed"], t["accuracy"]) for t in r["by_type"]] == [("J", 1, 1, 100), ("C", 2, 2, 100), ("H", 1, 0, 0), ("X", 1, 0, 0)]
assert [(h["code"], h["thrown"], h["landed"]) for h in r["by_hand"]] == [("L", 2, 1), ("R", 3, 2)]
c1, c2, c3 = r["combos"]
assert (c1["called"], c1["names"], c1["landed"], len(c1["punches"]), c1["hit"]) == ("1-2-3", ["Jab", "Cross", "Lead hook"], 2, 3, 1)
assert (c2["called"], c2["names"], c2["punches"][0]["impact"], c2["punches"][1]["t"]) == ("2-6", ["Cross", "Rear uppercut"], 40, None)
assert c3["punches"] == [] and r["has_detail"]
html = ok(kai, f"/session/{dsid}/round/1")          # Kai is in Mei's gym
for text in ["Mei", "Punches by type", "Every punch through the round", "Combos called", "1-2-3", "Jab · Cross · Lead hook",
             "L Jab", "R Cross", "HIT", "MISS", "nothing thrown", "12.0 g", "Average g this round", "16 g", "Bag hits with no punch", "138", 'id="byType"', 'id="timeline"']:
    assert text in html, text
assert f"/session/{dsid}/round/1" in ok(kai, f"/session/{dsid}")
ok(arjun, f"/session/{dsid}/round/1", 404); ok(coach2, f"/session/{dsid}/round/1", 404); ok(kai, f"/session/{dsid}/round/2", 404)
assert anon.get(f"/session/{dsid}/round/1").status_code == 302
html = ok(kai, "/live"); assert "Latest round" in html and "Combos called" in html and "Mei" in html       # newest round has detail
# a round uploaded with totals only still has a page, without the detail
html = ok(kai, f"/session/{sid}/round/1"); assert "Only the totals were uploaded" in html and 'id="byType"' not in html
# re-uploading the round replaces its combos and punches, not adds to them
upload(dict(detail, combos=detail["combos"][:1]))
r = store.round_detail(dsid, 1, 1); assert (len(r["combos"]), len(r["punches"]), r["thrown"]) == (1, 3, 3), r
assert len(db.rows("SELECT 1 FROM punches")) == 3 and len(db.rows("SELECT 1 FROM combos")) == 1
for bad in [{"combos": "x"}, {"combos": [{"called": [7]}]}, {"combos": [{"called": []}]}, {"combos": [{"called": [1], "punches": [{"hand": "Q", "type": "J"}]}]},
            {"combos": [{"called": [1], "punches": [{"hand": "L", "type": "Z"}]}]}, {"combos": [{"called": [1], "punches": [{"hand": "L", "type": "J", "peak_g": 500}]}]}]:
    assert upload(dict(bad, session="pi-bad", round=1), 400)["error"], bad
assert not db.rows("SELECT 1 FROM sessions WHERE session_key = 'pi-bad'")
store.delete_sessions(MEI_CARD); assert not db.rows("SELECT 1 FROM punches") and not db.rows("SELECT 1 FROM combos")
upload({"session": "pi-2", "round": 1, "card": MEI_CARD, "score": 90, "thrown": 12, "landed": 6})

# ---- sign up
def signup(**form):
    c = client(); form.setdefault("password", "secret1"); form.setdefault("password2", form["password"])
    return c, c.post("/signup", data=form)
bad = lambda **form: signup(**form)[1].status_code == 400
assert bad(role="athlete", username="zed", name="Zed", gym_code="WRONG")                              # no such gym
assert bad(role="athlete", username="zed", name="Zed", gym_code="")                                   # gym code needed
assert bad(role="athlete", username="Kai", name="Zed", gym_code="SOUTH1")                             # username taken
assert bad(role="athlete", username="z/d", name="Zed", gym_code="SOUTH1")                             # letters and numbers only
assert bad(role="athlete", username="guest", name="Zed", gym_code="SOUTH1")
assert bad(role="athlete", username="zed", name="Zed", gym_code="SOUTH1", card_uid=KAI_CARD)          # card taken
assert bad(role="athlete", username="zed", name="Zed", gym_code="SOUTH1", password="short")
assert bad(role="athlete", username="zed", name="Zed", gym_code="SOUTH1", password2="other11")
assert bad(role="coach", username="boss", name="Boss", gym_name="southpaw gym")                       # gym exists
# athlete signs up without a card, then links one: rounds already uploaded for that card become theirs
zc, r = signup(role="athlete", username="zed", name="Zed", gym_code="south1")
assert r.status_code == 302 and r.headers["Location"].endswith("/player/ZED")
html = ok(zc, "/player/ZED"); assert "No sessions yet" in html and "Link card" in html
assert "has not linked a card" in ok(coach, "/player/ZED") and "Zed" in ok(coach, "/") and "Zed" not in ok(coach2, "/")
assert zc.post("/account/card", data={"card_uid": KAI_CARD}).status_code == 400                       # someone else's card
assert zc.post("/account/card", data={"card_uid": ""}).status_code == 400
assert zc.post("/account/card", data={"card_uid": " 0bad cafe "}).status_code == 302
html = ok(zc, "/player/ZED"); assert "0BADCAFE" in html and "Change card" in html and "No sessions yet" not in html
ok(zc, f"/session/{stray_sid}"); ok(coach, f"/session/{stray_sid}"); ok(coach2, f"/session/{stray_sid}", 404)
# athlete signs up with a card straight away
yc, r = signup(role="athlete", username="yan", name="Yan", gym_code="NORTH1", card_uid="0c0c0c0c")
assert r.status_code == 302 and accounts.by_card("0C0C0C0C")["name"] == "Yan"
# coach signs up: new gym, empty, with its own code
bc, r = signup(role="coach", username="boss", name="Boss", gym_name="East Side")
assert r.status_code == 302 and "No athletes in this gym yet" in ok(bc, "/") and "Kai" not in ok(bc, "/sessions")
ok(bc, "/player/KAI", 404)
assert bc.post("/logout").status_code == 302 and bc.get("/").status_code == 302

# ---- delete my account: removes the login and that athlete's sessions only
assert zc.post("/account/delete").status_code == 302 and accounts.by_card("0BADCAFE") is None
assert not db.rows("SELECT 1 FROM sessions WHERE card_uid = '0BADCAFE'")
assert not db.rows("SELECT 1 FROM rounds WHERE session_id = :s", s=stray_sid)
assert len(db.rows("SELECT 1 FROM sessions WHERE card_uid = :c", c=KAI_CARD)) == 2
assert zc.get("/").status_code == 302 and "Zed" not in ok(coach, "/")

# ---- the simulator makes rounds the website accepts, in the hub's shape
import random, sim
before = len(db.rows("SELECT 1 FROM sessions"))
sim.seed(store.ingest_round, accounts.all_cards(), has_sessions=True); assert len(db.rows("SELECT 1 FROM sessions")) == before
fake = sim.fake_round(random.Random(1))
fsid = store.ingest_round(dict(fake, session="sim-test", round=1, card=KAI_CARD))
r = store.round_detail(fsid, 1, 1)
assert r["has_detail"] and r["thrown"] == sum(len(c["punches"]) for c in fake["combos"]) and r["score"] == fake["score"]
assert r["combos_hit"] == sum(c["hit"] for c in fake["combos"]) and all(p["t"] is not None and p["t"] <= 30 for p in r["punches"])
ok(kai, f"/session/{fsid}/round/1")
print("all ok")
