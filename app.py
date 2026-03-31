from flask import Flask, request, jsonify
from db import get_db, init_db
import os

app = Flask(__name__)
init_db()

TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "clemson-test-2026")


def check_test_auth():
    return request.headers.get("X-Test-Password") == TEST_PASSWORD


def get_json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def fetch_game(cur, gid):
    cur.execute("""
        SELECT game_id, grid_size, max_players, status, current_turn_index
        FROM games
        WHERE game_id = %s
    """, (gid,))
    return cur.fetchone()


def player_exists(cur, pid):
    cur.execute("SELECT 1 FROM players WHERE player_id = %s", (pid,))
    return cur.fetchone() is not None


def game_player_exists(cur, gid, pid):
    cur.execute("""
        SELECT turn_order
        FROM game_players
        WHERE game_id = %s AND player_id = %s
    """, (gid, pid))
    return cur.fetchone()


def count_players(cur, gid):
    cur.execute("SELECT COUNT(*) FROM game_players WHERE game_id = %s", (gid,))
    return cur.fetchone()[0]


def count_placed_players(cur, gid):
    cur.execute("""
        SELECT COUNT(*)
        FROM (
            SELECT player_id
            FROM ships
            WHERE game_id = %s
            GROUP BY player_id
            HAVING COUNT(*) = 3
        ) placed
    """, (gid,))
    return cur.fetchone()[0]


def all_players_placed(cur, gid):
    return count_players(cur, gid) > 0 and count_players(cur, gid) == count_placed_players(cur, gid)


def player_has_placed(cur, gid, pid):
    cur.execute("""
        SELECT COUNT(*)
        FROM ships
        WHERE game_id = %s AND player_id = %s
    """, (gid, pid))
    return cur.fetchone()[0] == 3


def player_alive(cur, gid, pid):
    cur.execute("""
        SELECT COUNT(*)
        FROM ships
        WHERE game_id = %s AND player_id = %s AND hit = FALSE
    """, (gid, pid))
    return cur.fetchone()[0] > 0


def active_players_with_ships(cur, gid):
    cur.execute("""
        SELECT gp.player_id, gp.turn_order
        FROM game_players gp
        WHERE gp.game_id = %s
        ORDER BY gp.turn_order
    """, (gid,))
    rows = cur.fetchall()
    alive = []
    for pid, turn_order in rows:
        if player_alive(cur, gid, pid):
            alive.append((pid, turn_order))
    return alive


def next_alive_player(cur, gid, current_turn_index):
    cur.execute("""
        SELECT gp.player_id, gp.turn_order
        FROM game_players gp
        WHERE gp.game_id = %s
        ORDER BY gp.turn_order
    """, (gid,))
    rows = cur.fetchall()
    if not rows:
        return None

    ordered_turns = [row[1] for row in rows]
    if current_turn_index not in ordered_turns:
        current_turn_index = ordered_turns[0]

    start_idx = ordered_turns.index(current_turn_index)
    n = len(rows)

    for step in range(1, n + 1):
        pid, turn_order = rows[(start_idx + step) % n]
        if player_alive(cur, gid, pid):
            return pid, turn_order

    return None


def remaining_opponents(cur, gid, pid):
    cur.execute("""
        SELECT COUNT(*)
        FROM (
            SELECT player_id
            FROM ships
            WHERE game_id = %s AND player_id <> %s AND hit = FALSE
            GROUP BY player_id
        ) alive_opponents
    """, (gid, pid))
    return cur.fetchone()[0]


@app.get("/api/health")
def health():
    return jsonify({"ok": True}), 200


@app.post("/api/reset")
def reset():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE TABLE moves, ships, game_players, games, players
                RESTART IDENTITY CASCADE
            """)
        return jsonify({"status": "reset"}), 200
    finally:
        conn.close()


@app.post("/api/players")
def create_player():
    data = get_json()
    username = data.get("username")

    if not username:
        return jsonify({"error": "missing username"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO players (username) VALUES (%s) RETURNING player_id",
                    (username,)
                )
                pid = cur.fetchone()[0]
            except Exception:
                return jsonify({"error": "username already exists"}), 409

        return jsonify({"player_id": pid}), 201
    finally:
        conn.close()


@app.get("/api/players/<int:pid>/stats")
def player_stats(pid):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT total_games, total_wins, total_losses, total_shots, total_hits
                FROM players
                WHERE player_id = %s
            """, (pid,))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": "not found"}), 404

            games, wins, losses, shots, hits = row
            accuracy = round(hits / shots, 3) if shots > 0 else 0.0

            return jsonify({
                "games_played": games,
                "wins": wins,
                "losses": losses,
                "total_shots": shots,
                "total_hits": hits,
                "accuracy": accuracy
            }), 200
    finally:
        conn.close()


