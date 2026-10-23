import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def health_check():
    return jsonify({
        "status": "online",
        "service": "ASH AUTOMATIC MOVIE FILE STORE BOT",
        "bot": "Ash_best_movie_bot",
        "branding": "ASH MOVIES",
        "version": "2.0.0"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
