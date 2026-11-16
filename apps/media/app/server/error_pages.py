"""Unexpected server errors never show their text to a user.

The framework's catch-all handler answers with ``message=str(exc)``, which
can carry internal detail. This handler keeps the framework's logging and
status choice (500, or 503 for a Redis outage), then replaces the body:

- a browser (``Accept`` lists ``text/html``) gets a plain HTML page;
- any other client gets the usual JSON envelope with a fixed message.

Error tracking is unaffected: Starlette's server error middleware re-raises
the exception after this handler answers, so Sentry/GlitchTip still gets it.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi_mongo_base.core.exceptions import general_exception_handler

SUPPORT_URL = "mailto:mahdikiany@gmail.com"

PLAIN_ERROR_HTML = f"""<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Something went wrong</title>
<style>
body {{ margin: 0; font-family: system-ui, sans-serif; color: #1c252e; }}
main {{ min-height: 100vh; display: flex; align-items: center;
  justify-content: center; padding: 24px; box-sizing: border-box;
  text-align: center; }}
div {{ max-width: 420px; }}
h1 {{ font-size: 22px; margin: 0 0 12px; }}
p {{ font-size: 15px; line-height: 1.7; color: #637381; margin: 0 0 24px; }}
nav {{ display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }}
button, a {{ padding: 10px 20px; border-radius: 8px; font: inherit;
  font-size: 15px; color: #1c252e; text-decoration: none;
  border: 1px solid #c4cdd5; background: #fff; cursor: pointer; }}
button {{ background: #1c252e; color: #fff; border-color: #1c252e; }}
</style>
</head>
<body>
<main>
<div>
<h1>Something went wrong.</h1>
<p>Reload the page. If it keeps happening, go to the home page
or contact support.</p>
<nav>
<button type="button" onclick="location.reload()">Reload</button>
<a href="/">Home</a>
<a href="{SUPPORT_URL}">Support</a>
</nav>
</div>
</main>
</body>
</html>
"""


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def safe_exception_handler(request: Request, exc: Exception) -> Response:
    """Catch-all handler: framework logging and status, no error text."""
    framework = general_exception_handler(request, exc)
    status = framework.status_code
    if status < 500:
        # A known error mapped to a client status (e.g. a database
        # duplicate key): its message is written for the client.
        return framework
    if _wants_html(request):
        return HTMLResponse(PLAIN_ERROR_HTML, status_code=status)
    message = (
        "Service temporarily unavailable" if status == 503 else "Internal server error"
    )
    return JSONResponse(
        status_code=status,
        content={"message": message, "error": "Exception"},
    )


def install_error_pages(app: FastAPI) -> None:
    app.add_exception_handler(Exception, safe_exception_handler)
