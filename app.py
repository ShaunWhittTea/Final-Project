from flask import Flask, request, jsonify
from db import get_db

app = Flask(__name__)

TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "clemson-test-2026")

def check_test_auth():
    return request.headers.get("X-Test-Password") == TEST_PASSWORD

# ---------------- HEALTH ----------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True}), 200

# ---------------- RESET ----------------
@app.post("/api/reset")
def reset():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM ships")
    cur.execute("DELETE FROM moves")
    cur.execute("DELETE FROM game_players")
    cur.execute("DELETE FROM games")
    cur.execute("DELETE FROM players")

    return jsonify({"status": "reset"}), 200

# ---------------- PLAYERS ----------------
@app.post("/api/players")
def create_player():
    data = request.json
    if "username" not in data:
        return jsonify({"error": "missing username"}), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO players (username) VALUES (%s) RETURNING player_id",
        (data["username"],)
    )
    pid = cur.fetchone()[0]

    return jsonify({"player_id": pid}), 201


@app.get("/api/players/<int:pid>/stats")
def player_stats(pid):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT total_games, total_wins, total_losses, total_shots, total_hits
        FROM players WHERE player_id=%s
    """, (pid,))
    row = cur.fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404

    games, wins, losses, shots, hits = row
    accuracy = round(hits / shots, 3) if shots > 0 else 0

    return jsonify({
        "games_played": games,
        "wins": wins,
        "losses": losses,
        "total_shots": shots,
        "total_hits": hits,
        "accuracy": accuracy
    }), 200


# ---------------- GAMES ----------------
@app.post("/api/games")
def create_game():
    data = request.json

    grid = data["grid_size"]
    maxp = data["max_players"]

    if grid < 5 or grid > 15:
        return jsonify({"error": "bad grid"}), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO games (grid_size, max_players)
        VALUES (%s, %s)
        RETURNING game_id
    """, (grid, maxp))
    gid = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO game_players (game_id, player_id, turn_order)
        VALUES (%s, %s, 0)
    """, (gid, data["creator_id"]))

    return jsonify({"game_id": gid}), 201


@app.post("/api/games/<int:gid>/join")
def join_game(gid):
    data = request.json
    pid = data["player_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT max_players, status FROM games WHERE game_id=%s", (gid,))
    game = cur.fetchone()
    if not game:
        return jsonify({"error": "not found"}), 404

    maxp, status = game

    if status != "waiting":
        return jsonify({"error": "not joinable"}), 409

    cur.execute("SELECT COUNT(*) FROM game_players WHERE game_id=%s", (gid,))
    count = cur.fetchone()[0]

    if count >= maxp:
        return jsonify({"error": "full"}), 409

    cur.execute("""
        INSERT INTO game_players (game_id, player_id, turn_order)
        VALUES (%s, %s, %s)
    """, (gid, pid, count))

    return jsonify({"status": "joined"}), 200


@app.get("/api/games/<int:gid>")
def get_game(gid):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT grid_size, status, current_turn_index
        FROM games WHERE game_id=%s
    """, (gid,))
    row = cur.fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404

    grid, status, turn = row

    cur.execute("SELECT COUNT(*) FROM game_players WHERE game_id=%s", (gid,))
    players = cur.fetchone()[0]

    return jsonify({
        "game_id": gid,
        "grid_size": grid,
        "status": status,
        "current_turn_index": turn,
        "active_players": players
    }), 200


# ---------------- SHIPS ----------------
@app.post("/api/games/<int:gid>/place")
def place(gid):
    data = request.json
    pid = data["player_id"]
    ships = data["ships"]

    if len(ships) != 3:
        return jsonify({"error": "need 3 ships"}), 400

    conn = get_db()
    cur = conn.cursor()

    for s in ships:
        cur.execute("""
            INSERT INTO ships (game_id, player_id, row, col)
            VALUES (%s, %s, %s, %s)
        """, (gid, pid, s["row"], s["col"]))

    # activate game
    cur.execute("SELECT COUNT(DISTINCT player_id) FROM ships WHERE game_id=%s", (gid,))
    placed = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM game_players WHERE game_id=%s", (gid,))
    total = cur.fetchone()[0]

    if placed == total:
        cur.execute("UPDATE games SET status='active' WHERE game_id=%s", (gid,))

    return jsonify({"status": "placed"}), 200


# ---------------- FIRE ----------------
@app.post("/api/games/<int:gid>/fire")
def fire(gid):
    data = request.json
    pid = data["player_id"]
    r = data["row"]
    c = data["col"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT status FROM games WHERE game_id=%s", (gid,))
    status = cur.fetchone()[0]

    if status == "finished":
        return jsonify({"error": "finished"}), 409

    # update shots
    cur.execute("""
        UPDATE players SET total_shots = total_shots + 1
        WHERE player_id=%s
    """, (pid,))

    # check hit
    cur.execute("""
        SELECT player_id FROM ships
        WHERE game_id=%s AND row=%s AND col=%s AND hit=FALSE
    """, (gid, r, c))
    hit_row = cur.fetchone()

    if hit_row:
        target = hit_row[0]

        cur.execute("""
            UPDATE ships SET hit=TRUE
            WHERE game_id=%s AND row=%s AND col=%s
        """, (gid, r, c))

        cur.execute("""
            UPDATE players SET total_hits = total_hits + 1
            WHERE player_id=%s
        """, (pid,))

        # check win
        cur.execute("""
            SELECT COUNT(*) FROM ships
            WHERE game_id=%s AND player_id=%s AND hit=FALSE
        """, (gid, target))
        remaining = cur.fetchone()[0]

        if remaining == 0:
            cur.execute("UPDATE games SET status='finished' WHERE game_id=%s", (gid,))

            # update stats
            cur.execute("""
                UPDATE players
                SET total_games = total_games + 1,
                    total_wins = total_wins + 1
                WHERE player_id=%s
            """, (pid,))

            cur.execute("""
                UPDATE players
                SET total_games = total_games + 1,
                    total_losses = total_losses + 1
                WHERE player_id!=%s
            """, (pid,))

            return jsonify({
                "result": "hit",
                "next_player_id": None,
                "game_status": "finished",
                "winner_id": pid
            }), 200

        return jsonify({
            "result": "hit",
            "game_status": "active"
        }), 200

    return jsonify({
        "result": "miss",
        "game_status": "active"
    }), 200


# ---------------- TEST MODE ----------------
@app.post("/api/test/games/<int:gid>/restart")
def restart(gid):
    if not check_test_auth():
        return jsonify({"error": "forbidden"}), 403

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM ships WHERE game_id=%s", (gid,))
    cur.execute("DELETE FROM moves WHERE game_id=%s", (gid,))
    cur.execute("UPDATE games SET status='waiting' WHERE game_id=%s", (gid,))

    return jsonify({"status": "restarted"}), 200


if __name__ == "__main__":
    app.run()
