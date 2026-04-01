"""DO Sandbox Demo - Todo App with AI-Suggested Priority (Flask + HTMX)

Keyword heuristic suggests priority as user types. Not a real LLM—just pattern matching.
"""
from flask import Flask, request, render_template_string

app = Flask(__name__)

# --- Priority keyword heuristic ---

HIGH_KEYWORDS = [
    "fix", "bug", "urgent", "critical", "prod", "security",
    "crash", "down", "broken", "emergency", "asap",
]
MEDIUM_KEYWORDS = [
    "update", "review", "meeting", "deadline", "test",
    "deploy", "release", "refactor", "migrate",
]

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def suggest_priority(text):
    """Return suggested priority based on keyword heuristic."""
    lower = text.lower()
    for kw in HIGH_KEYWORDS:
        if kw in lower:
            return "high"
    for kw in MEDIUM_KEYWORDS:
        if kw in lower:
            return "medium"
    return "low"


def _sorted_todos():
    return sorted(todos, key=lambda t: PRIORITY_ORDER.get(t["priority"], 2))


# --- Seed data ---

todos = [
    {"id": 1, "text": "Buy groceries", "done": False, "priority": "low"},
    {"id": 2, "text": "Write proposal", "done": False, "priority": "medium"},
    {"id": 3, "text": "Call dentist", "done": False, "priority": "low"},
]
next_id = 4

# --- Priority colors ---

PRIORITY_DOT = {
    "high": "#FF4D6A",
    "medium": "#FFB224",
    "low": "#00D4AA",
}
PRIORITY_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}

