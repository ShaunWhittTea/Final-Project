import os
import json
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

TEST_PASSWORD = os.getenv("TEST_PASSWORD")
BOARD_SIZE = 10

app = Flask(__name__)
CORS(app)

try:
    init_db()
except Exception as ex:
    print(f"DB init failed: {ex}")


def error_response(message, status=400):
    return jsonify({"error": message}), status


def require_test_mode():
    header = request.headers.get("X-Test-Mode")
    if not TEST_PASSWORD or header != TEST_PASSWORD:
        return error_response("Forbidden.", 403)
    return None


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/games")
def create_game():
    data = request.get_json(silent=True) or {}

    if "gameId" in data:
        return error_response("Client may not supply gameId.", 400)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO games (status)
                VALUES ('waiting')
                RETURNING game_id,status,created_at
                """
            )
            game = cur.fetchone()
            conn.commit()

    return jsonify({
        "gameId": str(game["game_id"]),
        "status": game["status"],
        "createdAt": game["created_at"].isoformat()
    }), 201


@app.post("/games/<game_id>/join")
def join_game(game_id):

    data = request.get_json(silent=True) or {}
    player_name = data.get("playerName")

    if not player_name:
        return error_response("playerName is required.", 400)

    if "playerId" in data:
        return error_response("Client may not supply playerId.", 400)

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT * FROM games WHERE game_id=%s", (game_id,))
            game = cur.fetchone()

            if not game:
                return error_response("Game not found.", 404)

            cur.execute(
                "SELECT player_id,display_name FROM players WHERE display_name=%s",
                (player_name,)
            )
            player = cur.fetchone()

            if not player:
                cur.execute(
                    """
                    INSERT INTO players (display_name)
                    VALUES (%s)
                    RETURNING player_id,display_name
                    """,
                    (player_name,)
                )
                player = cur.fetchone()

            try:
                cur.execute(
                    """
                    INSERT INTO game_players (game_id,player_id)
                    VALUES (%s,%s)
                    """,
                    (game_id, player["player_id"])
                )
            except UniqueViolation:
                return error_response("Player is already in this game.", 400)

            conn.commit()

    return jsonify({
        "gameId": str(game_id),
        "playerId": str(player["player_id"]),
        "playerName": player["display_name"],
        "status": game["status"]
    }), 201


@app.get("/players/<player_id>")
def get_player(player_id):

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT player_id,display_name,created_at,
                       total_games,total_wins,total_losses,total_moves
                FROM players
                WHERE player_id=%s
                """,
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return error_response("Player not found.", 404)

    return jsonify({
        "playerId": str(player["player_id"]),
        "displayName": player["display_name"],
        "createdAt": player["created_at"].isoformat(),
        "totalGames": player["total_games"],
        "totalWins": player["total_wins"],
        "totalLosses": player["total_losses"],
        "totalMoves": player["total_moves"]
    }), 200


@app.post("/test/games/<game_id>/ships")
def place_ships(game_id):

    check = require_test_mode()
    if check:
        return check

    data = request.get_json(silent=True) or {}
    player_id = data.get("playerId")
    ships = data.get("ships")

    if not player_id or not ships:
        return error_response("Invalid request.", 400)

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT status FROM games WHERE game_id=%s", (game_id,))
            game = cur.fetchone()

            if not game:
                return error_response("Game not found.", 404)

            if game["status"] != "waiting":
                return error_response("Ships can only be placed before game starts.", 400)

            cur.execute(
                """
                DELETE FROM ships
                WHERE game_id=%s AND player_id=%s
                """,
                (game_id, player_id)
            )

            for ship in ships:

                ship_type = ship["type"]
                coordinates = ship["coordinates"]

                cur.execute(
                    """
                    INSERT INTO ships (game_id,player_id,ship_type,coordinates)
                    VALUES (%s,%s,%s,%s::jsonb)
                    """,
                    (game_id, player_id, ship_type, json.dumps(coordinates))
                )

            conn.commit()

    return jsonify({
        "gameId": game_id,
        "playerId": player_id,
        "message": "Ships placed successfully"
    }), 200


@app.get("/test/games/<game_id>/board")
def reveal_board(game_id):

    check = require_test_mode()
    if check:
        return check

    player_id = request.args.get("playerId")

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT ship_type,coordinates
                FROM ships
                WHERE game_id=%s AND player_id=%s
                """,
                (game_id, player_id)
            )
            ships = cur.fetchall()

            cur.execute(
                """
                SELECT row_index,col_index,result
                FROM shots
                WHERE game_id=%s AND target_player_id=%s
                """,
                (game_id, player_id)
            )
            shots = cur.fetchall()

    return jsonify({
        "gameId": game_id,
        "playerId": player_id,
        "ships": ships,
        "shots": shots
    }), 200


@app.post("/test/games/<game_id>/reset")
def reset_game(game_id):

    check = require_test_mode()
    if check:
        return check

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("DELETE FROM ships WHERE game_id=%s", (game_id,))
            cur.execute("DELETE FROM shots WHERE game_id=%s", (game_id,))

            cur.execute(
                """
                UPDATE games
                SET status='waiting',
                    current_turn_player_id=NULL
                WHERE game_id=%s
                """,
                (game_id,)
            )

            conn.commit()

    return jsonify({
        "gameId": game_id,
        "status": "waiting"
    }), 200


@app.post("/test/games/<game_id>/set-turn")
def set_turn(game_id):

    check = require_test_mode()
    if check:
        return check

    data = request.get_json(silent=True) or {}
    player_id = data.get("playerId")

    if not player_id:
        return error_response("playerId required.", 400)

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE games
                SET current_turn_player_id=%s
                WHERE game_id=%s
                """,
                (player_id, game_id)
            )

            conn.commit()

    return jsonify({
        "gameId": game_id,
        "currentTurnPlayerId": player_id
    }), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
