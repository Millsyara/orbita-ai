from fastapi import FastAPI, Request, Depends, APIRouter, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, or_
import os

from schemas import UserRegister, UserLogin
from models import UserBase
from database import async_db_manager

current_directory = os.path.dirname(os.path.abspath(__file__))
static_directory = os.path.join(current_directory, '..', 'static')
templates_directory = os.path.join(current_directory, '..', 'templates')

router = APIRouter()
templates = Jinja2Templates(directory=templates_directory)

async def get_current_user(request: Request) -> UserBase:
    token = request.cookies.get("auth_token")
    if not token:
        return None # type: ignore
    
    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.token == token)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        return user # type: ignore

@router.get("/")
async def home_page(request: Request):
    user = await get_current_user(request)
    if not user: user = None

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user},
    )

@router.get("/tos")
async def home_page(request: Request):
    user = await get_current_user(request)
    if not user: user = None

    return templates.TemplateResponse(
        request=request,
        name="tos.html",
        context={"user": user},
    )