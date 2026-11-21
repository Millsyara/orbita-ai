from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from fastapi.responses import FileResponse, JSONResponse

from models import UserBase, Base, MessageBase
from database import async_db_manager
from middleware import user_middleware

from routers import auth, pages, agent, users

from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

current_directory = os.path.dirname(os.path.abspath(__file__))
static_directory = os.path.join(current_directory, 'static')
templates_directory = os.path.join(current_directory, 'templates')

templates = Jinja2Templates(directory=templates_directory)

app = FastAPI(title="Orbita AI")

class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto == "https":
            request.scope["scheme"] = "https"
        response = await call_next(request)
        return response

app.add_middleware(ProxyHeadersMiddleware)

# app.add_middleware(
#     TrustedHostMiddleware,
#     allowed_hosts=["test.ru", "www.domen.ru", "localhost", "127.0.0.1"]
# )

app.middleware("http")(user_middleware)
app.mount("/static", StaticFiles(directory=static_directory), name="static")

@app.on_event('startup')
async def startup_event():
    await async_db_manager.create_tables()

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(users.router)
app.include_router(agent.router)
