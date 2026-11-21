from fastapi import FastAPI, Request, Depends, APIRouter, Form, status, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select, or_
from datetime import datetime, timedelta
from passlib.context import CryptContext # type: ignore
import os
import secrets
import hashlib
from decimal import Decimal

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

@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={},
    )

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={},
    )

async def email_check(email: str) -> bool:
    async with async_db_manager.AsyncSessionLocal() as session:
        try:
            query = select(UserBase).where(UserBase.email == email)
            result = await session.execute(query)
            user = result.scalar_one_or_none()
            return user is not None
        except:
            return False
        
async def username_check(username: str) -> bool:
    async with async_db_manager.AsyncSessionLocal() as session:
        try:
            query = select(UserBase).where(
                UserBase.username.ilike(username) 
            )
            result = await session.execute(query)
            user = result.scalar_one_or_none()
            return user is not None
        except:
            return False

        
def hash_password(password: str) -> str:
    return str(pwd_context.hash(password))

async def create_user(user_data: UserRegister, background_tasks: BackgroundTasks) -> UserBase:
    async with async_db_manager.AsyncSessionLocal() as session:
        USE_SMTP = os.environ.get("USE_SMTP")
        try:
            hashed_password = hash_password(user_data.password)
            verification_token = secrets.token_urlsafe(32)
            verification_expires = datetime.now() + timedelta(hours=24)
            password_reset_token = secrets.token_urlsafe(32)
            password_reset_token_expires = datetime.now() + timedelta(hours=24)

            new_user = UserBase(
            username=user_data.username,
            email=user_data.email,
            password=hashed_password,
            token=secrets.token_urlsafe(32),
            created_at=datetime.now(),
            verification_token=verification_token,
            verification_token_expires=verification_expires,
            password_reset_token=password_reset_token,
            password_reset_token_expires=password_reset_token_expires,
            status="unverified" if USE_SMTP=="True" else "verified"  
        )
                
            session.add(new_user)

            await session.commit()

            await session.refresh(new_user)

            if USE_SMTP == "True":
                email_service.send_verification_email_background(background_tasks, user_data.email, verification_token, user_data.username) 

            return new_user

        except Exception as e:
            await session.rollback()
            raise e

async def get_current_user(request: Request) -> UserBase:
    token = request.cookies.get("auth_token")
    if not token:
        return None # type: ignore
    
    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(UserBase.token == token)
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        return user # type: ignore

@router.post("/register", response_class=HTMLResponse)
async def register_user(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    agree_terms: bool = Form(False)
):
    
    form_data = {
        "username": username,
        "email": email
    }
    
    try:
        user_data = UserRegister(
            username=username,
            email=email,
            password=password,
            confirm_password=confirm_password,
            agree_terms=agree_terms
        )
        
        username_exists = await username_check(username)
        email_exists = await email_check(email)

        if username_exists or email_exists:
            if username_exists and email_exists:
                error_message = 'Пользователь с такими именем пользователя и email уже существует'

            elif username_exists:
                error_message = 'Пользователь с таким именем уже существует'

            elif email_exists:
                error_message = 'Пользователь с таким email уже существует'

            return templates.TemplateResponse(
                request=request,
                name="register.html", 
                context={
                    "error": error_message if error_message else "Неизвестная ошибка", # type: ignore
                    "form_data": form_data
                },
                status_code=422
            ) 
        
        new_user = await create_user(user_data, background_tasks) 

        response = RedirectResponse(url=f"/", status_code=status.HTTP_303_SEE_OTHER)
                        
        response.set_cookie(
            key="auth_token", 
            value=new_user.token, 
            httponly=True,
            # secure=True, TODO включить в проде
            samesite="lax"
        )

        return response
        
    except ValidationError as e:
        error_messages = []
        
        for error in e.errors():
            field = error["loc"][0]
            msg = error["msg"]
            
            if field == "confirm_password" and "Пароли не совпадают" in msg:
                error_messages.append("Пароли не совпадают")
            elif field == "username":
                if "ensure this value has at least 3 characters" in msg:
                    error_messages.append("Имя пользователя должно содержать минимум 3 символа")
                elif "ensure this value has at most 25 characters" in msg:
                    error_messages.append("Имя пользователя должно содержать максимум 25 символов")
                else:
                    error_messages.append(f"Ошибка в имени пользователя: {msg}")
            elif field == "email":
                error_messages.append("Некорректный email адрес")
            elif field == "password":
                error_messages.append("Пароль должен содержать минимум 6 символов")
            elif field == "agree_terms":
                error_messages.append("Необходимо согласие с условиями использования")
            else:
                error_messages.append(msg)
        
        unique_errors = list(dict.fromkeys(error_messages))
        error_message = "; ".join(unique_errors)
        
        return templates.TemplateResponse(
            request=request,
            name="register.html", 
            context={
                "error": error_message, # type: ignore
                "form_data": form_data
            },
            status_code=422
        ) 
    
    except Exception as e:
        return templates.TemplateResponse(
            request=request,
            name="register.html", 
            context={
                "error": e, # type: ignore
                "form_data": form_data
            },
            status_code=422
        ) 
    
