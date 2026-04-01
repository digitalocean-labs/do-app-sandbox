"""DO Sandbox Demo - Todo App with Priority Color Badges (Flask + HTMX)"""
from flask import Flask, request, render_template_string

app = Flask(__name__)

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

todos = [
    {"id": 1, "text": "Buy groceries", "done": False, "priority": "low"},
    {"id": 2, "text": "Write proposal", "done": False, "priority": "medium"},
    {"id": 3, "text": "Call dentist", "done": False, "priority": "high"},
]
next_id = 4

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DO Sandbox - Priority Todos</title>
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
    --priority-high: #FF4D6A;
    --priority-medium: #FFB84D;
    --priority-low: #00D4AA;
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

  /* Add form */
  .add-form {
    display: flex;
    gap: 10px;
    margin-bottom: 32px;
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
  .add-form select {
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 14px;
    font-weight: 500;
    padding: 16px 14px;
    background: var(--surface);
    color: var(--text);
    border: 1.5px solid var(--border);
    border-radius: var(--radius);
    outline: none;
    cursor: pointer;
    transition: border-color 0.2s, box-shadow 0.2s;
    -webkit-appearance: none;
    -moz-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%237B8FAD' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 32px;
  }
  .add-form select:focus {
    border-color: var(--blue);
    box-shadow: 0 0 0 3px var(--blue-soft), 0 4px 24px rgba(0,0,0,0.2);
  }
  .add-form select option {
    background: var(--surface);
    color: var(--text);
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

  /* Priority dot */
  .priority-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .priority-dot.high {
    background: var(--priority-high);
    box-shadow: 0 0 8px var(--priority-high);
  }
  .priority-dot.medium {
    background: var(--priority-medium);
    box-shadow: 0 0 8px var(--priority-medium);
  }
  .priority-dot.low {
    background: var(--priority-low);
    box-shadow: 0 0 8px var(--priority-low);
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
    <div class="badge">sandbox demo</div>
    <h1>Priority Todos</h1>
  </div>
  <form class="add-form" hx-post="/todos" hx-target="#todo-list" hx-swap="innerHTML"
        hx-on::after-request="this.querySelector('input').value=''">
    <input type="text" name="text" placeholder="What needs to be done?" autocomplete="off" required>
    <select name="priority">
      <option value="high">High</option>
      <option value="medium" selected>Medium</option>
      <option value="low">Low</option>
    </select>
    <button type="submit">Add</button>
  </form>
  <div id="todo-list">{{ todo_html | safe }}</div>
  <div class="counter" id="counter" hx-swap-oob="true">{{ counter_text }}</div>
</div>
</body>
</html>"""

ITEM = """{% for t in todos %}<div class="todo-item" style="animation-delay:{{ loop.index0 * 0.05 }}s">
<span class="priority-dot {{ t.priority }}"></span>
<div class="check {{ 'checked' if t.done }}" hx-post="/todos/{{ t.id }}/toggle" hx-target="#todo-list" hx-swap="innerHTML"></div>
<span class="todo-text {{ 'done' if t.done }}">{{ t.text }}</span>
<button class="delete-btn" hx-delete="/todos/{{ t.id }}" hx-target="#todo-list" hx-swap="innerHTML">&times;</button>
</div>{% endfor %}{% if not todos %}<div class="empty">No todos yet — add one above</div>{% endif %}
<div class="counter" id="counter" hx-swap-oob="true">{{ counter_text }}</div>"""


def _sorted_todos():
    return sorted(todos, key=lambda t: PRIORITY_ORDER.get(t["priority"], 1))


def _counter():
    done = sum(1 for t in todos if t["done"])
    return f"{done}/{len(todos)} completed" if todos else ""


def _render_items():
    return render_template_string(ITEM, todos=_sorted_todos(), counter_text=_counter())


@app.get("/")
def index():
    items_html = render_template_string(ITEM, todos=_sorted_todos(), counter_text=_counter())
    return render_template_string(PAGE, todo_html=items_html, counter_text=_counter())


@app.get("/todos")
def get_todos():
    return _render_items()


@app.post("/todos")
def add_todo():
    global next_id
    text = request.form.get("text", "").strip()
    priority = request.form.get("priority", "medium")
    if priority not in PRIORITY_ORDER:
        priority = "medium"
    if text:
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
