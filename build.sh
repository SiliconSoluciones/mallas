#!/usr/bin/env bash
set -o errexit

# 1) Instala dependencias
pip install -r requirements.txt

# 2) Usa el CLI de Flask para inicializar la BD
export FLASK_APP=app.py
flask init_db