@router.post("/login", response_class=HTMLResponse)
async def login_user(
    request: Request,
    username_or_email: str = Form(...),
    password: str = Form(...),
):
    
    form_data = {
        "username_or_email": username_or_email,
    }
    
    try:
        user_data = UserLogin(
            username_or_email=username_or_email,
            password=password,
        )

        async with async_db_manager.AsyncSessionLocal() as session:
            query = select(UserBase).where(
                or_(UserBase.username == username_or_email,
                UserBase.email == username_or_email)
            )

            result = await session.execute(query)
            user = result.scalar_one_or_none()

            if not user:
                error_message = "Неверное имя пользователя/email или пароль"
                return templates.TemplateResponse(
                    request=request,
                    name="login.html", 
                    context={
                        "error": error_message, # type: ignore
                        "form_data": form_data
                    },
                    status_code=422
                ) 
            
            if not pwd_context.verify(password, user.password):
                error_message = "Неверное имя пользователя/email или пароль"
                return templates.TemplateResponse(
                    request=request,
                    name="login.html", 
                    context={
                        "error": error_message, # type: ignore
                        "form_data": form_data
                    },
                    status_code=422
                ) 
            
            if user.status != 'verified':
                error_message = 'email_verify'
                return templates.TemplateResponse(
                    request=request,
                    name="login.html", 
                    context={
                        "error": error_message, # type: ignore
                        "form_data": form_data
                    },
                    status_code=422
                ) 

            new_token = secrets.token_urlsafe(32)
            user.token = new_token  
            await session.commit()  

            username = user.username
            response = RedirectResponse(url=f"/", status_code=status.HTTP_303_SEE_OTHER)
                
            response.set_cookie(
                key="auth_token", 
                value=user.token, 
                httponly=True,
                # secure=True, TODO включить в проде
                samesite="lax"
            )

            return response

    except ValidationError as e:
        error_messages = []
        
        for error in e.errors():
            field = error["loc"][0]
            msg = error["msg"]
            
            if field == "password":
                error_messages.append("Пароль должен содержать минимум 6 символов")
            else:
                error_messages.append(msg)

        unique_errors = list(dict.fromkeys(error_messages))
        error_message = "; ".join(unique_errors)
        
        return templates.TemplateResponse(
            request=request,
            name="login.html", 
            context={
                "error": error_message, # type: ignore
                "form_data": form_data
            },
            status_code=422
        ) 
    
    except Exception as e:
        return templates.TemplateResponse(
            request=request,
            name="login.html", 
            context={
                "error": error_message, # type: ignore
                "form_data": form_data
            },
            status_code=422
        ) 
    
@router.post("/logout")
async def logout_user():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="auth_token")
    return response

@router.get("/logout", response_class=HTMLResponse)
async def logout_page(request: Request):
    response = templates.TemplateResponse(request=request, name="logout.html", context={})
    response.delete_cookie(key="auth_token")
    return response

@router.get("/verify-email")
async def verify_email(request: Request, token: str):
    async with async_db_manager.AsyncSessionLocal() as session:
        try:
            query = select(UserBase).where(UserBase.verification_token == token,
                                           UserBase.verification_token_expires > datetime.now())
            result = await session.execute(query)
            user = result.scalar_one_or_none()

            if not user:
                return templates.TemplateResponse(
                    request=request,
                    name="verification_failed.html",
                    context={
                        "error": "Токен подтверждения недействителен или истек",
                        "email": ""
                    }
                )
            
            user.status = "verified"
            user.verification_token = None  
            user.verification_token_expires = None
            
            await session.commit()
            
            return templates.TemplateResponse(
                request=request,
                name="verification_success.html", 
                context={
                    "username": user.username
                }
            )
            
        except Exception as e:
            await session.rollback()
            return templates.TemplateResponse(
                request=request,
                name="verification_failed.html",
                context={
                    "error": f"Произошла ошибка: {str(e)}",
                    "email": ""
                }
            )
        
async def resend_verification(request: Request, email: str, background_tasks: BackgroundTasks):
    async with async_db_manager.AsyncSessionLocal() as session:
        query = select(UserBase).where(
            UserBase.email == email,
            UserBase.status == 'unverified'
        )
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            return templates.TemplateResponse(
                request=request,
                name="verification_failed.html",
                context={
                    "error": "Пользователь не найден или уже подтвержден"
                }
            )
        
        new_token = secrets.token_urlsafe(32)
        user.verification_token = new_token
        user.verification_token_expires = datetime.now() + timedelta(hours=24)
        
        await session.commit()
        
        email_service.send_verification_email_background(
            background_tasks,
            user.email,
            new_token,
            user.username
        )
        
        return templates.TemplateResponse(
            request=request,
            name="register_success.html",
            context={
                "email": user.email
            }
        )

@router.get("/register-success", response_class=HTMLResponse)
async def register_success(request: Request, email: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="register_success.html",
        context={"email": email}
    )

