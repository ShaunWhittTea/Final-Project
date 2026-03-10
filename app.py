import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

app = Flask(__name__)
CORS(app)

try:
    init_db()
except Exception as ex:
    print(f"DB init failed: {ex}")


def error_response(message, status=400):
    return jsonify({"error": message}), status


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/games")
def create_game():
    data = request.get_json(silent=True) or {}

    if "gameId" in data:
        return error_response("Client may not supply gameId.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO games (status)
                    VALUES (%s)
                    RETURNING game_id, status, created_at
                    """,
                    ("waiting",)
                )
                game = cur.fetchone()
                conn.commit()

        return jsonify({
            "gameId": str(game["game_id"]),
            "status": game["status"],
            "createdAt": game["created_at"].isoformat()
        }), 201

    except Exception as ex:
        print(f"Create game error: {ex}")
        return error_response("Failed to create game.", 500)


@app.post("/games/<game_id>/join")
def join_game(game_id):
    data = request.get_json(silent=True) or {}

    player_name = data.get("playerName")

    if not player_name or not isinstance(player_name, str) or not player_name.strip():
        return error_response("playerName is required.", 400)

    player_name = player_name.strip()

    if "playerId" in data:
        return error_response("Client may not supply playerId.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT game_id, status
                    FROM games
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cur.fetchone()

                if not game:
                    return error_response("Game not found.", 404)

                cur.execute(
                    """
                    SELECT player_id, display_name
                    FROM players
                    WHERE display_name = %s
                    """,
                    (player_name,)
                )
                player = cur.fetchone()

                if not player:
                    cur.execute(
                        """
                        INSERT INTO players (display_name)
                        VALUES (%s)
                        RETURNING player_id, display_name
                        """,
                        (player_name,)
                    )
                    player = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO game_players (game_id, player_id)
                    VALUES (%s, %s)
                    """,
                    (game_id, player["player_id"])
                )

                conn.commit()

        return jsonify({
            "gameId": str(game["game_id"]),
            "playerId": str(player["player_id"]),
            "playerName": player["display_name"],
            "status": game["status"]
        }), 201

    except UniqueViolation:
        return error_response("Player is already in this game.", 400)
    except Exception as ex:
        print(f"Join game error: {ex}")
        return error_response("Failed to join game.", 500)


@app.get("/players/<player_id>")
def get_player(player_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT player_id, display_name, created_at,
                           total_games, total_wins, total_losses, total_moves
                    FROM players
                    WHERE player_id = %s
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

    except Exception as ex:
        print(f"Get player error: {ex}")
        return error_response("Failed to fetch player.", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)