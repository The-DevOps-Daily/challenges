"""A tiny slug service: turns any text into a URL-friendly slug."""

from flask import Flask, jsonify, request
from slugify import slugify

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/slugify")
def make_slug():
    text = request.args.get("text", "")
    if not text:
        return jsonify(error="pass the text to turn into a slug as ?text="), 400
    return jsonify(text=text, slug=slugify(text))