@app.post("/api/games")
def create_game():
    data = get_json()

    creator_id = data.get("creator_id")
    grid_size = data.get("grid_size")
    max_players = data.get("max_players")

    if creator_id is None or grid_size is None or max_players is None:
        return jsonify({"error": "missing fields"}), 400

    if not isinstance(grid_size, int) or grid_size < 5 or grid_size > 15:
        return jsonify({"error": "bad grid"}), 400

    if not isinstance(max_players, int) or max_players < 1:
        return jsonify({"error": "bad max_players"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if not player_exists(cur, creator_id):
                return jsonify({"error": "creator not found"}), 404

            cur.execute("""
                INSERT INTO games (grid_size, max_players, status, current_turn_index)
                VALUES (%s, %s, 'waiting', 0)
                RETURNING game_id
            """, (grid_size, max_players))
            gid = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO game_players (game_id, player_id, turn_order)
                VALUES (%s, %s, 0)
            """, (gid, creator_id))

        return jsonify({"game_id": gid}), 201
    finally:
        conn.close()


@app.post("/api/games/<int:gid>/join")
def join_game(gid):
    data = get_json()
    pid = data.get("player_id")

    if pid is None:
        return jsonify({"error": "missing player_id"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            _, _, max_players, status, _ = game

            if not player_exists(cur, pid):
                return jsonify({"error": "player not found"}), 404

            if status != "waiting":
                return jsonify({"error": "not joinable"}), 409

            if game_player_exists(cur, gid, pid):
                return jsonify({"error": "already joined"}), 400

            count = count_players(cur, gid)
            if count >= max_players:
                return jsonify({"error": "full"}), 409

            cur.execute("""
                INSERT INTO game_players (game_id, player_id, turn_order)
                VALUES (%s, %s, %s)
            """, (gid, pid, count))

        return jsonify({"status": "joined"}), 200
    finally:
        conn.close()


@app.get("/api/games/<int:gid>")
def get_game(gid):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            _, grid_size, _, status, current_turn_index = game
            active_count = count_players(cur, gid)

            return jsonify({
                "game_id": gid,
                "grid_size": grid_size,
                "status": status,
                "current_turn_index": current_turn_index,
                "active_players": active_count
            }), 200
    finally:
        conn.close()


@app.post("/api/games/<int:gid>/place")
def place(gid):
    data = get_json()
    pid = data.get("player_id")
    ships = data.get("ships")

    if pid is None or ships is None:
        return jsonify({"error": "missing fields"}), 400

    if not isinstance(ships, list) or len(ships) != 3:
        return jsonify({"error": "need 3 ships"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            _, grid_size, _, status, _ = game

            if status != "waiting":
                return jsonify({"error": "cannot place now"}), 409

            if not game_player_exists(cur, gid, pid):
                return jsonify({"error": "player not in game"}), 403

            if player_has_placed(cur, gid, pid):
                return jsonify({"error": "already placed"}), 409

            seen = set()
            coords = []

            for ship in ships:
                if not isinstance(ship, dict):
                    return jsonify({"error": "invalid ship"}), 400

                row = ship.get("row")
                col = ship.get("col")

                if not isinstance(row, int) or not isinstance(col, int):
                    return jsonify({"error": "invalid coordinate"}), 400

                if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                    return jsonify({"error": "out of bounds"}), 400

                if (row, col) in seen:
                    return jsonify({"error": "overlap"}), 400

                seen.add((row, col))
                coords.append((row, col))

            for row, col in coords:
                cur.execute("""
                    INSERT INTO ships (game_id, player_id, row, col)
                    VALUES (%s, %s, %s, %s)
                """, (gid, pid, row, col))

            if all_players_placed(cur, gid):
                cur.execute("""
                    UPDATE games
                    SET status = 'active',
                        current_turn_index = 0
                    WHERE game_id = %s
                """, (gid,))

        return jsonify({"status": "placed"}), 200
    finally:
        conn.close()


@app.post("/api/games/<int:gid>/fire")
def fire(gid):
    data = get_json()
    pid = data.get("player_id")
    row = data.get("row")
    col = data.get("col")

    if pid is None or row is None or col is None:
        return jsonify({"error": "missing fields"}), 400

    if not isinstance(row, int) or not isinstance(col, int):
        return jsonify({"error": "invalid coordinates"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            _, grid_size, _, status, current_turn_index = game

            if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                return jsonify({"error": "out of bounds"}), 400

            gp = game_player_exists(cur, gid, pid)
            if not gp:
                return jsonify({"error": "player not in game"}), 403

            player_turn_order = gp[0]

            if status == "finished":
                return jsonify({"error": "finished"}), 409

            if not all_players_placed(cur, gid):
                return jsonify({"error": "not all players placed"}), 409

            if status != "active":
                return jsonify({"error": "game not active"}), 409

            if player_turn_order != current_turn_index:
                return jsonify({"error": "out of turn"}), 403

            cur.execute("""
                UPDATE players
                SET total_shots = total_shots + 1
                WHERE player_id = %s
            """, (pid,))

            cur.execute("""
                SELECT player_id
                FROM ships
                WHERE game_id = %s
                  AND player_id <> %s
                  AND row = %s
                  AND col = %s
                  AND hit = FALSE
                ORDER BY player_id
                LIMIT 1
            """, (gid, pid, row, col))
            hit_row = cur.fetchone()

            if hit_row:
                target_pid = hit_row[0]

                cur.execute("""
                    UPDATE ships
                    SET hit = TRUE
                    WHERE game_id = %s
                      AND player_id = %s
                      AND row = %s
                      AND col = %s
                """, (gid, target_pid, row, col))

                cur.execute("""
                    UPDATE players
                    SET total_hits = total_hits + 1
                    WHERE player_id = %s
                """, (pid,))

                cur.execute("""
                    INSERT INTO moves (game_id, player_id, row, col, result)
                    VALUES (%s, %s, %s, %s, 'hit')
                """, (gid, pid, row, col))

                if remaining_opponents(cur, gid, pid) == 0:
                    cur.execute("""
                        UPDATE games
                        SET status = 'finished'
                        WHERE game_id = %s
                    """, (gid,))

                    cur.execute("""
                        UPDATE players
                        SET total_games = total_games + 1,
                            total_wins = total_wins + 1
                        WHERE player_id = %s
                    """, (pid,))

                    cur.execute("""
                        UPDATE players
                        SET total_games = total_games + 1,
                            total_losses = total_losses + 1
                        WHERE player_id IN (
                            SELECT gp.player_id
                            FROM game_players gp
                            WHERE gp.game_id = %s
                              AND gp.player_id <> %s
                        )
                    """, (gid, pid))

                    return jsonify({
                        "result": "hit",
                        "next_player_id": None,
                        "game_status": "finished",
                        "winner_id": pid
                    }), 200

                nxt = next_alive_player(cur, gid, current_turn_index)
                next_player_id = None
                next_turn_order = current_turn_index
                if nxt is not None:
                    next_player_id, next_turn_order = nxt

                cur.execute("""
                    UPDATE games
                    SET current_turn_index = %s
                    WHERE game_id = %s
                """, (next_turn_order, gid))

                return jsonify({
                    "result": "hit",
                    "next_player_id": next_player_id,
                    "game_status": "active"
                }), 200

            cur.execute("""
                INSERT INTO moves (game_id, player_id, row, col, result)
                VALUES (%s, %s, %s, %s, 'miss')
            """, (gid, pid, row, col))

            nxt = next_alive_player(cur, gid, current_turn_index)
            next_player_id = None
            next_turn_order = current_turn_index
            if nxt is not None:
                next_player_id, next_turn_order = nxt

            cur.execute("""
                UPDATE games
                SET current_turn_index = %s
                WHERE game_id = %s
            """, (next_turn_order, gid))

            return jsonify({
                "result": "miss",
                "next_player_id": next_player_id,
                "game_status": "active"
            }), 200
    finally:
        conn.close()


@app.get("/api/games/<int:gid>/moves")
def moves(gid):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            cur.execute("""
                SELECT player_id, row, col, result
                FROM moves
                WHERE game_id = %s
                ORDER BY move_id ASC
            """, (gid,))
            rows = cur.fetchall()

            return jsonify([
                {
                    "player_id": player_id,
                    "row": row,
                    "col": col,
                    "result": result
                }
                for player_id, row, col, result in rows
            ]), 200
    finally:
        conn.close()


@app.post("/api/test/games/<int:gid>/restart")
def restart(gid):
    if not check_test_auth():
        return jsonify({"error": "forbidden"}), 403

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            cur.execute("DELETE FROM ships WHERE game_id = %s", (gid,))
            cur.execute("DELETE FROM moves WHERE game_id = %s", (gid,))
            cur.execute("""
                UPDATE games
                SET status = 'waiting',
                    current_turn_index = 0
                WHERE game_id = %s
            """, (gid,))

        return jsonify({"status": "restarted"}), 200
    finally:
        conn.close()


@app.post("/api/test/games/<int:gid>/ships")
def test_place_ships(gid):
    if not check_test_auth():
        return jsonify({"error": "forbidden"}), 403

    data = get_json()
    pid = data.get("player_id")
    ships = data.get("ships")

    if pid is None or ships is None:
        return jsonify({"error": "missing fields"}), 400

    if not isinstance(ships, list) or len(ships) != 3:
        return jsonify({"error": "need 3 ships"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            _, grid_size, _, status, _ = game

            if status == "finished":
                return jsonify({"error": "game finished"}), 409

            if not game_player_exists(cur, gid, pid):
                return jsonify({"error": "player not in game"}), 403

            cur.execute("""
                DELETE FROM ships
                WHERE game_id = %s AND player_id = %s
            """, (gid, pid))

            seen = set()
            coords = []

            for ship in ships:
                if not isinstance(ship, dict):
                    return jsonify({"error": "invalid ship"}), 400

                row = ship.get("row")
                col = ship.get("col")

                if not isinstance(row, int) or not isinstance(col, int):
                    return jsonify({"error": "invalid coordinate"}), 400

                if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                    return jsonify({"error": "out of bounds"}), 400

                if (row, col) in seen:
                    return jsonify({"error": "overlap"}), 400

                seen.add((row, col))
                coords.append((row, col))

            for row, col in coords:
                cur.execute("""
                    INSERT INTO ships (game_id, player_id, row, col)
                    VALUES (%s, %s, %s, %s)
                """, (gid, pid, row, col))

            if all_players_placed(cur, gid):
                cur.execute("""
                    UPDATE games
                    SET status = 'active',
                        current_turn_index = 0
                    WHERE game_id = %s
                """, (gid,))
            else:
                cur.execute("""
                    UPDATE games
                    SET status = 'waiting',
                        current_turn_index = 0
                    WHERE game_id = %s
                """, (gid,))

        return jsonify({"status": "placed"}), 200
    finally:
        conn.close()


@app.get("/api/test/games/<int:gid>/board/<int:pid>")
def reveal_board(gid, pid):
    if not check_test_auth():
        return jsonify({"error": "forbidden"}), 403

    conn = get_db()
    try:
        with conn.cursor() as cur:
            game = fetch_game(cur, gid)
            if not game:
                return jsonify({"error": "not found"}), 404

            if not game_player_exists(cur, gid, pid):
                return jsonify({"error": "player not in game"}), 403

            cur.execute("""
                SELECT row, col, hit
                FROM ships
                WHERE game_id = %s AND player_id = %s
                ORDER BY row, col
            """, (gid, pid))
            ship_rows = cur.fetchall()

            cur.execute("""
                SELECT row, col, result, player_id
                FROM moves
                WHERE game_id = %s
                ORDER BY move_id ASC
            """, (gid,))
            move_rows = cur.fetchall()

            return jsonify({
                "player_id": pid,
                "ships": [
                    {"row": row, "col": col, "hit": hit}
                    for row, col, hit in ship_rows
                ],
                "moves": [
                    {
                        "player_id": firing_player_id,
                        "row": row,
                        "col": col,
                        "result": result
                    }
                    for row, col, result, firing_player_id in move_rows
                ]
            }), 200
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
