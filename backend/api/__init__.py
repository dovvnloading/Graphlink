"""ADR-002 stage 2.6: register_canvas's former ~1572-line body, split into
one register_*_intents(bus, document, ...) function per feature area.

Pure code motion - no behavior change. Each module here was relocated
VERBATIM from backend/canvas.py's own register_canvas (see each module's
own docstring for its exact former line range), with closures over
register_canvas's local scope turned into explicit function parameters.
backend/canvas.py's register_canvas is now a thin orchestrator that
constructs the shared SceneDocument, registers the handful of topics with
no natural feature home, then calls each register_*_intents function in
turn - see that function's own docstring.
"""
