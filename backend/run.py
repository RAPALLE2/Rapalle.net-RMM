"""backend/run.py - Kompatibilitaets-Starter (sehr alte Installationen starteten
run.py). Macht genau dasselbe wie backend/main.py - siehe dort."""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"),
               run_name="__main__")
