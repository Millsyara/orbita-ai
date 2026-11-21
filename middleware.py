from fastapi import Request
from fastapi.responses import RedirectResponse
from database import async_db_manager
from sqlalchemy import select
from models import UserBase

excluded_paths = [
    "/dashboard",
    "/login",
    "/register",
    "/banned" 
]

async def user_middleware(request: Request, call_next):
    token = request.cookies.get("auth_token")
    user = None
    
    if token:
        try:
            async with async_db_manager.AsyncSessionLocal() as session:
                query = select(UserBase).where(UserBase.token == token)
                result = await session.execute(query)
                user = result.scalar_one_or_none()
                
                if user and user.role == "banned":
                    current_path = request.url.path
                    if not any(current_path.startswith(path) for path in excluded_paths):
                        response = RedirectResponse(url="/banned", status_code=302)
                        response.delete_cookie("auth_token")
                        return response
        except:
            user = None
    
    request.state.user = user
    response = await call_next(request)
    return response