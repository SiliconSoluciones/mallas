# init_db.py
from app import db
from app.models import *

with db.engine.begin() as conn:
    db.metadata.create_all(bind=conn)
