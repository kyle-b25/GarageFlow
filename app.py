from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')

db = SQLAlchemy(app)

import models  # noqa: E402, F401

@app.route('/operator-front')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return {'status': 'ok'}

if __name__ == '__main__':
    app.run()