# --- Templates ---

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DO Sandbox - Smart Priority Todos</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --navy: #0B1A2E;
    --navy-light: #122240;
    --navy-mid: #1A2D4A;
    --surface: #15253F;
    --border: #1E3455;
    --blue: #0069FF;
    --blue-glow: #0069FF44;
    --blue-soft: #0069FF22;
    --cyan: #00D4AA;
    --cyan-glow: #00D4AA33;
    --text: #E8EDF5;
    --text-dim: #7B8FAD;
    --text-muted: #4A5E7A;
    --danger: #FF4D6A;
    --warning: #FFB224;
    --success: #00D4AA;
    --radius: 14px;
  }

  body {
    font-family: 'Plus Jakarta Sans', sans-serif;
    background: var(--navy);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 80px 24px;
    overflow-x: hidden;
  }

  /* Ambient background glow */
  body::before {
    content: '';
    position: fixed;
    top: -40%; left: -20%;
    width: 80%; height: 80%;
    background: radial-gradient(ellipse at center, var(--blue-glow) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }
  body::after {
    content: '';
    position: fixed;
    bottom: -30%; right: -10%;
    width: 60%; height: 70%;
    background: radial-gradient(ellipse at center, var(--cyan-glow) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .container {
    width: 100%;
    max-width: 580px;
    position: relative;
    z-index: 1;
  }

  /* Header */
  .header {
    text-align: center;
    margin-bottom: 48px;
  }
  .header .badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--cyan);
    background: var(--cyan-glow);
    border: 1px solid #00D4AA44;
    padding: 6px 14px;
    border-radius: 100px;
    margin-bottom: 20px;
  }
  .badge::before {
    content: '';
    width: 6px; height: 6px;
    background: var(--cyan);
    border-radius: 50%;
    box-shadow: 0 0 8px var(--cyan);
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  h1 {
    font-size: 42px;
    font-weight: 700;
    letter-spacing: -1px;
    line-height: 1.1;
    background: linear-gradient(135deg, var(--text) 0%, var(--text-dim) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .subtitle {
    font-size: 14px;
    color: var(--text-muted);
    margin-top: 8px;
    font-weight: 500;
  }

  /* Add form */
  .add-form {
    margin-bottom: 32px;
  }
  .add-form .input-row {
    display: flex;
    gap: 10px;
  }
  .add-form input[type="text"] {
    flex: 1;
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 15px;
    font-weight: 500;
    padding: 16px 20px;
    background: var(--surface);
    color: var(--text);
    border: 1.5px solid var(--border);
    border-radius: var(--radius);
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
  }
  .add-form input[type="text"]::placeholder {
    color: var(--text-muted);
  }
  .add-form input[type="text"]:focus {
    border-color: var(--blue);
    box-shadow: 0 0 0 3px var(--blue-soft), 0 4px 24px rgba(0,0,0,0.2);
  }
  .add-form button {
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 14px;
    font-weight: 700;
    padding: 16px 28px;
    background: var(--blue);
    color: #fff;
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s, background 0.2s;
    white-space: nowrap;
  }
  .add-form button:hover {
    background: #0057DD;
    transform: translateY(-1px);
    box-shadow: 0 6px 24px var(--blue-glow);
  }
  .add-form button:active { transform: translateY(0); }

  /* Suggestion + override row */
  .suggest-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
    min-height: 36px;
  }

  #suggestion {
    flex: 1;
  }

  .suggestion-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 6px 14px;
    border-radius: 100px;
    animation: suggestFadeIn 0.3s ease-out both;
  }
  @keyframes suggestFadeIn {
    from { opacity: 0; transform: translateY(4px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  .suggestion-pill .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .suggestion-pill.high {
    color: var(--danger);
    background: #FF4D6A18;
    border: 1px solid #FF4D6A44;
  }
  .suggestion-pill.high .dot {
    background: var(--danger);
    box-shadow: 0 0 8px var(--danger);
  }
  .suggestion-pill.medium {
    color: var(--warning);
    background: #FFB22418;
    border: 1px solid #FFB22444;
  }
  .suggestion-pill.medium .dot {
    background: var(--warning);
    box-shadow: 0 0 8px var(--warning);
  }
  .suggestion-pill.low {
    color: var(--success);
    background: #00D4AA18;
    border: 1px solid #00D4AA44;
  }
  .suggestion-pill.low .dot {
    background: var(--success);
    box-shadow: 0 0 8px var(--success);
  }

  /* Priority override select */
  .priority-select {
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 12px;
    background: var(--surface);
    color: var(--text-dim);
    border: 1.5px solid var(--border);
    border-radius: 10px;
    outline: none;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .priority-select:focus {
    border-color: var(--blue);
  }

  /* Todo list */
  .todo-list { display: flex; flex-direction: column; gap: 8px; }

  .todo-item {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 16px 20px;
    background: var(--surface);
    border: 1.5px solid var(--border);
    border-radius: var(--radius);
    transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
    animation: slideIn 0.3s ease-out both;
  }
  .todo-item:hover {
    border-color: var(--navy-mid);
    transform: translateX(4px);
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
  }

  @keyframes slideIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* Priority dot in list */
  .priority-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  /* Custom checkbox */
  .check {
    width: 22px; height: 22px;
    border-radius: 7px;
    border: 2px solid var(--text-muted);
    cursor: pointer;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
  }
  .check:hover { border-color: var(--blue); }
  .check.checked {
    background: var(--blue);
    border-color: var(--blue);
  }
  .check.checked::after {
    content: '';
    width: 6px; height: 10px;
    border: solid #fff;
    border-width: 0 2.5px 2.5px 0;
    transform: rotate(45deg) translate(-1px, -1px);
  }

  .todo-text {
    flex: 1;
    font-size: 15px;
    font-weight: 500;
    line-height: 1.4;
    transition: color 0.2s;
  }
  .todo-text.done {
    color: var(--text-muted);
    text-decoration: line-through;
    text-decoration-color: var(--text-muted);
  }

  .priority-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 6px;
    flex-shrink: 0;
  }
  .priority-label.high {
    color: var(--danger);
    background: #FF4D6A18;
  }
  .priority-label.medium {
    color: var(--warning);
    background: #FFB22418;
  }
  .priority-label.low {
    color: var(--success);
    background: #00D4AA18;
  }

  .delete-btn {
    opacity: 0;
    font-size: 18px;
    color: var(--text-muted);
    background: none;
    border: none;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 8px;
    transition: opacity 0.15s, color 0.15s, background 0.15s;
  }
  .todo-item:hover .delete-btn { opacity: 1; }
  .delete-btn:hover {
    color: var(--danger);
    background: #FF4D6A18;
  }

  /* Empty state */
  .empty {
    text-align: center;
    padding: 48px 24px;
    color: var(--text-muted);
    font-size: 15px;
    font-style: italic;
  }

  /* Counter */
  .counter {
    text-align: center;
    margin-top: 24px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--text-muted);
    letter-spacing: 1px;
  }

  /* HTMX loading indicator */
  .htmx-request .todo-list { opacity: 0.6; transition: opacity 0.1s; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="badge">smart suggest</div>
    <h1>Priority Todos</h1>
    <p class="subtitle">AI-suggested priority as you type</p>
  </div>
  <form class="add-form" hx-post="/todos" hx-target="#todo-list" hx-swap="innerHTML"
        hx-on::after-request="this.querySelector('input[name=text]').value=''; document.getElementById('suggestion').innerHTML=''; this.querySelector('select[name=priority]').value='';">
    <div class="input-row">
      <input type="text" name="text" placeholder="What needs to be done?" autocomplete="off" required
             hx-get="/suggest" hx-trigger="keyup changed delay:300ms" hx-target="#suggestion">
      <button type="submit">Add</button>
    </div>
    <div class="suggest-row">
      <div id="suggestion"></div>
      <select name="priority" class="priority-select">
        <option value="">Auto priority</option>
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
    </div>
  </form>
  <div id="todo-list">{{ todo_html | safe }}</div>
  <div class="counter" id="counter" hx-swap-oob="true">{{ counter_text }}</div>
</div>
</body>
</html>"""

ITEM = """{% for t in todos %}<div class="todo-item" style="animation-delay:{{ loop.index0 * 0.05 }}s">
<div class="priority-dot" style="background:{{ dot_colors[t.priority] }};box-shadow:0 0 8px {{ dot_colors[t.priority] }}"></div>
<div class="check {{ 'checked' if t.done }}" hx-post="/todos/{{ t.id }}/toggle" hx-target="#todo-list" hx-swap="innerHTML"></div>
<span class="todo-text {{ 'done' if t.done }}">{{ t.text }}</span>
<span class="priority-label {{ t.priority }}">{{ t.priority }}</span>
<button class="delete-btn" hx-delete="/todos/{{ t.id }}" hx-target="#todo-list" hx-swap="innerHTML">&times;</button>
</div>{% endfor %}{% if not todos %}<div class="empty">No todos yet — add one above</div>{% endif %}
<div class="counter" id="counter" hx-swap-oob="true">{{ counter_text }}</div>"""


def _counter():
    done = sum(1 for t in todos if t["done"])
    return f"{done}/{len(todos)} completed" if todos else ""


def _render_items():
    return render_template_string(
        ITEM, todos=_sorted_todos(), counter_text=_counter(), dot_colors=PRIORITY_DOT
    )


@app.get("/")
def index():
    items_html = render_template_string(
        ITEM, todos=_sorted_todos(), counter_text=_counter(), dot_colors=PRIORITY_DOT
    )
    return render_template_string(PAGE, todo_html=items_html, counter_text=_counter())


@app.get("/suggest")
def suggest():
    text = request.args.get("text", "").strip()
    if not text:
        return ""
    priority = suggest_priority(text)
    label = PRIORITY_LABEL[priority]
    return (
        f'<span class="suggestion-pill {priority}">'
        f'<span class="dot"></span>Suggested: {label}</span>'
    )


@app.get("/todos")
def get_todos():
    return _render_items()


@app.post("/todos")
def add_todo():
    global next_id
    text = request.form.get("text", "").strip()
    if text:
        # Use manual override if provided, otherwise auto-suggest
        priority = request.form.get("priority", "").strip()
        if priority not in ("high", "medium", "low"):
            priority = suggest_priority(text)
        todos.append({"id": next_id, "text": text, "done": False, "priority": priority})
        next_id += 1
    return _render_items()


@app.post("/todos/<int:todo_id>/toggle")
def toggle(todo_id):
    for t in todos:
        if t["id"] == todo_id:
            t["done"] = not t["done"]
            break
    return _render_items()


@app.delete("/todos/<int:todo_id>")
def delete(todo_id):
    global todos
    todos = [t for t in todos if t["id"] != todo_id]
    return _render_items()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
