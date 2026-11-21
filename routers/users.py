from fastapi import FastAPI, Request, Depends, APIRouter, status, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, or_
import os
import secrets
from datetime import datetime, timedelta
from passlib.context import CryptContext

from schemas import UserRegister, UserLogin
from models import UserBase
from database import async_db_manager
from email_service import email_service

current_directory = os.path.dirname(os.path.abspath(__file__))
static_directory = os.path.join(current_directory, '..', 'static')
templates_directory = os.path.join(current_directory, '..', 'templates')

router = APIRouter()
templates = Jinja2Templates(directory=templates_directory)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

async def get_current_user(request: Request):
    token = request.cookies.get("auth_token")
    if not token: return None 
    
    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.token == token)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        return user

def hash_password(password: str) -> str:
    return str(pwd_context.hash(password))

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    
    user = await get_current_user(request)

    if not user:
        return RedirectResponse(url='/login', status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "username": user.username,
        "email": user.email,
        "joined_date": user.created_at.strftime("%d.%m.%Y"),
        }
        )

@router.get("/password-reset-page", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="password_reset.html", context={})

async def password_reset_send(request: Request, background_tasks: BackgroundTasks, email: str = Form(...)):

    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.email == email)

        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            error_message = "user_not_found"
            return templates.TemplateResponse(
                request=request,
                name="password_reset.html", 
                context={
                    "error": error_message,
                    "email": email
                },
                status_code=status.HTTP_404_NOT_FOUND
            )
        
        new_token = secrets.token_urlsafe(32)
        user.password_reset_token = new_token
        user.password_reset_token_expires = datetime.now() + timedelta(hours=24)
        await session.commit()
        
        email_service.send_password_reset_email_background(
            background_tasks,
            user.email,
            new_token,
            user.username
        )

        error_message = "success"
        return templates.TemplateResponse(
            request=request,
            name="password_reset.html",
            context={
                "error": error_message,
                "email": email
            }
        )
    
async def password_reset_resend(request: Request, email: str, background_tasks: BackgroundTasks):

    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.email == email)

        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            error_message = "user_not_found"
            return templates.TemplateResponse(
                request=request,
                name="password_reset.html", 
                context={
                    "error": error_message,
                    "email": email
                },
                status_code=status.HTTP_404_NOT_FOUND
            )
        
        new_token = secrets.token_urlsafe(32)
        user.password_reset_token = new_token
        user.password_reset_token_expires = datetime.now() + timedelta(hours=24)
        
        await session.commit()
        
        email_service.send_password_reset_email_background(
            background_tasks,
            user.email,
            new_token,
            user.username
        )

        error_message = "success"
        return templates.TemplateResponse(
            request=request,
            name="password_reset.html",
            context={
                "error": error_message,
                "email": email
            }
        )
    
@router.post("/password-reset")
async def password_reset(request: Request, token: str, password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        redirect_url = f"/password-reset-form?error=passwords_unmatch"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    
    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.password_reset_token == token,
                                        UserBase.password_reset_token_expires > datetime.now())
        result = await session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            return templates.TemplateResponse(
                request=request,
                name="password_reset.html",
                context={
                    "error": "token_expired",
                    "email": ""
                }
            )
        
        new_password = hash_password(password)

        user.password = new_password  
        user.password_reset_token = None
        user.password_reset_token_expires = None
    
        await session.commit()

        redirect_url = f"/password-reset-success"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    
@router.get("/password-reset-form", response_class=HTMLResponse)
async def reset_password_form(request: Request, token: str, error: str = None):
    return templates.TemplateResponse(request=request, name="password_reset_form.html", context={"error": error, "token": token})

@router.get("/password-reset-success", response_class=HTMLResponse)
async def password_reset_success(request: Request):
    return templates.TemplateResponse(requeste=request, name="password_reset_success.html", context={})
