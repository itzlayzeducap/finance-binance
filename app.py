from flask import Flask

from helpers import format_usd

app = Flask(__name__)
app.jinja_env.filters["usd"] = format_usd

from routes.auth import bp as auth_bp
from routes.finance import bp as finance_bp

app.register_blueprint(auth_bp)
app.register_blueprint(finance_bp)

if __name__ == "__main__":
    app.run()
