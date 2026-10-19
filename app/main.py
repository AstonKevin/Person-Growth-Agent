from fastapi import FastAPI

from .core.exceptions import register_exception_handlers
from .routers import admin, auth, checkins, plans, providers, users

app = FastAPI(title="Personal-Growth-Agent")

register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(plans.router)
app.include_router(checkins.router)
app.include_router(providers.router)
app.include_router(users.router)
app.include_router(admin.router)


@app.get("/")
def root() -> dict:
    return {"message": "Personal-Growth-Agent API running"}
