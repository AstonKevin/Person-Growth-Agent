from fastapi import FastAPI

from .core.exceptions import register_exception_handlers
from .routers import auth, checkins, plans, users

app = FastAPI(title="Personal-Growth-Agent")

register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(plans.router)
app.include_router(checkins.router)
app.include_router(users.router)


@app.get("/")
def root() -> dict:
    return {"message": "Personal-Growth-Agent API running"}
