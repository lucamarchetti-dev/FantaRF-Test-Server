"""Punto d'ingresso WSGI per gunicorn:  gunicorn FantaRF.deploy.wsgi:app"""
from .. import create_app

app = create_app()
