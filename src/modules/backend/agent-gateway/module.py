# module.py (Modul "agent-gateway") - der eigentliche Teil laeuft auf den Nodes
# (src/modules/node/agent-gateway). Im Backend ist nichts zu tun: die weitergereichten
# Agent-Verbindungen kommen wie jede andere an und werden ganz normal verarbeitet.
def on_startup(ctx):
    ctx.log("Agent-Gateway bereit (Node-Modul je Node aktivieren)")
